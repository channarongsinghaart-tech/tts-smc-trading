"""ระบบทอง SMA200 (ระบบหลัก): D1 + H4 อยู่ฝั่งเดียวกันของเส้น 200 แล้วเข้าเมื่อแท่ง H1 ปิดข้ามเส้น 200
SL 1.5 x ATR(H4) | TP 2R | ถือนานสุด 48 ชม. | ไม่เปิดไม้ทองช่วงพักตลาด 20:58-22:00 UTC และชั่วโมงสุดท้ายวันศุกร์

ระบบทดลอง (เทียบใน Backtest เท่านั้น): "สวนเส้น 200 + จุดกลับตัว"
  ราคาอยู่ใต้เส้น 200 -> รอจุดกลับตัวขึ้นแล้วซื้อ | อยู่เหนือเส้น -> รอจุดกลับตัวลงแล้วขาย
  จุดกลับตัว (H1): ทำจุดต่ำสุดใหม่ของ 10 แท่งภายใน 3 แท่งล่าสุด แล้วปิดแท่งเขียวทะลุ High แท่งก่อนหน้า (ฝั่งขายกลับกัน)
  SL เลยจุดกลับตัว 0.2 ATR(H1) (ต้องอยู่ระหว่าง 0.5-3 ATR) | TP 2R | ถือนานสุด 48 ชม.
  ใต้/เหนือเส้น วัด 2 แบบ: 'd1h4' = D1 และ H4 อยู่ฝั่งเดียวกัน | 'h1' = เส้น 200 ของ H1
"""
import math
import numpy as np
import pandas as pd
import m1_swing_scanner as core
import simple200 as S

TFS = ('D1', 'H4', 'H1', 'M15')
SL_ATR_H4 = S.SL_ATR_H4
TP_R = 2.0
MAX_HOLD_H = 48
FRESH_H1 = 2                     # สัญญาณจากแท่ง H1 ภายใน 2 แท่งล่าสุด ถือว่ายังสด
MAIN_FILTERS = {'no_break': True}
REV_LOOKBACK, REV_RECENT, REV_BUF_ATR = 10, 3, 0.2
REV_MIN_RISK, REV_MAX_RISK = 0.5, 3.0
REV_NAMES = {'d1h4': 'สวนเส้น 200 (D1+H4) + จุดกลับตัว H1', 'h1': 'สวนเส้น 200 (H1) + จุดกลับตัว H1'}
MAIN_NAME = 'ตามเทรนด์: H1 ข้ามเส้น 200 (ระบบหลัก)'


def no_entry_time(symbol, close_t):
    return S.no_entry_time(symbol, close_t)


def run_main(symbol, d1, h4, h1):
    return S.run(symbol, d1, h4, h1, 'cross', 'tp', max_hold=MAX_HOLD_H, tp_r=TP_R, filters=MAIN_FILTERS)


# ---------------- ระบบทดลอง: สวนเส้น 200 + จุดกลับตัว ----------------
def reversal_signals(d1p, h4p, h1p, mode='d1h4'):
    d1s, h4s, _, _ = S.align(d1p, h4p, h1p)
    o, h, l, c, atr1 = (h1p[k].values for k in ('open', 'high', 'low', 'close', 'ATR'))
    low_n = pd.Series(l).rolling(REV_LOOKBACK).min().values
    high_n = pd.Series(h).rolling(REV_LOOKBACK).max().values
    low_r = pd.Series(l).rolling(REV_RECENT).min().values
    high_r = pd.Series(h).rolling(REV_RECENT).max().values
    prev_h, prev_l = np.r_[np.nan, h[:-1]], np.r_[np.nan, l[:-1]]
    if mode == 'd1h4':
        below, above = (d1s == -1) & (h4s == -1), (d1s == 1) & (h4s == 1)
    else:
        below, above = h1p.side.values == -1, h1p.side.values == 1
    buy = below & (low_r <= low_n) & (c > prev_h) & (c > o)
    sell = above & (high_r >= high_n) & (c < prev_l) & (c < o)
    risk = np.where(buy, c - (low_n - REV_BUF_ATR * atr1), np.where(sell, (high_n + REV_BUF_ATR * atr1) - c, np.nan))
    sig = np.where(buy, 1, np.where(sell, -1, 0))
    bad = ~np.isfinite(risk) | (risk < REV_MIN_RISK * atr1) | (risk > REV_MAX_RISK * atr1)
    return np.where(bad, 0, sig).astype(int), risk


