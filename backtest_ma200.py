"""Backtest กลยุทธ์ MA200 + Market Profile (คำนวณจาก D1/H4/H1 จุดเข้า M15)

- ดึง D1 / H4 / H1 / M15 จริงจาก Twelve Data ครั้งเดียว แล้วเดินทีละแท่ง M15
- ใช้เฉพาะแท่งที่ "ปิดแล้ว" ณ เวลานั้น (D1/H4/H1/M15) ไม่แอบดูอนาคต
- เข้าที่ราคาปิดแท่งสัญญาณ ถ้าแท่งเดียวชนทั้ง SL และ TP ถือว่า SL (เข้าข้างร้าย)
- WINDOWS=0 หมายถึงทดสอบต่อเนื่องทั้งช่วงข้อมูล (ได้จำนวนเทรดมากที่สุด)
- บันทึกตัวชี้วัดทุกเทรด (ระยะห่าง/ความชัน SMA200, ADX, ความผันผวน, MFE/MAE, ตาราง SL/TP)
  และแบ่งข้อมูลเป็นช่วงพัฒนา (70% แรก) กับช่วงทดสอบ (30% หลัง) ให้อัตโนมัติ
"""
import os, math
import numpy as np
import pandas as pd
import requests
import m1_swing_scanner as core
import ma200_strategy as st

WINDOWS = int(os.getenv('WINDOWS', '60'))
WINDOW_HOURS = int(os.getenv('WINDOW_HOURS', '24'))
SEED = int(os.getenv('SEED', '42'))
M15_PAGES = int(os.getenv('M15_PAGES', '2'))       # 1 หน้า = 5000 แท่ง M15 (~52 วัน BTC)
SPREAD = {'BTC/USD': float(os.getenv('SPREAD_BTC', '6.4')),
          'XAU/USD': float(os.getenv('SPREAD_XAU', '0.30'))}
