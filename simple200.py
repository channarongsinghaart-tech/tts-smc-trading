"""ระบบง่าย SMA200 สามกรอบ (D1 / H4 / H1) — ใช้แค่เส้น SMA200 กับ ATR

1) ทิศ    : ราคาปิด D1 อยู่เหนือ SMA200(D1) = ซื้ออย่างเดียว | อยู่ใต้ = ขายอย่างเดียว
2) ยืนยัน : ราคาปิด H4 ต้องอยู่ฝั่งเดียวกับ SMA200(H4)
3) จุดเข้า : เลือกได้ 2 แบบ
   'cross'    = แท่ง H1 ปิดข้ามเส้น SMA200(H1) กลับมาฝั่งเดียวกับ D1/H4 (ครบ 3 กรอบพอดี)
   'pullback' = H1 อยู่ฝั่งถูกอยู่แล้ว ย่อมาแตะ SMA200(H1) แล้วปิดยืนได้ (แท่งปิดไปทางเดียวกับทิศ)
4) SL      : ห่างจากราคาเข้า 1.5 x ATR(H4)
5) ออก     : เลือกได้ 'tp' = TP 2R | 'trend' = ออกเมื่อ H4 ปิดกลับไปอีกฝั่งของ SMA200(H4)
ใช้เฉพาะแท่งที่ปิดแล้ว (ไม่แอบดูอนาคต) และไม่เข้าเทรดทองตอนตลาดปิด
"""
import math
import numpy as np
import pandas as pd
import requests
import m1_swing_scanner as core

SMA_N = 200
SL_ATR_H4 = 1.5
TP_R = 2.0
PULL_TOL_ATR = 0.2            # ระยะที่นับว่า "แตะเส้น" (ATR ของ H1)
MAX_HOLD_H1 = 24 * 15         # ถือนานสุด 15 วัน
TEST_FRAC = 0.3               # 30% ท้ายของช่วงเวลาเก็บไว้ทดสอบ
ROLLOVER_BAR_HOUR = 20        # โหมดไม่ข้ามคืน: ปิดตอนแท่ง H1 20:00-21:00 UTC ปิด (ก่อนตัดรอบ swap)
NO_ENTRY_FROM_HOUR = 18       # โหมดไม่ข้ามคืน: ไม่เปิดไม้ใหม่ตั้งแต่แท่ง 18:00 UTC
SPREAD = {'BTC/USD': 6.4, 'XAU/USD': 0.30}
# ค่าข้ามคืน (% ของราคาต่อคืน) แยกฝั่งซื้อ/ขาย ค่าบวก = เสียเงิน (ใส่ตามโบรกเกอร์)
# ทอง Exness (XAUUSDm, ต.ค. 2026): Swap long -515.5 จุด = 0.5155 USD/oz ต่อคืน ≈ 0.0124% ของราคา, Swap short 0
SWAP_PCT_DAY = {'BTC/USD': {'LONG': 0.0, 'SHORT': 0.0}, 'XAU/USD': {'LONG': 0.0124, 'SHORT': 0.0}}
ROLLOVER_HOUR_UTC = 21        # เวลาตัดรอบคิด swap (โดยประมาณ)
# จำนวนเท่าที่คิด swap ในแต่ละคืน จันทร์..อาทิตย์ (ทองตามตาราง Exness: พุธ 3 เท่า, เสาร์-อาทิตย์ไม่คิด)
SWAP_DAYS = {'XAU/USD': (1, 1, 3, 1, 1, 0, 0), 'BTC/USD': (1, 1, 1, 1, 1, 1, 1)}


def rollover_units(symbol, t_in, t_out):
    """นับจำนวนครั้งที่ถือผ่านเวลาตัดรอบ (ถ่วงวันพุธ 3 เท่า) ระหว่างเปิดถึงปิดไม้"""
    t = t_in.normalize() + pd.Timedelta(hours=ROLLOVER_HOUR_UTC)
    if t <= t_in:
        t += pd.Timedelta(days=1)
    units = 0
    while t < t_out:
        units += SWAP_DAYS[symbol][t.dayofweek]
        t += pd.Timedelta(days=1)
    return units
