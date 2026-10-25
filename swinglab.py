"""ห้องทดลองสวิง: เทียบ 4 สูตรจุดเข้า ถือไม่เกิน 2 วัน เทรดได้ทั้งขึ้นและลง (ใช้ข้อมูล D1/H4/H1 ชุดเดียวกับ Backtest)

M1 ย่อในเทรนด์      : D1+H4 อยู่ฝั่งเดียวกันของ SMA200 + RSI(2) ของ H1 ย่อแรง (<10 ซื้อ / >90 ขาย) | SL 1×ATR(H4) | ถือ ≤ 2 วัน
M2 สวิงในกรอบ       : ADX(H4) < 20 (ไม่มีเทรนด์) + H1 หลุดกรอบ Bollinger(20,2) แล้วปิดกลับเข้ามา | SL 1×ATR(H4) | ถือ ≤ 2 วัน
M3 เบรกกรอบเอเชีย   : กรอบ 00-07 UTC, H1 ปิดเบรกช่วง 07-16 UTC เข้าตามทิศเบรก | SL กลางกรอบ | ปิดก่อน 21:00 UTC
M4 กวาด High/Low เมื่อวาน: H1 ทะลุ High/Low ของ D1 เมื่อวานแล้วปิดกลับ เข้าสวน (07-18 UTC) | SL เลยไส้เทียน | ปิดก่อน 21:00 UTC
ทุกสูตร: TP ทดสอบที่ 1R และ 1.5R, ใช้เฉพาะแท่งที่ปิดแล้ว, หักสเปรดและ swap ตามจริง, M3/M4 วันละไม่เกิน 1 ไม้
"""
import math
import numpy as np
import pandas as pd
import simple200 as S

MODELS = {
    'M1': 'M1 ย่อในเทรนด์ (RSI2)',
    'M2': 'M2 สวิงในกรอบ (Bollinger)',
    'M3': 'M3 เบรกกรอบเอเชีย',
    'M4': 'M4 กวาด High/Low เมื่อวาน',
}
TP_LIST = (1.0, 1.5)
HOLD_H = 48                  # M1/M2 ถือนานสุด 48 ชม. (เวลาจริง ไม่ข้ามสุดสัปดาห์)
ADX_RANGE = 20
MIN_RISK_ATR1, MAX_RISK_ATR1 = 0.5, 2.0     # M3/M4 ระยะ SL ที่ยอมรับ (ATR ของ H1)


def rsi(c, n):
    d = c.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    au = up.ewm(alpha=1 / n, adjust=False).mean()
    ad = dn.ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + au / ad.replace(0, np.nan))


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


def features(d1, h4, h1):
    """คำนวณทุกอย่างที่ต้องใช้ ณ เวลาปิดของแต่ละแท่ง H1 (เฉพาะข้อมูลที่ปิดแล้ว)"""
    d1p, h4p, h1p = S.prep(d1), S.prep(h4), S.prep(h1)
    asof = (h1p.index.tz_localize(None) + pd.Timedelta(hours=1)).values
    dc = np.searchsorted((d1p.index.tz_localize(None) + pd.Timedelta(days=1)).values, asof, side='right')
    hc = np.searchsorted((h4p.index.tz_localize(None) + pd.Timedelta(hours=4)).values, asof, side='right')
    di, hi_ = np.maximum(dc - 1, 0), np.maximum(hc - 1, 0)
    f = pd.DataFrame(index=h1p.index)
    for c in ('open', 'high', 'low', 'close', 'ATR'):
        f[c] = h1p[c].values
    f['ok_trend'] = (dc >= S.SMA_N) & (hc >= S.SMA_N)
    f['ok_any'] = (dc >= 2) & (hc >= 40) & (np.arange(len(f)) >= 40)
    f['d1_side'] = np.where(f.ok_trend, d1p.side.values[di], 0)
    f['h4_side'] = np.where(f.ok_trend, h4p.side.values[hi_], 0)
    f['atr4'] = h4p.ATR.values[hi_]
    f['adx4'] = adx(h4p).values[hi_]
    f['pdh'], f['pdl'] = d1p.high.values[di], d1p.low.values[di]
    c = h1p.close
    f['rsi2'] = rsi(c, 2).values
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std(ddof=0)
    f['bb_up'], f['bb_lo'] = (mid + 2 * sd).values, (mid - 2 * sd).values
    hour = h1p.index.hour
    f['hour'] = hour
    f['date'] = h1p.index.date
    asia = h1p[hour < 7]
    g = asia.groupby(asia.index.date)
    ah, al, an = g.high.max(), g.low.min(), g.size()
    after = hour >= 7                               # ใช้ได้เฉพาะหลังช่วงเอเชียจบ (ไม่แอบดูอนาคต)
    f['asia_hi'] = np.where(after, f.date.map(ah).astype(float), np.nan)
    f['asia_lo'] = np.where(after, f.date.map(al).astype(float), np.nan)
    f['asia_n'] = np.where(after, f.date.map(an).fillna(0).astype(int), 0)
    return f, h1p