TP1_SHARE = 0.7
TIME_STOP_BARS = 16
RUNNER_MAX_BARS = 48
COOLDOWN_BARS = 4
WARMUP = 10
TEST_FRAC = float(os.getenv('TEST_FRAC', '0.3'))   # สัดส่วนช่วงท้ายที่เก็บไว้ทดสอบ
HORIZON_BARS = 16                                   # MFE/MAE และตาราง SL/TP ดู 16 แท่ง M15 (4 ชม.)
GRID_SL = (0.75, 1.0, 1.5)                          # ระยะ SL เทียบกับ SL ของกลยุทธ์
GRID_TP = (0.5, 1.0, 1.5, 2.0)                      # ระยะ TP เป็นกี่เท่าของ SL
DIR_HORIZON_H4 = 6                                  # วัดความถูกต้องของทิศใน 6 แท่ง H4 (24 ชม.)
DEV, TEST = 'พัฒนา', 'ทดสอบ'


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
            raise RuntimeError(data.get('message', 'no data'))
        d = pd.DataFrame(data['values'])
        for c in ('open', 'high', 'low', 'close'):
            d[c] = pd.to_numeric(d[c], errors='coerce')
        d['datetime'] = pd.to_datetime(d['datetime'], utc=True)
        d = d.sort_values('datetime').set_index('datetime').dropna(subset=['open', 'high', 'low', 'close'])
        if p == 0 and len(d) > 1:
            d = d.iloc[:-1]
        frames.append(d)
        end = (d.index[0] - pd.Timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')
    out = pd.concat(frames).sort_index()
    return out[~out.index.duplicated()]


def load_history(symbol):
    m15 = history(symbol, '15min', 5000, M15_PAGES)
    days = (m15.index[-1] - m15.index[0]).days + 2
    h1 = history(symbol, '1h', min(5000, days * 24 + 260))
    h4 = history(symbol, '4h', min(5000, days * 6 + 260))
    d1 = history(symbol, '1day', min(5000, days + 260))
    return d1, h4, h1, m15


def simulate(df, i, direction, sig):
    hi, lo, cl = df.high.values, df.low.values, df.close.values
    n = len(df)
    sgn = 1 if direction == 'LONG' else -1
    entry, sl, tp1, tp2, risk = sig['entry'], sig['sl'], sig['tp1'], sig['tp2'], sig['risk']
    r1, r2 = abs(tp1 - entry) / risk, abs(tp2 - entry) / risk

    def hit_sl(j, lv):
        return lo[j] <= lv if sgn == 1 else hi[j] >= lv

    def hit_tp(j, lv):
        return hi[j] >= lv if sgn == 1 else lo[j] <= lv

    j = i
    for k in range(1, TIME_STOP_BARS + 1):
        j = i + k
        if j >= n:
            return 'END', sgn * (cl[n - 1] - entry) / risk, False, n - 1 - i
        if hit_sl(j, sl):
            return 'SL', -1.0, False, k
        if hit_tp(j, tp1):
            break
    else:
        return 'TIME', sgn * (cl[j] - entry) / risk, False, TIME_STOP_BARS

    runner, bars = None, k
    for m in range(1, RUNNER_MAX_BARS + 1):
        jj = j + m
        bars = k + m
        if jj >= n:
            runner = sgn * (cl[n - 1] - entry) / risk
            break
        if hit_sl(jj, entry):
            runner = 0.0
            break
        if hit_tp(jj, tp2):
            runner = r2
            break
    if runner is None:
        runner = sgn * (cl[min(j + RUNNER_MAX_BARS, n - 1)] - entry) / risk
    label = 'TP1+TP2' if runner == r2 else ('TP1+BE' if runner == 0.0 else 'TP1+TIME')
    return label, TP1_SHARE * r1 + (1 - TP1_SHARE) * runner, True, bars


def pick_windows(m15, rng, count, lo_idx):
    n = len(m15)
    size = WINDOW_HOURS * 4
    idx = m15.index
    wins, tries = [], 0
    while len(wins) < count and tries < count * 200:
        tries += 1
        a = int(rng.integers(lo_idx, n - size - 60))
        b = a + size
        if (idx[b - 1] - idx[a]).total_seconds() / 3600 > WINDOW_HOURS + 6:
            continue
        if any(not (b <= x or a >= y) for x, y in wins):
            continue
        wins.append((a, b))
    return sorted(wins)


def gold_closed(ts):
    """ตลาดทองปิด: เสาร์ทั้งวัน, อาทิตย์ก่อน 22:00 UTC, ศุกร์หลัง 21:00 UTC"""
    dow, hr = ts.dayofweek, ts.hour
    return dow == 5 or (dow == 6 and hr < 22) or (dow == 4 and hr >= 21)


def adx(df, n=14):
    h, l, c = df.high, df.low, df.close
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pdm.ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * mdm.ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def split_time(m15, lo_idx):
    t0, t1 = m15.index[lo_idx], m15.index[-1]
    return t0 + (t1 - t0) * (1 - TEST_FRAC)


def excursion(df, i, direction, entry, risk):
    """MFE/MAE ใน HORIZON_BARS แท่งถัดไป (หน่วย R) ไม่สนว่าชน SL/TP แล้วหรือยัง"""
    hi = df.high.values[i + 1:i + 1 + HORIZON_BARS]
    lo = df.low.values[i + 1:i + 1 + HORIZON_BARS]
    if len(hi) < HORIZON_BARS:
        return np.nan, np.nan
    if direction == 'LONG':
        return (hi.max() - entry) / risk, (entry - lo.min()) / risk
    return (entry - lo.min()) / risk, (hi.max() - entry) / risk


def grid_eval(df, i, direction, entry, risk, spread):
    """ลองทุกคู่ SL/TP กับจุดเข้าเดิม: คืน {(sl, tp): (ชนะไหม, R หลังหักต้นทุน)}"""
    hi = df.high.values[i + 1:i + 1 + HORIZON_BARS]
    lo = df.low.values[i + 1:i + 1 + HORIZON_BARS]
    cl = df.close.values[i + 1:i + 1 + HORIZON_BARS]
    out = {}
    if len(hi) < HORIZON_BARS:
        return out
    s = 1 if direction == 'LONG' else -1
    for a in GRID_SL:
        stop = a * risk
        for b in GRID_TP:
            win, r = False, None
            for j in range(HORIZON_BARS):
                adverse = (entry - lo[j]) if s == 1 else (hi[j] - entry)
                favor = (hi[j] - entry) if s == 1 else (entry - lo[j])
                if adverse >= stop:
                    r = -1.0
                    break
                if favor >= b * stop:
                    win, r = True, b
                    break
            if r is None:
                r = s * (cl[-1] - entry) / stop
            out[(a, b)] = (win, r - spread / stop)
    return out


def direction_study(symbol, d1, h4, m15):
    """ทิศจาก D1+H4 ถูกกี่ % ใน 24 ชม. ถัดไป (วัดทุกแท่ง H4 ไม่ขึ้นกับการเข้าเทรด)"""
    d1p, h4p = st.prep(d1), st.prep(h4)
    d1_close = (d1.index.tz_localize(None) + pd.Timedelta(days=1)).values
    t_close = h4.index + pd.Timedelta(hours=4)
    dc_all = np.searchsorted(d1_close, t_close.tz_localize(None).values, side='right')
    need = st.SMA_N + st.SLOPE_BARS + 2
    asof = (m15.index.tz_localize(None) + pd.Timedelta(minutes=15)).values
    lo_ok = np.flatnonzero(np.searchsorted(d1_close, asof, side='right') >= need)
    cut = split_time(m15, int(lo_ok[0])) if len(lo_ok) else m15.index[-1]
    cl, atr = h4p.close.values, h4p.ATR.values
    rows = []
    for k in range(need, len(h4p) - DIR_HORIZON_H4):
        if dc_all[k] < need or t_close[k] < m15.index[0]:
            continue
        if symbol == 'XAU/USD' and gold_closed(t_close[k]):
            continue
        fut = cl[k + DIR_HORIZON_H4] - cl[k]
        dr, _ = st.direction(d1p.iloc[:dc_all[k]], h4p.iloc[:k + 1])
        rows.append({'symbol': symbol, 'period': DEV if t_close[k] < cut else TEST, 'dir': dr,
                     'up': fut > 0, 'move_atr': fut / atr[k]})
    return pd.DataFrame(rows)


def run_asset(symbol, d1, h4, h1, m15, rng):
    d1p, h4p, h1p, m15p = st.prep(d1), st.prep(h4), st.prep(h1), st.prep(m15)
    d1_close = (d1.index.tz_localize(None) + pd.Timedelta(days=1)).values
    h4_close = (h4.index.tz_localize(None) + pd.Timedelta(hours=4)).values
    h1_close = (h1.index.tz_localize(None) + pd.Timedelta(hours=1)).values
    asof = (m15.index.tz_localize(None) + pd.Timedelta(minutes=15)).values
    dc_all = np.searchsorted(d1_close, asof, side='right')
    hc_all = np.searchsorted(h4_close, asof, side='right')
    h1c_all = np.searchsorted(h1_close, asof, side='right')
    need = st.SMA_N + st.SLOPE_BARS + 2
    ok_idx = np.flatnonzero((dc_all >= need) & (hc_all >= need) & (h1c_all >= st.SMA_N + 2))
    if len(ok_idx) == 0:
        return pd.DataFrame(), 0
    lo_idx = max(WARMUP, int(ok_idx[0]))
    cut = split_time(m15, lo_idx)
    adx_h4, adx_h1 = adx(h4p).values, adx(h1p).values
    vol_pct = h1p.ATR.rolling(500, min_periods=100).rank(pct=True).values
    if WINDOWS == 0:
        wins = [(lo_idx, len(m15) - 1)]
    else:
        wins = pick_windows(m15, rng, WINDOWS, lo_idx)
    trades = []
    for (a, b) in wins:
        free_at, last_i = a, {}
        for i in range(a, b):
            if i < free_at:
                continue
            if symbol == 'XAU/USD' and gold_closed(m15.index[i]):
                continue
            res = st.decide(d1p.iloc[:dc_all[i]], h4p.iloc[:hc_all[i]], h1p.iloc[:h1c_all[i]],
                            m15p.iloc[:i + 1], fast=True)
            sig, dr = res['setup'], res['direction']
            if not sig:
                continue
            if i - last_i.get(dr, -10 ** 9) < COOLDOWN_BARS:
                continue
            label, r_gross, tp1_hit, bars = simulate(m15, i, dr, sig)
            cost = SPREAD[symbol] / sig['risk']
            row = {'symbol': symbol, 'time': m15.index[i], 'period': DEV if m15.index[i] < cut else TEST,
                   'dir': dr, 'zone': sig['names'], 'score': sig['score'], 'result': label, 'tp1': tp1_hit,
                   'R_gross': r_gross, 'R_net': r_gross - cost, 'risk_atr': sig['risk'] / sig['atr']}
            s = 1 if dr == 'LONG' else -1
            for tf, dfp, cnt in (('D1', d1p, dc_all[i]), ('H4', h4p, hc_all[i]), ('H1', h1p, h1c_all[i])):
                x = dfp.iloc[cnt - 1]
                a = max(float(x.ATR), 1e-9)
                row[f'dist_{tf}'] = s * (float(x.close) - float(x.SMA)) / a
                row[f'slope_{tf}'] = s * (float(x.SMA) - float(dfp.SMA.iloc[cnt - 1 - st.SLOPE_BARS])) / a
            row['adx_H4'] = float(adx_h4[hc_all[i] - 1])
            row['adx_H1'] = float(adx_h1[h1c_all[i] - 1])
            row['vol_pct_H1'] = float(vol_pct[h1c_all[i] - 1])
            row['body'] = float(m15p.body_ratio.iloc[i])
            row['tp2_R'] = abs(sig['tp2'] - sig['entry']) / sig['risk']
            row['hour'] = int(m15.index[i].hour)
            row['mfe_R'], row['mae_R'] = excursion(m15, i, dr, sig['entry'], sig['risk'])
            for (ga, gb), (w, r) in grid_eval(m15, i, dr, sig['entry'], sig['risk'], SPREAD[symbol]).items():
                row[f'W_sl{ga}_tp{gb}'], row[f'R_sl{ga}_tp{gb}'] = w, r
            trades.append(row)
            last_i[dr] = i
            free_at = i + bars + 1
    return pd.DataFrame(trades), len(wins)


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0
    p = k / n
    den = 1 + z * z / n
    c = p + z * z / (2 * n)
    w = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - w) / den * 100, (c + w) / den * 100