def run_reversal(symbol, d1, h4, h1, mode='d1h4', cut=None, start=None):
    d1p, h4p, h1p = S.prep(d1), S.prep(h4), S.prep(h1)
    sig, risk_arr = reversal_signals(d1p, h4p, h1p, mode)
    idx = h1p.index
    tns = idx.as_unit('ns').asi8
    if cut is None:
        d1s = S.align(d1p, h4p, h1p)[0]
        ready = np.flatnonzero(d1s != 0)
        if len(ready) == 0:
            return pd.DataFrame()
        t0, t1 = idx[ready[0]], idx[-1]
        cut = t0 + (t1 - t0) * (1 - S.TEST_FRAC)
        start = t0 if start is None else start
    z = np.zeros(len(idx))
    trades, free = [], 0
    for i in np.flatnonzero(sig != 0):
        if i < free or i >= len(idx) - 1 or (start is not None and idx[i] < start):
            continue
        close_t = idx[i] + pd.Timedelta(hours=1)
        if (symbol == 'XAU/USD' and S.gold_closed(idx[i])) or no_entry_time(symbol, close_t):
            continue
        if (tns[i + 1] - tns[i]) > 6 * 3_600_000_000_000:
            continue
        s, risk = int(sig[i]), float(risk_arr[i])
        entry = float(h1p.close.values[i])
        j, px, why = S.simulate(h1p, i, s, risk, 'tp', z, z, MAX_HOLD_H, TP_R, False)
        side = 'LONG' if s == 1 else 'SHORT'
        nights = S.rollover_units(symbol, close_t, idx[j] + pd.Timedelta(hours=1))
        swap_r = entry * S.SWAP_PCT_DAY[symbol][side] / 100 * nights / risk
        r = s * (px - entry) / risk
        trades.append({'symbol': symbol, 'time': idx[i], 'period': S.DEV if idx[i] < cut else S.TEST, 'dir': side,
                       'entry': entry, 'exit': float(px), 'risk': risk, 'exit_time': idx[j], 'result': why,
                       'days': (idx[j] - idx[i]).total_seconds() / 86400, 'nights': nights, 'R_gross': r,
                       'swap_R': swap_r, 'R_net': r - S.SPREAD[symbol] / risk - swap_r})
        free = j + 1
    return pd.DataFrame(trades)


def compare(symbol, frames):
    """เทียบระบบหลัก (ตามเทรนด์) กับระบบทดลองสวนเส้น 200 ทั้ง 2 แบบ บนข้อมูล H1 ชุดเดียวกัน
    คืน (ตาราง, {ชื่อ: รายการเทรด})"""
    d1, h4, h1 = frames
    main = run_main(symbol, d1, h4, h1)
    d1p, h4p, h1p = S.prep(d1), S.prep(h4), S.prep(h1)
    ready = np.flatnonzero(S.align(d1p, h4p, h1p)[0] != 0)
    t0, t1 = h1p.index[ready[0]], h1p.index[-1]
    cut = t0 + (t1 - t0) * (1 - S.TEST_FRAC)
    sets = {MAIN_NAME: main}
    for mode, name in REV_NAMES.items():
        sets[name] = run_reversal(symbol, d1, h4, h1, mode, cut=cut, start=t0)
    return pd.DataFrame([_row(k, v) for k, v in sets.items()]), sets