def model_signals(f, model):
    """คืน (ทิศ +1/-1/0, ระยะ SL) ของแต่ละแท่ง H1"""
    n = len(f)
    sig, risk = np.zeros(n, dtype=int), np.full(n, np.nan)
    cl, op, hi, lo, atr1 = f.close.values, f.open.values, f.high.values, f.low.values, f.ATR.values
    prev_cl = np.r_[np.nan, cl[:-1]]
    if model == 'M1':
        trend = np.where((f.d1_side.values == f.h4_side.values) & (f.d1_side.values != 0), f.d1_side.values, 0)
        r2 = f.rsi2.values
        sig = np.where((trend == 1) & (r2 < 10), 1, np.where((trend == -1) & (r2 > 90), -1, 0))
        risk = 1.0 * f.atr4.values
    elif model == 'M2':
        rng = f.ok_any.values & (f.adx4.values < ADX_RANGE)
        up, lo_b = f.bb_up.values, f.bb_lo.values
        prev_up, prev_lo = np.r_[np.nan, up[:-1]], np.r_[np.nan, lo_b[:-1]]
        buy = rng & (prev_cl < prev_lo) & (cl > lo_b) & (cl > op)
        sell = rng & (prev_cl > prev_up) & (cl < up) & (cl < op)
        sig = np.where(buy, 1, np.where(sell, -1, 0))
        risk = 1.0 * f.atr4.values
    elif model == 'M3':
        ok = f.ok_any.values & (f.asia_n.values >= 5) & (f.hour.values >= 7) & (f.hour.values <= 15)
        ah, al = f.asia_hi.values, f.asia_lo.values
        buy = ok & (cl > ah) & (prev_cl <= ah)
        sell = ok & (cl < al) & (prev_cl >= al)
        sig = np.where(buy, 1, np.where(sell, -1, 0))
        risk = np.abs(cl - (ah + al) / 2)
    elif model == 'M4':
        ok = f.ok_any.values & (f.hour.values >= 7) & (f.hour.values <= 17)
        pdh, pdl = f.pdh.values, f.pdl.values
        sell = ok & (hi > pdh) & (cl < pdh) & (cl < op)
        buy = ok & (lo < pdl) & (cl > pdl) & (cl > op)
        sig = np.where(buy, 1, np.where(sell, -1, 0))
        risk = np.where(sell, hi + 0.2 * atr1 - cl, cl - (lo - 0.2 * atr1))
    if model in ('M3', 'M4'):                       # SL ต้องอยู่ในช่วงที่รับได้ ไม่งั้นข้าม
        bad = (risk < MIN_RISK_ATR1 * atr1) | (risk > MAX_RISK_ATR1 * atr1) | ~np.isfinite(risk)
        sig = np.where(bad, 0, sig)
    return sig.astype(int), risk