def block(t, title):
    n = len(t)
    if n == 0:
        return f'**{title}**: ไม่มีเทรดเลย\n'
    k = int(t.tp1.sum())
    lo, hi = wilson(k, n)
    sl_n = int((t.result == 'SL').sum())
    dec = f'{k / (k + sl_n) * 100:.0f}%' if (k + sl_n) else '-'
    return (f'**{title}**: {n} เทรด | ถึง TP1 ก่อน SL = **{k / n * 100:.0f}%** (ช่วงเชื่อมั่น 95%: {lo:.0f}-{hi:.0f}%) | '
            f'เฉพาะที่จบ TP1/SL = {dec} | SL {(t.result == "SL").mean() * 100:.0f}% | '
            f'หมดเวลา {t.result.isin(["TIME", "END"]).mean() * 100:.0f}% | '
            f'เฉลี่ย/เทรด {t.R_gross.mean():+.2f}R (หักต้นทุน {t.R_net.mean():+.2f}R)\n')


def report(all_t, n_wins):
    mode = 'ต่อเนื่องทั้งช่วงข้อมูล' if WINDOWS == 0 else f'{n_wins} ช่วง × {WINDOW_HOURS} ชม.'
    lines = [f'## ผล Backtest MA200 + Market Profile\nโหมดทดสอบ: {mode} | seed {SEED}\n']
    lines.append(block(all_t, 'รวมทุกสินทรัพย์'))
    if len(all_t) and 'period' in all_t:
        for p in (DEV, TEST):
            lines.append(block(all_t[all_t.period == p], f'ช่วง{p}'))
    if len(all_t):
        for sym in all_t.symbol.unique():
            lines.append(block(all_t[all_t.symbol == sym], sym))
        for d in ('LONG', 'SHORT'):
            if (all_t.dir == d).any():
                lines.append(block(all_t[all_t.dir == d], d))
        for name, mask in (('ช่องเข้ามีหลายเส้นรวมกัน (score ≥2)', all_t.score >= 2),
                           ('แตะ SMA200 เส้นเดียว', all_t.score < 2)):
            if mask.any():
                lines.append(block(all_t[mask], name))
    lines.append('\nหมายเหตุ: TP1 = 1R ต้องชนะ >50% (บวกต้นทุน) ถึงจะเริ่มไม่ขาดทุน | '
                 'เทรดน้อยกว่า ~30 รายการ ตัวเลข % ยังไม่น่าเชื่อถือ | ผลย้อนหลังไม่รับประกันอนาคต')
    return '\n'.join(lines)