ENTRY_NAMES = {'cross': 'H1 ข้ามเส้น 200', 'pullback': 'H1 ย่อแตะเส้น 200'}
EXIT_NAMES = {'tp': 'TP 2R', 'trend': 'ออกเมื่อ H4 กลับฝั่ง'}
DEV, TEST = 'พัฒนา', 'ทดสอบ'


def prep(d):
    d = d.copy()
    c, h, l = d.close, d.high, d.low
    d['SMA'] = c.rolling(SMA_N).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d['ATR'] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    d['side'] = np.sign(c - d['SMA']).fillna(0)
    return d


def gold_closed(ts):
    """ตลาดทองปิด: เสาร์ทั้งวัน, อาทิตย์ก่อน 22:00 UTC, ศุกร์หลัง 21:00 UTC"""
    dow, hr = ts.dayofweek, ts.hour
    return dow == 5 or (dow == 6 and hr < 22) or (dow == 4 and hr >= 21)


# ---------------- ข้อมูล ----------------
def history(symbol, interval, size, pages=1):
    frames, end = [], None
    for p in range(pages):
        core._pace()
        params = {'symbol': symbol, 'interval': interval, 'outputsize': size,
                  'apikey': core.API_KEY, 'timezone': 'UTC'}
        if end:
            params['end_date'] = end
        data = requests.get(f'{core.BASE}/time_series', params=params, timeout=60).json()
        if 'values' not in data:
            if p > 0:
                break                      # ข้อมูลย้อนหลังหมดแล้ว ใช้เท่าที่มี
            raise RuntimeError(data.get('message', 'no data'))
        d = pd.DataFrame(data['values'])
        for c in ('open', 'high', 'low', 'close'):
            d[c] = pd.to_numeric(d[c], errors='coerce')
        d['datetime'] = pd.to_datetime(d['datetime'], utc=True)
        d = d.sort_values('datetime').set_index('datetime').dropna(subset=['open', 'high', 'low', 'close'])
        if p == 0 and len(d) > 1:
            d = d.iloc[:-1]                # ตัดแท่งที่ยังไม่ปิด
        frames.append(d)
        end = (d.index[0] - pd.Timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')
    out = pd.concat(frames).sort_index()
    return out[~out.index.duplicated()]


def load_history(symbol, h1_pages=4):
    h1 = history(symbol, '1h', 5000, h1_pages)
    days = (h1.index[-1] - h1.index[0]).days + 2
    h4 = history(symbol, '4h', 5000, max(1, math.ceil((days * 6 + 260) / 5000)))
    d1 = history(symbol, '1day', min(5000, days + 260))
    return d1, h4, h1


# ---------------- สัญญาณ ----------------
def align(d1p, h4p, h1p):
    """สถานะ D1/H4 ณ เวลาปิดของแต่ละแท่ง H1 (ใช้เฉพาะแท่ง D1/H4 ที่ปิดแล้ว)"""
    asof = (h1p.index.tz_localize(None) + pd.Timedelta(hours=1)).values
    dc = np.searchsorted((d1p.index.tz_localize(None) + pd.Timedelta(days=1)).values, asof, side='right')
    hc = np.searchsorted((h4p.index.tz_localize(None) + pd.Timedelta(hours=4)).values, asof, side='right')
    ok = (dc >= SMA_N) & (hc >= SMA_N)
    d1s = np.where(ok, d1p.side.values[np.maximum(dc - 1, 0)], 0)
    h4s = np.where(ok, h4p.side.values[np.maximum(hc - 1, 0)], 0)
    atr4 = h4p.ATR.values[np.maximum(hc - 1, 0)]
    return d1s, h4s, atr4, hc


def context(d1p, h4p, h1p):
    """ตัวชี้วัดสภาพตลาด ณ เวลาปิดของแต่ละแท่ง H1 (ใช้เฉพาะแท่ง D1/H4 ที่ปิดแล้ว)
    dist_d1 = ราคาปิด D1 ห่างเส้น 200 กี่ ATR(D1) | slope_h4 = เส้น 200 ของ H4 เปลี่ยนไปกี่ ATR(H4) ใน 10 แท่ง"""
    asof = (h1p.index.tz_localize(None) + pd.Timedelta(hours=1)).values
    dc = np.searchsorted((d1p.index.tz_localize(None) + pd.Timedelta(days=1)).values, asof, side='right')
    hc = np.searchsorted((h4p.index.tz_localize(None) + pd.Timedelta(hours=4)).values, asof, side='right')
    di, hi_ = np.maximum(dc - 1, 0), np.maximum(hc - 1, 0)
    d_atr = d1p.ATR.values[di]
    dist_d1 = (d1p.close.values[di] - d1p.SMA.values[di]) / np.where(d_atr > 0, d_atr, np.nan)
    sma4 = h4p.SMA.values
    h_atr = h4p.ATR.values[hi_]
    slope_h4 = (sma4[hi_] - sma4[np.maximum(hi_ - 10, 0)]) / np.where(h_atr > 0, h_atr, np.nan)
    h, l, c = h4p.high, h4p.low, h4p.close
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=h4p.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=h4p.index)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    pdi = 100 * pdm.ewm(alpha=1 / 14, adjust=False).mean() / atr
    mdi = 100 * mdm.ewm(alpha=1 / 14, adjust=False).mean() / atr
    adx4 = (100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)).ewm(alpha=1 / 14, adjust=False).mean().values
    return {'dist_d1': dist_d1, 'slope_h4': slope_h4, 'adx_h4': adx4[hi_]}


