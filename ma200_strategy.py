"""กลยุทธ์ MA200 + Market Profile — คำนวณจาก D1 / H4 / H1 เท่านั้น ส่วน M15 ใช้เป็นจุดเข้า

D1 + H4 : กำหนดทิศ (ราคาเทียบ SMA200 และ SMA200 ลาดไปทางเดียวกัน ต้องตรงกันทั้งสองกรอบ)
H1      : สร้าง "ช่องเข้า" = SMA200(H1) + Market Profile (POC/VAH/VAL ของ 3 วันล่าสุด คำนวณจากแท่ง H1)
          และใช้ ATR(H1) วัดระยะทั้งหมด (ความกว้างช่อง, SL, ระยะไล่ราคา)
M15     : ใช้แค่ 2 อย่าง = (1) ดูว่าราคาย่อ/เด้งมาแตะช่องแล้วหรือยัง (2) แท่งยืนยันการเข้า
          ไม่ใช้ M15 คำนวณเส้นหรือโปรไฟล์
"""
import numpy as np
import pandas as pd

SMA_N = 200
SLOPE_BARS = 10                    # ดูความลาด SMA200 ย้อนหลังกี่แท่ง (ของ D1 และ H4)
PROFILE_DAYS = 3                   # วันที่แสดง (ตรงกับ Market Profile ที่ตั้งไว้)
PROFILE_MODE = 'DAY'               # 'DAY' = รายวัน | 'SESSION' = แยก Asia/EU/US
SESSION_START = {'ASIA': 0, 'EU': 8, 'US': 14}   # ชั่วโมงเริ่มเซสชัน (เวลา UTC)
VALUE_AREA = 0.70
BINS_PER_ATR = 6
MIN_GROUP_BARS = 4                 # จำนวนแท่ง H1 ขั้นต่ำต่อโปรไฟล์
ZONE_TOL_ATR = 0.20                # ความกว้างช่อง (หน่วย ATR ของ H1)
PIERCE_ATR = 0.5                   # ราคาทะลุช่องได้ไม่เกินเท่านี้ (ATR ของ H1)
TOUCH_BARS = 4                     # M15 ย้อนหลังกี่แท่งที่นับว่า "แตะช่อง"
MIN_CONFLUENCE = 2                 # ต้องมีระดับรวมกันอย่างน้อยกี่เส้น (นับ SMA200 ด้วย)
ALLOW_SMA200_ALONE = True          # แตะ SMA200(H1) เส้นเดียวก็เข้าได้
MAX_CHASE_ATR = 0.6                # ปิดห่างช่องเกินเท่านี้ (ATR ของ H1) = ไล่ราคา
SL_BUFFER_ATR = 0.15
MIN_RISK_ATR, MAX_RISK_ATR = 0.3, 1.8
TP1_R = 1.0
TP2_MIN_R, TP2_MAX_R, TP2_FALLBACK_R = 1.5, 3.5, 2.0


def prep(d):
    d = d.copy()
    c, h, l = d.close, d.high, d.low
    d['SMA'] = c.rolling(SMA_N).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d['ATR'] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    d['range'] = h - l
    d['body_ratio'] = (c - d.open).abs() / d['range'].replace(0, np.nan)
    d['upper_wick'] = h - d[['open', 'close']].max(axis=1)
    d['lower_wick'] = d[['open', 'close']].min(axis=1) - l
    return d


# ---------------- ทิศทาง: D1 + H4 ----------------
def trend_dir(d):
    if len(d) < SMA_N + SLOPE_BARS + 1:
        return 'NA', 'ข้อมูลไม่พอ'
    x = d.iloc[-1]
    s_now, s_prev = float(x.SMA), float(d.SMA.iloc[-1 - SLOPE_BARS])
    if np.isnan(s_now) or np.isnan(s_prev):
        return 'NA', 'ข้อมูลไม่พอ'
    c = float(x.close)
    note = f'ราคา {c:,.2f} / SMA200 {s_now:,.2f}'
    if c > s_now and s_now > s_prev:
        return 'UP', note
    if c < s_now and s_now < s_prev:
        return 'DOWN', note
    return 'FLAT', note