# ---------- รายงานเชิงลึก (คืนเป็นตาราง DataFrame) ----------
def _stats(t):
    n = len(t)
    if n == 0:
        return {'เทรด': 0, 'TP1%': np.nan, 'R สุทธิ/เทรด': np.nan}
    return {'เทรด': n, 'TP1%': round(t.tp1.mean() * 100, 1), 'R สุทธิ/เทรด': round(t.R_net.mean(), 3)}


def direction_report(dirs):
    rows = []
    if dirs is None or len(dirs) == 0:
        return pd.DataFrame()
    for (sym, p), g in dirs.groupby(['symbol', 'period']):
        up_base = g.up.mean()
        for dr in ('LONG', 'SHORT'):
            x = g[g.dir == dr]
            if len(x) == 0:
                continue
            ok = x.up.mean() if dr == 'LONG' else 1 - x.up.mean()
            base = up_base if dr == 'LONG' else 1 - up_base
            move = x.move_atr.mean() * (1 if dr == 'LONG' else -1)
            rows.append({'สินทรัพย์': sym, 'ช่วง': p, 'ทิศ': dr, 'จำนวนจุด': len(x),
                         'ทิศถูก %': round(ok * 100, 1), 'ฐานสุ่ม %': round(base * 100, 1),
                         'ราคาไปทางเรา (ATR H4)': round(move, 2)})
        none = g[g.dir.isna()]
        rows.append({'สินทรัพย์': sym, 'ช่วง': p, 'ทิศ': 'ไม่เล่น', 'จำนวนจุด': len(none),
                     'ทิศถูก %': np.nan, 'ฐานสุ่ม %': round(up_base * 100, 1), 'ราคาไปทางเรา (ATR H4)': np.nan})
    return pd.DataFrame(rows)