def signals(h1p, d1s, h4s, entry):
    """คืน +1 (ซื้อ) / -1 (ขาย) / 0 ของแต่ละแท่ง H1"""
    s1 = h1p.side.values
    prev = np.r_[0, s1[:-1]]
    trend = np.where((d1s == h4s) & (d1s != 0), d1s, 0)
    if entry == 'cross':
        sig = (trend != 0) & (s1 == trend) & (prev == -trend)
    else:
        sma, atr = h1p.SMA.values, h1p.ATR.values
        op, hi, lo, cl = h1p.open.values, h1p.high.values, h1p.low.values, h1p.close.values
        touch_long = (lo <= sma + PULL_TOL_ATR * atr) & (cl > op)
        touch_short = (hi >= sma - PULL_TOL_ATR * atr) & (cl < op)
        sig = (trend != 0) & (s1 == trend) & (prev == trend) & np.where(trend == 1, touch_long, touch_short)
    return np.where(sig, trend, 0).astype(int)


# ---------------- จำลองเทรด ----------------
def simulate(h1p, i, s, risk, exit_mode, h4s, hc, max_hold=None, tp_r=None, intraday=False):
    """max_hold = ชั่วโมงตามเวลาจริง (None = แบบเดิม: 15 วันนับเป็นแท่ง)
    ถ้าจำกัดเวลาถือหรือโหมดไม่ข้ามคืน จะปิดก่อนตลาดหยุด (เช่น ทองก่อนเสาร์-อาทิตย์) ด้วย"""
    op, hi, lo, cl = h1p.open.values, h1p.high.values, h1p.low.values, h1p.close.values
    n = len(cl)
    entry = cl[i]
    tp_r = TP_R if tp_r is None else tp_r
    sl, tp = entry - s * risk, entry + s * tp_r * risk
    last = min(n - 1, i + MAX_HOLD_H1)
    hours = h1p.index.hour
    tns = h1p.index.as_unit('ns').asi8           # บังคับหน่วยนาโนวินาที (pandas 3 อาจใช้ไมโครวินาที)
    hour_ns = 3_600_000_000_000
    guard_gap = intraday or max_hold is not None
    for j in range(i + 1, last + 1):
        if s == 1:                                         # SL ก่อนเสมอ (เข้าข้างร้าย) และคิดราคาเปิดที่กระโดดข้าม
            if op[j] <= sl:
                return j, op[j], 'SL'
            if lo[j] <= sl:
                return j, sl, 'SL'
        else:
            if op[j] >= sl:
                return j, op[j], 'SL'
            if hi[j] >= sl:
                return j, sl, 'SL'
        if exit_mode == 'tp':
            if s == 1 and op[j] >= tp:
                return j, op[j], 'TP'
            if s == 1 and hi[j] >= tp:
                return j, tp, 'TP'
            if s == -1 and op[j] <= tp:
                return j, op[j], 'TP'
            if s == -1 and lo[j] <= tp:
                return j, tp, 'TP'
        elif hc[j] != hc[j - 1] and h4s[j] == -s:          # แท่ง H4 เพิ่งปิด และกลับฝั่งเส้น 200
            return j, cl[j], 'H4 กลับฝั่ง'
        if intraday and hours[j] == ROLLOVER_BAR_HOUR:      # ปิดก่อนตัดรอบ swap (แท่ง 20:00-21:00 UTC)
            return j, cl[j], 'ปิดก่อนข้ามคืน'
        if max_hold is not None and (tns[j] - tns[i]) / hour_ns >= max_hold:
            return j, cl[j], 'หมดเวลา'
        if guard_gap and j + 1 < n and (tns[j + 1] - tns[j]) > 6 * hour_ns:
            return j, cl[j], 'ปิดก่อนตลาดหยุด'
    return last, cl[last], 'หมดเวลา' if last < n - 1 else 'จบข้อมูล'