def direction(d1, h4):
    a, na = trend_dir(d1)
    b, nb = trend_dir(h4)
    info = {'d1': a, 'h4': b, 'd1_note': na, 'h4_note': nb}
    if a == 'UP' and b == 'UP':
        return 'LONG', info
    if a == 'DOWN' and b == 'DOWN':
        return 'SHORT', info
    return None, info


# ---------------- Market Profile (TPO จากแท่ง H1) ----------------
def tpo_levels(df, step):
    lo = float(df.low.min())
    hi = float(df.high.max())
    n = int(np.ceil((hi - lo) / step)) + 1
    cnt = np.zeros(n)
    a = np.floor((df.low.values - lo) / step).astype(int)
    b = np.floor((df.high.values - lo) / step).astype(int)
    for i, j in zip(a, b):
        cnt[i:j + 1] += 1
    mx = cnt.max()
    idxs = np.flatnonzero(cnt == mx)
    poc = int(idxs[np.argmin(np.abs(idxs - n / 2))])
    target = VALUE_AREA * cnt.sum()
    lo_i = hi_i = poc
    acc = cnt[poc]
    while acc < target:
        up = cnt[hi_i + 1] if hi_i + 1 < n else -1
        dn = cnt[lo_i - 1] if lo_i > 0 else -1
        if up < 0 and dn < 0:
            break
        if up >= dn:
            hi_i += 1
            acc += up
        else:
            lo_i -= 1
            acc += dn
    return {'POC': lo + (poc + 0.5) * step, 'VAH': lo + (hi_i + 1) * step, 'VAL': lo + lo_i * step}


def _session_of(hour):
    for name, start in sorted(SESSION_START.items(), key=lambda kv: -kv[1]):
        if hour >= start:
            return name
    return 'ASIA'


def profile_levels(h1):
    atr = max(float(h1.ATR.iloc[-1]), 1e-9)
    step = atr / BINS_PER_ATR
    tail = h1.iloc[-(PROFILE_DAYS + 1) * 30:]
    dates = np.array(tail.index.date)
    days = sorted(set(dates))[-PROFILE_DAYS:]
    day_names = ['วันนี้', 'เมื่อวาน', '2 วันก่อน', '3 วันก่อน']
    groups = []
    for k, day in enumerate(days):
        label = day_names[len(days) - 1 - k]
        sub = tail[dates == day]
        if PROFILE_MODE == 'DAY':
            groups.append((label, sub))
        else:
            sess = np.array([_session_of(h) for h in sub.index.hour])
            for name in SESSION_START:
                groups.append((f'{label} {name}', sub[sess == name]))
    levels = []
    for label, sub in groups:
        if len(sub) < MIN_GROUP_BARS:
            continue
        lv = tpo_levels(sub, step)
        for kind in ('POC', 'VAH', 'VAL'):
            levels.append({'name': f'{kind} {label}', 'price': float(lv[kind]), 'kind': kind})
    return levels


# ---------------- จุดเข้า: M15 ----------------
def confirm_candle(x, long):
    """แท่ง M15 ยืนยัน (สัดส่วนตัวแท่ง/ไส้เทียน ไม่ใช้ ATR ของ M15)"""
    rng = max(float(x.range), 1e-9)
    c = float(x.close)
    if long:
        body = c > float(x.open) and float(x.body_ratio) >= 0.35
        rej = float(x.lower_wick) >= 0.4 * rng and c >= float(x.low) + 0.6 * rng
    else:
        body = c < float(x.open) and float(x.body_ratio) >= 0.35
        rej = float(x.upper_wick) >= 0.4 * rng and c <= float(x.high) - 0.6 * rng
    return bool(body or rej)