def run_model(symbol, d1, h4, h1, model, tp_r, cache=None):
    if cache is None:
        cache = features(d1, h4, h1)
    f, h1p = cache
    sig, risk_arr = model_signals(f, model)
    intraday = model in ('M3', 'M4')
    max_hold = None if intraday else HOLD_H
    idx = h1p.index
    tns = idx.as_unit('ns').asi8
    ready = np.flatnonzero(f.ok_any.values)
    if len(ready) == 0:
        return pd.DataFrame()
    t0, t1 = idx[ready[0]], idx[-1]
    cut = t0 + (t1 - t0) * (1 - S.TEST_FRAC)
    trades, free, taken_days = [], 0, set()
    dummy_h4s, dummy_hc = np.zeros(len(idx)), np.zeros(len(idx), dtype=int)
    for i in np.flatnonzero(sig != 0):
        if i < free or i >= len(idx) - 1:
            continue
        if symbol == 'XAU/USD' and S.gold_closed(idx[i]):
            continue
        if symbol in S.FX and S.no_entry_time(symbol, idx[i] + pd.Timedelta(hours=1)):
            continue                                     # Forex: ไม่เข้าช่วงตัดรอบ/ชั่วโมงสุดท้ายวันศุกร์/ตลาดปิด
        if intraday and (idx[i].hour >= S.NO_ENTRY_FROM_HOUR or f.date.values[i] in taken_days):
            continue
        if (tns[i + 1] - tns[i]) > 6 * 3_600_000_000_000:
            continue                                     # ไม่เปิดไม้บนแท่งสุดท้ายก่อนตลาดหยุด
        s, risk = int(sig[i]), float(risk_arr[i])
        if not np.isfinite(risk) or risk <= 0:
            continue
        entry_px = float(f.close.values[i])
        j, px, why = S.simulate(h1p, i, s, risk, 'tp', dummy_h4s, dummy_hc, max_hold, tp_r, intraday)
        side = 'LONG' if s == 1 else 'SHORT'
        nights = S.rollover_units(symbol, idx[i] + pd.Timedelta(hours=1), idx[j] + pd.Timedelta(hours=1))
        swap_r = entry_px * S.SWAP_PCT_DAY[symbol][side] / 100 * nights / risk
        cost = S.SPREAD[symbol] / risk + swap_r
        r = s * (px - entry_px) / risk
        trades.append({'model': MODELS[model], 'tp': tp_r, 'symbol': symbol, 'time': idx[i],
                       'period': S.DEV if idx[i] < cut else S.TEST, 'dir': side, 'entry': entry_px,
                       'exit': float(px), 'risk': risk, 'exit_time': idx[j], 'result': why,
                       'days': (idx[j] - idx[i]).total_seconds() / 86400, 'nights': nights,
                       'R_gross': r, 'swap_R': swap_r, 'R_net': r - cost})
        free = j + 1
        if intraday:
            taken_days.add(f.date.values[i])
    return pd.DataFrame(trades)


def lab(data, symbols=None):
    """รันทุกสูตร x TP กับทุกสินทรัพย์ คืน (ตารางสรุป, รายการเทรดทั้งหมด)"""
    rows, allt = [], []
    for sym, frames in data.items():
        if symbols and sym not in symbols:
            continue
        cache = features(*frames)
        for m in MODELS:
            for tp in TP_LIST:
                t = run_model(sym, *frames, m, tp, cache=cache)
                if len(t):
                    allt.append(t)
                dev = t[t.period == S.DEV] if len(t) else t
                test = t[t.period == S.TEST] if len(t) else t
                sa = S.stats(t) if len(t) else {'เทรด': 0}
                rows.append({'สินทรัพย์': sym, 'สูตร': MODELS[m], 'TP': f'{tp:g}R', 'เทรด': sa.get('เทรด', 0),
                             'ชนะ %': sa.get('ชนะ %'), 'R/เทรด สุทธิ': sa.get('R/เทรด สุทธิ'),
                             'ช่วงเชื่อมั่น 95%': sa.get('ช่วงเชื่อมั่น 95%'), 'PF': sa.get('PF'),
                             'ถือเฉลี่ย (วัน)': sa.get('ถือเฉลี่ย (วัน)'),
                             'พัฒนา: R สุทธิ': S.stats(dev).get('R/เทรด สุทธิ') if len(dev) else None,
                             'ทดสอบ: R สุทธิ': S.stats(test).get('R/เทรด สุทธิ') if len(test) else None,
                             'ซื้อ/ขาย': f"{(t.dir == 'LONG').sum()}/{(t.dir == 'SHORT').sum()}" if len(t) else '0/0',
                             'ผล': S._verdict(dev, test) if len(t) else 'ไม่มีเทรด'})
    trades = pd.concat(allt, ignore_index=True) if allt else pd.DataFrame()
    return pd.DataFrame(rows), trades