def run(symbol, d1, h4, h1, entry='cross', exit_mode='trend', max_hold=None, tp_r=None, intraday=False,
        filters=None):
    """filters (ไม่ใส่ = แบบเดิม): {'no_weekend': True, 'min_dist_d1': 1.0, 'slope_h4': True}"""
    filters = filters or {}
    d1p, h4p, h1p = prep(d1), prep(h4), prep(h1)
    d1s, h4s, atr4, hc = align(d1p, h4p, h1p)
    ctx = context(d1p, h4p, h1p)
    sig = signals(h1p, d1s, h4s, entry)
    ready = np.flatnonzero(d1s != 0)
    if len(ready) == 0:
        return pd.DataFrame()
    t0, t1 = h1p.index[ready[0]], h1p.index[-1]
    cut = t0 + (t1 - t0) * (1 - TEST_FRAC)
    idx = h1p.index
    tns = idx.as_unit('ns').asi8
    trades, free = [], 0
    for i in np.flatnonzero(sig != 0):
        if i < free or i >= len(h1p) - 1:
            continue
        if symbol == 'XAU/USD' and gold_closed(idx[i]):
            continue
        if intraday and idx[i].hour >= NO_ENTRY_FROM_HOUR:
            continue
        if (intraday or max_hold is not None) and i + 1 < len(idx) and (tns[i + 1] - tns[i]) > 6 * 3_600_000_000_000:
            continue                                   # ถือสั้น: ไม่เปิดไม้บนแท่งสุดท้ายก่อนตลาดหยุด
        s = int(sig[i])
        if filters.get('no_weekend') and idx[i].dayofweek >= 5:
            continue
        if filters.get('min_dist_d1') is not None and not (s * ctx['dist_d1'][i] >= filters['min_dist_d1']):
            continue
        if filters.get('slope_h4') and not (s * ctx['slope_h4'][i] > 0):
            continue
        risk = SL_ATR_H4 * max(float(atr4[i]), 1e-9)
        entry_px = float(h1p.close.values[i])
        j, px, why = simulate(h1p, i, s, risk, exit_mode, h4s, hc, max_hold, tp_r, intraday)
        days = (idx[j] - idx[i]).total_seconds() / 86400
        side = 'LONG' if s == 1 else 'SHORT'
        nights = rollover_units(symbol, idx[i] + pd.Timedelta(hours=1), idx[j] + pd.Timedelta(hours=1))
        swap_r = entry_px * SWAP_PCT_DAY[symbol][side] / 100 * nights / risk
        cost = SPREAD[symbol] / risk + swap_r
        r = s * (px - entry_px) / risk
        trades.append({'symbol': symbol, 'time': idx[i], 'period': DEV if idx[i] < cut else TEST,
                       'dir': side, 'entry': entry_px, 'exit': float(px), 'risk': risk,
                       'exit_time': idx[j], 'result': why, 'days': days,
                       'nights': nights, 'R_gross': r, 'swap_R': swap_r, 'R_net': r - cost,
                       'dist_d1': s * ctx['dist_d1'][i], 'slope_h4': s * ctx['slope_h4'][i],
                       'adx_h4': ctx['adx_h4'][i], 'weekday': int(idx[i].dayofweek), 'hour': int(idx[i].hour)})
        free = j + 1
    return pd.DataFrame(trades)