def entry_setup(h1, m15, direction, levels):
    if len(h1) < SMA_N + 2 or len(m15) < TOUCH_BARS:
        return None, 'ข้อมูลไม่พอ'
    x1 = h1.iloc[-1]
    atr, sma = max(float(x1.ATR), 1e-9), float(x1.SMA)
    if np.isnan(sma):
        return None, 'ข้อมูล H1 ไม่พอสำหรับ SMA200'
    xm = m15.iloc[-1]
    c = float(xm.close)
    long = direction == 'LONG'
    if (long and c < sma) or ((not long) and c > sma):
        return None, 'ราคาอยู่ผิดฝั่งของ SMA200(H1)'
    tol = ZONE_TOL_ATR * atr
    pts = [(lv['name'], lv['price']) for lv in levels] + [('SMA200 H1', sma)]
    recent = m15.iloc[-TOUCH_BARS:]
    ext_low, ext_high = float(recent.low.min()), float(recent.high.max())

    best = None
    for name, p in pts:
        if long:
            if p < sma - 0.15 * atr or p > c:
                continue
            touched = (ext_low <= p + tol) and (ext_low >= p - PIERCE_ATR * atr)
        else:
            if p > sma + 0.15 * atr or p < c:
                continue
            touched = (ext_high >= p - tol) and (ext_high <= p + PIERCE_ATR * atr)
        if not touched:
            continue
        cluster = [(n, q) for n, q in pts if abs(q - p) <= 1.3 * tol]
        score = len(cluster)
        if score < MIN_CONFLUENCE and not (ALLOW_SMA200_ALONE and name == 'SMA200 H1'):
            continue
        key = (score, -abs(c - p))
        if best is None or key > best['key']:
            best = {'key': key, 'price': p, 'cluster': cluster, 'score': score}
    if best is None:
        return None, 'ยังไม่มีช่องเข้า (ราคาไม่ได้แตะจุดรวม SMA200/Market Profile ของ H1)'

    p = best['price']
    held = c > p if long else c < p
    if not held:
        return None, 'ราคายังไม่ยืนเหนือ/ใต้ช่องเข้า'
    if not confirm_candle(xm, long):
        return None, 'รอแท่ง M15 ยืนยัน'
    ext = (c - p) / atr if long else (p - c) / atr
    if ext > MAX_CHASE_ATR:
        return None, f'ไล่ราคา ({ext:.2f} ATR(H1) จากช่องเข้า)'

    prices = [q for _, q in best['cluster']]
    zone_lo, zone_hi = min(prices), max(prices)
    if long:
        sl = min(ext_low, zone_lo) - SL_BUFFER_ATR * atr
        risk = c - sl
    else:
        sl = max(ext_high, zone_hi) + SL_BUFFER_ATR * atr
        risk = sl - c
    if not (MIN_RISK_ATR * atr <= risk <= MAX_RISK_ATR * atr):
        return None, f'risk นอกช่วง ({risk / atr:.2f} ATR(H1))'
    s = 1 if long else -1
    tp1 = c + s * TP1_R * risk
    ahead = sorted([q for n, q in pts[:-1]
                    if TP2_MIN_R * risk <= s * (q - c) <= TP2_MAX_R * risk], key=lambda q: s * (q - c))
    tp2 = ahead[0] if ahead else c + s * TP2_FALLBACK_R * risk
    return {'entry': c, 'sl': sl, 'tp1': tp1, 'tp2': tp2, 'risk': risk, 'atr': atr,
            'level': p, 'zone_lo': zone_lo, 'zone_hi': zone_hi, 'score': best['score'],
            'names': ' + '.join(n for n, _ in best['cluster'])}, 'ok'


def decide(d1p, h4p, h1p, m15p, fast=False):
    """ทุกอาร์กิวเมนต์ = ผลของ prep() เฉพาะแท่งที่ปิดแล้ว
    fast=True ข้ามการคำนวณโปรไฟล์ถ้ายังไม่มีแท่ง M15 ยืนยัน (ใช้ตอน Backtest)"""
    dr, info = direction(d1p, h4p)
    out = {'direction': dr, 'info': info, 'levels': [], 'setup': None}
    if not dr:
        out['why'] = f'D1 {info["d1"]} / H4 {info["h4"]} ไม่ตรงกัน จึงยังไม่เล่น'
        return out
    if fast:
        long = dr == 'LONG'
        sma = float(h1p.SMA.iloc[-1])
        c = float(m15p.close.iloc[-1])
        if np.isnan(sma) or (long and c < sma) or ((not long) and c > sma) \
                or not confirm_candle(m15p.iloc[-1], long):
            out['why'] = 'ยังไม่มีแท่ง M15 ยืนยัน/อยู่ผิดฝั่ง SMA200(H1)'
            return out
    levels = profile_levels(h1p)
    sig, why = entry_setup(h1p, m15p, dr, levels)
    out.update(levels=levels, setup=sig, why=why)
    return out