def excursion_report(all_t):
    rows = []
    for p in (DEV, TEST):
        t = all_t[all_t.period == p].dropna(subset=['mfe_R'])
        if len(t) == 0:
            continue
        rows.append({'ช่วง': p, 'เทรด': len(t), 'MFE กลาง (R)': round(t.mfe_R.median(), 2),
                     'MAE กลาง (R)': round(t.mae_R.median(), 2),
                     'ไปถึง 0.5R %': round((t.mfe_R >= 0.5).mean() * 100, 1),
                     'ไปถึง 1R %': round((t.mfe_R >= 1).mean() * 100, 1),
                     'ไปถึง 2R %': round((t.mfe_R >= 2).mean() * 100, 1),
                     'สวนเกิน 0.5R %': round((t.mae_R >= 0.5).mean() * 100, 1),
                     'สวนเกิน 1R %': round((t.mae_R >= 1).mean() * 100, 1)})
    return pd.DataFrame(rows)


def grid_report(all_t):
    rows = []
    for a in GRID_SL:
        for b in GRID_TP:
            w, r = f'W_sl{a}_tp{b}', f'R_sl{a}_tp{b}'
            if w not in all_t:
                continue
            row = {'SL (เท่าของเดิม)': a, 'TP (เท่าของ SL)': b, 'ชนะถ้าสุ่ม %': round(100 / (1 + b), 1)}
            for p in (DEV, TEST):
                t = all_t[all_t.period == p].dropna(subset=[r])
                row[f'{p}: ชนะ %'] = round(t[w].astype(bool).mean() * 100, 1) if len(t) else np.nan
                row[f'{p}: R สุทธิ'] = round(t[r].mean(), 3) if len(t) else np.nan
            rows.append(row)
    return pd.DataFrame(rows)


FEATURES = ['dist_D1', 'dist_H4', 'dist_H1', 'slope_D1', 'slope_H4', 'slope_H1', 'adx_H4', 'adx_H1',
            'vol_pct_H1', 'body', 'risk_atr', 'tp2_R', 'score']