# ---------------- สรุปผล ----------------
def _verdict(dev, test, min_n=20):
    if len(dev) < min_n or len(test) < min_n:
        return 'ข้อมูลน้อย'
    def z(x):
        sd = x.std(ddof=1)
        return x.mean() / (sd / math.sqrt(len(x))) if sd > 0 else 0.0
    zd, zt = z(dev.R_net), z(test.R_net)
    if zd > 2 and zt > 1:
        return '✓ ได้เปรียบชัดทั้งสองช่วง'
    if zd < -2 and zt < -1:
        return '✗ เสียเปรียบชัดทั้งสองช่วง'
    return 'ไม่ชัด'


def stats(t):
    n = len(t)
    if n == 0:
        return {'เทรด': 0}
    r = t.R_net
    m = r.mean()
    se = r.std(ddof=1) / math.sqrt(n) if n > 1 else float('nan')
    loss = -r[r < 0].sum()
    cum = r.cumsum()
    return {'เทรด': n, 'ชนะ %': round((r > 0).mean() * 100, 1),
            'R/เทรด ก่อนหักต้นทุน': round(t.R_gross.mean(), 3), 'R/เทรด สุทธิ': round(m, 3),
            'ช่วงเชื่อมั่น 95%': f'{m - 1.96 * se:+.2f} ถึง {m + 1.96 * se:+.2f}' if n > 1 else '-',
            'PF': round(r[r > 0].sum() / loss, 2) if loss > 0 else float('nan'),
            'R รวม': round(r.sum(), 1), 'ขาดทุนสะสมสูงสุด R': round((cum.cummax() - cum).max(), 1),
            'ถือเฉลี่ย (วัน)': round(t.days.mean(), 1)}


def report(t):
    rows = []
    if len(t) == 0:
        return pd.DataFrame()
    t = t.sort_values('time')
    groups = [('รวมทั้งหมด', t), (f'ช่วง{DEV}', t[t.period == DEV]), (f'ช่วง{TEST}', t[t.period == TEST])]
    groups += [(s, t[t.symbol == s]) for s in t.symbol.unique()]
    groups += [(d, t[t.dir == d]) for d in ('LONG', 'SHORT') if (t.dir == d).any()]
    for name, g in groups:
        rows.append({'กลุ่ม': name, **stats(g)})
    out = pd.DataFrame(rows)
    out.attrs['verdict'] = _verdict(t[t.period == DEV], t[t.period == TEST])
    return out


def compare(data):
    """เทียบ 4 แบบ (จุดเข้า 2 x การออก 2) กับข้อมูลชุดเดียวกัน"""
    rows = []
    for e in ENTRY_NAMES:
        for x in EXIT_NAMES:
            parts = [run(sym, *frames, entry=e, exit_mode=x) for sym, frames in data.items()]
            t = pd.concat([p for p in parts if len(p)], ignore_index=True) if any(len(p) for p in parts) else pd.DataFrame()
            if len(t) == 0:
                rows.append({'จุดเข้า': ENTRY_NAMES[e], 'การออก': EXIT_NAMES[x], 'ผล': 'ไม่มีเทรด'})
                continue
            dev, test = t[t.period == DEV], t[t.period == TEST]
            sd, stt = stats(dev), stats(test)
            rows.append({'จุดเข้า': ENTRY_NAMES[e], 'การออก': EXIT_NAMES[x],
                         'พัฒนา: เทรด': sd.get('เทรด', 0), 'พัฒนา: R สุทธิ': sd.get('R/เทรด สุทธิ'),
                         'พัฒนา: ช่วงเชื่อมั่น': sd.get('ช่วงเชื่อมั่น 95%'),
                         'ทดสอบ: เทรด': stt.get('เทรด', 0), 'ทดสอบ: R สุทธิ': stt.get('R/เทรด สุทธิ'),
                         'ทดสอบ: ช่วงเชื่อมั่น': stt.get('ช่วงเชื่อมั่น 95%'),
                         'ชนะ % รวม': stats(t)['ชนะ %'], 'PF รวม': stats(t)['PF'], 'ผล': _verdict(dev, test)})
    return pd.DataFrame(rows)