def _row(label, t):
    if t is None or len(t) == 0:
        return {'แบบ': label, 'เทรด': 0, 'ผล': 'ไม่มีเทรด'}
    sa = S.stats(t)
    dev, test = t[t.period == S.DEV], t[t.period == S.TEST]
    return {'แบบ': label, 'เทรด': sa['เทรด'], 'ชนะ %': sa['ชนะ %'], 'R/เทรด สุทธิ': sa['R/เทรด สุทธิ'],
            'ช่วงเชื่อมั่น 95%': sa['ช่วงเชื่อมั่น 95%'], 'PF': sa['PF'], 'R รวม': sa['R รวม'],
            'ขาดทุนสะสมสูงสุด R': sa['ขาดทุนสะสมสูงสุด R'], 'ถือเฉลี่ย (วัน)': sa['ถือเฉลี่ย (วัน)'],
            'ซื้อ/ขาย': f"{(t.dir == 'LONG').sum()}/{(t.dir == 'SHORT').sum()}",
            'พัฒนา: R สุทธิ': S.stats(dev).get('R/เทรด สุทธิ') if len(dev) else None,
            'ทดสอบ: R สุทธิ': S.stats(test).get('R/เทรด สุทธิ') if len(test) else None,
            'ผล': S._verdict(dev, test)}


# ---------------- สแกนสด (ระบบหลัก) ----------------
def live(symbol):
    d1 = core.fetch(symbol, '1day', 300)
    h4 = core.fetch(symbol, '4h', 300)
    h1 = core.fetch(symbol, '1h', 300)
    m15 = core.fetch(symbol, '15min', 300)
    d1p, h4p, h1p, m15p = S.prep(d1), S.prep(h4), S.prep(h1), S.prep(m15)
    d1s, h4s, atr4, _ = S.align(d1p, h4p, h1p)
    sig_arr = S.signals(h1p, d1s, h4s, 'cross')
    frames = {'D1': d1p, 'H4': h4p, 'H1': h1p, 'M15': m15p}
    state = {}
    for tf, fp in frames.items():
        x = fp.iloc[-1]
        enough = not np.isnan(x.SMA)
        state[tf] = {'side': int(x.side) if enough else 0, 'close': float(x.close),
                     'sma': float(x.SMA) if enough else float('nan'),
                     'dist_atr': float((x.close - x.SMA) / x.ATR) if enough and x.ATR > 0 else float('nan')}
    trend = int(d1s[-1]) if d1s[-1] == h4s[-1] else 0
    price = float(m15p.close.iloc[-1]) if m15p.index[-1] > h1p.index[-1] else float(h1p.close.iloc[-1])
    last_t = max(m15p.index[-1] + pd.Timedelta(minutes=15), h1p.index[-1] + pd.Timedelta(hours=1))
    risk = SL_ATR_H4 * float(atr4[-1])
    out = {'state': state, 'trend': trend, 'frames': frames, 'time': last_t, 'risk': risk, 'price': price,
           'signal': None, 'h1_close': h1p.index[-1] + pd.Timedelta(hours=1),
           'gold_closed': symbol == 'XAU/USD' and S.gold_closed(last_t - pd.Timedelta(minutes=1))}
    n = len(h1p)
    for i in range(n - 1, n - 1 - FRESH_H1, -1):
        if sig_arr[i] != 0 and not no_entry_time(symbol, h1p.index[i] + pd.Timedelta(hours=1)):
            s = int(sig_arr[i])
            entry = float(h1p.close.values[i])
            rk = SL_ATR_H4 * float(atr4[i])
            out['signal'] = {'dir': s, 'time': h1p.index[i] + pd.Timedelta(hours=1), 'entry': entry,
                             'sl': entry - s * rk, 'tp': entry + s * TP_R * rk, 'risk': rk,
                             'moved_R': s * (price - entry) / rk,
                             'close_by': h1p.index[i] + pd.Timedelta(hours=1 + MAX_HOLD_H)}
            break
    return out


def lot_size(risk_price, account_usd, risk_pct, contract=100):
    """จำนวน lot ที่เสี่ยงไม่เกิน risk_pct ของทุน (ทอง 1 lot = 100 ออนซ์)"""
    if account_usd <= 0 or risk_price <= 0:
        return None, None
    money = account_usd * risk_pct / 100
    lot = math.floor(money / (risk_price * contract) * 100) / 100
    return lot, money