def _session(h):
    return np.select([h < 7, h < 12, h < 17, h < 21], ['เอเชีย 0-7', 'ลอนดอน 7-12', 'นิวยอร์ก 12-17',
                                                       'นิวยอร์กช่วงท้าย 17-21'], 'ดึก 21-24')


def feature_report(all_t, min_n=30):
    if len(all_t) == 0:
        return pd.DataFrame()
    dev, test = all_t[all_t.period == DEV], all_t[all_t.period == TEST]
    base_dev, base_test = dev.R_net.mean(), test.R_net.mean()
    rows = []

    def z(x, base):
        # ห่างจากค่าเฉลี่ยรวมกี่เท่าของความคลาดเคลื่อน (เกิน 2 = ไม่น่าเป็นความบังเอิญ)
        sd = x.R_net.std(ddof=1)
        return (x.R_net.mean() - base) / (sd / math.sqrt(len(x))) if len(x) > 2 and sd > 0 else 0.0

    def add(name, bucket, d, t):
        sd, stt = _stats(d), _stats(t)
        flag = 'ข้อมูลน้อย'
        if sd['เทรด'] >= min_n and stt['เทรด'] >= min_n:
            zd, zt = z(d, base_dev), z(t, base_test)
            if zd > 2 and zt > 1:
                flag = '✓ ดีกว่าชัดทั้งสองช่วง'
            elif zd < -2 and zt < -1:
                flag = '✗ แย่กว่าชัดทั้งสองช่วง'
            else:
                flag = 'ไม่ชัด'
        rows.append({'ตัวชี้วัด': name, 'กลุ่ม': bucket, 'พัฒนา: เทรด': sd['เทรด'], 'พัฒนา: TP1%': sd['TP1%'],
                     'พัฒนา: R สุทธิ': sd['R สุทธิ/เทรด'], 'ทดสอบ: เทรด': stt['เทรด'], 'ทดสอบ: TP1%': stt['TP1%'],
                     'ทดสอบ: R สุทธิ': stt['R สุทธิ/เทรด'], 'ผล': flag})

    for f in FEATURES:
        if f not in all_t or dev[f].dropna().nunique() < 3:
            continue
        q1, q2 = dev[f].quantile([1 / 3, 2 / 3]).values
        if q1 == q2:
            continue
        for name, lo, hi in (('ต่ำ', -np.inf, q1), ('กลาง', q1, q2), ('สูง', q2, np.inf)):
            add(f, f'{name} ({lo:.2f} ถึง {hi:.2f})', dev[(dev[f] > lo) & (dev[f] <= hi)],
                test[(test[f] > lo) & (test[f] <= hi)])
    if 'hour' in all_t:
        sd, stt = _session(dev.hour.values), _session(test.hour.values)
        for sname in ['เอเชีย 0-7', 'ลอนดอน 7-12', 'นิวยอร์ก 12-17', 'นิวยอร์กช่วงท้าย 17-21', 'ดึก 21-24']:
            add('ช่วงเวลา (UTC)', sname, dev[sd == sname], test[stt == sname])
    return pd.DataFrame(rows)


def main():
    if not core.API_KEY:
        raise SystemExit('Missing TWELVEDATA_API_KEY')
    rng = np.random.default_rng(SEED)
    parts, dirs, total = [], [], 0
    for name, sym in core.ASSETS.items():
        print(f'[LOAD] {sym}')
        d1, h4, h1, m15 = load_history(sym)
        print(f'  M15 {len(m15)} แท่ง {m15.index[0]} → {m15.index[-1]} | H1 {len(h1)} | H4 {len(h4)} | D1 {len(d1)}')
        t, nw = run_asset(sym, d1, h4, h1, m15, rng)
        parts.append(t)
        dirs.append(direction_study(sym, d1, h4, m15))
        total += nw
    all_t = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    md = report(all_t, total)
    print(md)
    if len(all_t):
        print(direction_report(pd.concat(dirs, ignore_index=True)).to_string())
        print(excursion_report(all_t).to_string())
        print(grid_report(all_t).to_string())
        print(feature_report(all_t).to_string())
    all_t.to_csv('backtest_trades.csv', index=False)
    path = os.getenv('GITHUB_STEP_SUMMARY')
    if path:
        with open(path, 'a') as f:
            f.write(md + '\n')


if __name__ == '__main__':
    main()