HOLD_CHOICES = {'12 ชม.': 12, '1 วัน': 24, '2 วัน': 48, '3 วัน': 72, '15 วัน (แบบเดิม)': None}
TP_CHOICES = {'1R': 1.0, '1.5R': 1.5, '2R': 2.0}


def compare_short(symbol, frames, entry='cross'):
    """ถือสั้น: เทียบ TP 1 / 1.5 / 2R กับเวลาถือสูงสุด 12 ชม. - 3 วัน และโหมดไม่ข้ามคืน"""
    rows = []
    combos = [(tp, hold, False) for tp in TP_CHOICES for hold in ('12 ชม.', '1 วัน', '2 วัน', '3 วัน')]
    combos += [(tp, 'ไม่ข้ามคืน', True) for tp in TP_CHOICES]
    for tp, hold, intra in combos:
        t = run(symbol, *frames, entry=entry, exit_mode='tp', tp_r=TP_CHOICES[tp],
                max_hold=None if intra else HOLD_CHOICES[hold], intraday=intra)
        if len(t) == 0:
            rows.append({'TP': tp, 'ถือนานสุด': hold, 'ผล': 'ไม่มีเทรด'})
            continue
        dev, test = t[t.period == DEV], t[t.period == TEST]
        sd, stt, sa = stats(dev), stats(test), stats(t)
        rows.append({'TP': tp, 'ถือนานสุด': hold, 'เทรด': sa['เทรด'], 'ชนะ %': sa['ชนะ %'],
                     'ถือเฉลี่ย (วัน)': sa['ถือเฉลี่ย (วัน)'], 'R/เทรด สุทธิ': sa['R/เทรด สุทธิ'],
                     'ช่วงเชื่อมั่น 95%': sa['ช่วงเชื่อมั่น 95%'], 'PF': sa['PF'],
                     'พัฒนา: R สุทธิ': sd.get('R/เทรด สุทธิ'), 'ทดสอบ: R สุทธิ': stt.get('R/เทรด สุทธิ'),
                     'ผล': _verdict(dev, test)})
    return pd.DataFrame(rows)


FILTER_SETS = {
    'พื้นฐาน (ไม่กรอง)': {},
    'ไม่เข้าวันเสาร์-อาทิตย์': {'no_weekend': True},
    'ราคา D1 ห่างเส้น 200 ≥ 1 ATR': {'min_dist_d1': 1.0},
    'เส้น 200 ของ H4 ชันตามทิศ': {'slope_h4': True},
    'รวมทั้ง 3 ข้อ': {'no_weekend': True, 'min_dist_d1': 1.0, 'slope_h4': True},
}


def compare_filters(symbol, frames, tp_r=2.0, max_hold=48):
    """เทียบตัวกรองที่ตั้งสมมติฐานไว้ล่วงหน้า (ไม่ไล่หาตัวเลข) จุดเข้า H1 ข้ามเส้น 200 + TP 2R + ถือ ≤ 48 ชม."""
    rows = []
    for name, flt in FILTER_SETS.items():
        t = run(symbol, *frames, entry='cross', exit_mode='tp', tp_r=tp_r, max_hold=max_hold, filters=flt)
        if len(t) == 0:
            rows.append({'ตัวกรอง': name, 'ผล': 'ไม่มีเทรด'})
            continue
        dev, test = t[t.period == DEV], t[t.period == TEST]
        sa = stats(t)
        rows.append({'ตัวกรอง': name, 'เทรด': sa['เทรด'], 'ชนะ %': sa['ชนะ %'], 'R/เทรด สุทธิ': sa['R/เทรด สุทธิ'],
                     'ช่วงเชื่อมั่น 95%': sa['ช่วงเชื่อมั่น 95%'], 'PF': sa['PF'],
                     'พัฒนา: R สุทธิ': stats(dev).get('R/เทรด สุทธิ'), 'ทดสอบ: R สุทธิ': stats(test).get('R/เทรด สุทธิ'),
                     'ผล': _verdict(dev, test)})
    return pd.DataFrame(rows)


def diagnose(t, min_n=15):
    """แยกเทรดตามสภาพตลาด ณ จุดเข้า ดูว่ากลุ่มไหนต่างจากค่าเฉลี่ยชัดทั้งสองช่วง"""
    if len(t) == 0:
        return pd.DataFrame()
    dev, test = t[t.period == DEV], t[t.period == TEST]
    bd, bt_ = dev.R_net.mean(), test.R_net.mean()

    def z(x, base):
        sd = x.R_net.std(ddof=1)
        return (x.R_net.mean() - base) / (sd / math.sqrt(len(x))) if len(x) > 2 and sd > 0 else 0.0

    rows = []

    def add(feat, name, md, mt):
        d, x = dev[md], test[mt]
        flag = 'ข้อมูลน้อย'
        if len(d) >= min_n and len(x) >= min_n:
            zd, zt = z(d, bd), z(x, bt_)
            flag = ('✓ ดีกว่าชัดทั้งสองช่วง' if zd > 2 and zt > 1 else
                    '✗ แย่กว่าชัดทั้งสองช่วง' if zd < -2 and zt < -1 else 'ไม่ชัด')
        rows.append({'ตัวชี้วัด': feat, 'กลุ่ม': name, 'พัฒนา: เทรด': len(d),
                     'พัฒนา: R สุทธิ': round(d.R_net.mean(), 3) if len(d) else None,
                     'ทดสอบ: เทรด': len(x), 'ทดสอบ: R สุทธิ': round(x.R_net.mean(), 3) if len(x) else None, 'ผล': flag})

    add('วัน', 'จันทร์-ศุกร์', dev.weekday < 5, test.weekday < 5)
    add('วัน', 'เสาร์-อาทิตย์', dev.weekday >= 5, test.weekday >= 5)
    for name, a, b in (('เอเชีย 0-7', 0, 7), ('ลอนดอน 7-13', 7, 13), ('อเมริกา 13-21', 13, 21), ('ดึก 21-24', 21, 24)):
        add('ช่วงเวลา (UTC)', name, (dev.hour >= a) & (dev.hour < b), (test.hour >= a) & (test.hour < b))
    for feat in ('dist_d1', 'slope_h4', 'adx_h4'):
        if dev[feat].dropna().nunique() < 3:
            continue
        q1, q2 = dev[feat].quantile([1 / 3, 2 / 3]).values
        for name, lo, hi in (('ต่ำ', -np.inf, q1), ('กลาง', q1, q2), ('สูง', q2, np.inf)):
            add(feat, f'{name} ({lo:.2f} ถึง {hi:.2f})', (dev[feat] > lo) & (dev[feat] <= hi),
                (test[feat] > lo) & (test[feat] <= hi))
    return pd.DataFrame(rows)


# ---------------- สแกนสด ----------------
def live(symbol):
    d1 = prep(core.fetch(symbol, '1day', 300))
    h4 = prep(core.fetch(symbol, '4h', 300))
    h1 = prep(core.fetch(symbol, '1h', 300))
    d1s, h4s, atr4, hc = align(d1, h4, h1)
    state = {}
    for name, d in (('D1', d1), ('H4', h4), ('H1', h1)):
        x = d.iloc[-1]
        state[name] = {'side': int(x.side), 'close': float(x.close), 'sma': float(x.SMA),
                       'dist_atr': float((x.close - x.SMA) / x.ATR) if x.ATR > 0 else float('nan')}
    trend = state['D1']['side'] if state['D1']['side'] == state['H4']['side'] else 0
    sig = {e: int(signals(h1, d1s, h4s, e)[-1]) for e in ENTRY_NAMES}
    risk = SL_ATR_H4 * float(h4.ATR.iloc[-1])
    entry = float(h1.close.iloc[-1])
    s = trend if trend else 1
    plan = {'entry': entry, 'sl': entry - s * risk, 'tp': entry + s * TP_R * risk, 'risk': risk}
    return {'state': state, 'trend': trend, 'signal': sig, 'plan': plan, 'h1': h1,
            'time': h1.index[-1], 'gold_closed': symbol == 'XAU/USD' and gold_closed(h1.index[-1])}
