"""ระบบทอง SMA200: D1 + H4 อยู่ฝั่งเดียวกันของเส้น 200 แล้วเข้าเมื่อแท่งกรอบจุดเข้า (M30 หรือ H1) ปิดข้ามเส้น 200
SL 1.5 x ATR(H4) | TP 2R | ถือนานสุด 48 ชม. | ไม่เปิดไม้ทองช่วงพักตลาด 20:58-22:00 UTC และชั่วโมงสุดท้ายวันศุกร์
ตัวเลือก: require_h1 = ให้ H1 อยู่ฝั่งเดียวกันด้วย (ใช้กับจุดเข้า M30)
"""
import math
import numpy as np
import pandas as pd
import m1_swing_scanner as core
import simple200 as S

ENTRY_TFS = {'M30': ('30min', 30), 'H1': ('1h', 60)}
DEFAULT_ENTRY = 'M30'
SL_ATR_H4 = S.SL_ATR_H4
TP_R = 2.0
MAX_HOLD_H = 48
FRESH_BARS = 2
HOUR_NS = 3_600_000_000_000


def no_entry_time(symbol, close_t):
    return S.no_entry_time(symbol, close_t)


def name_of(entry, require_h1=False):
    return f'{entry} ข้ามเส้น 200' + (' + H1 ฝั่งเดียวกัน' if require_h1 else '')


def load_history(symbol, entry='M30', pages=4):
    iv, mins = ENTRY_TFS[entry]
    e = S.history(symbol, iv, 5000, pages)
    days = (e.index[-1] - e.index[0]).days + 2
    h1 = e if entry == 'H1' else S.history(symbol, '1h', 5000, max(1, math.ceil((days * 24 + 260) / 5000)))
    h4 = S.history(symbol, '4h', 5000, max(1, math.ceil((days * 6 + 260) / 5000)))
    d1 = S.history(symbol, '1day', min(5000, days + 300))
    return d1, h4, h1, e


def _asof(frame_p, offset, target):
    return np.searchsorted((frame_p.index.tz_localize(None) + offset).values, target, side='right')


def build(d1, h4, h1, e, mins, require_h1=False):
    """สถานะ D1/H4/H1 ณ เวลาปิดของแต่ละแท่งกรอบจุดเข้า และสัญญาณ (+1 ซื้อ / -1 ขาย / 0)"""
    d1p, h4p, h1p, ep = S.prep(d1), S.prep(h4), S.prep(h1), S.prep(e)
    target = (ep.index.tz_localize(None) + pd.Timedelta(minutes=mins)).values
    side = {}
    for name, fp, off in (('D1', d1p, pd.Timedelta(days=1)), ('H4', h4p, pd.Timedelta(hours=4)),
                          ('H1', h1p, pd.Timedelta(hours=1))):
        cnt = _asof(fp, off, target)
        side[name] = np.where(cnt >= S.SMA_N, fp.side.values[np.maximum(cnt - 1, 0)], 0)
        if name == 'H4':
            atr4 = h4p.ATR.values[np.maximum(cnt - 1, 0)]
    trend = np.where((side['D1'] == side['H4']) & (side['D1'] != 0), side['D1'], 0)
    if require_h1:
        trend = np.where(side['H1'] == trend, trend, 0)
    se = ep.side.values
    prev = np.r_[0, se[:-1]]
    sig = np.where((trend != 0) & (se == trend) & (prev == -trend), trend, 0).astype(int)
    return {'ep': ep, 'side': side, 'trend': trend, 'sig': sig, 'atr4': atr4, 'mins': mins,
            'frames': {'D1': d1p, 'H4': h4p, 'H1': h1p}}


def run_entry(symbol, d1, h4, h1, e, entry='M30', require_h1=False, cut=None, start=None):
    mins = ENTRY_TFS[entry][1]
    b = build(d1, h4, h1, e, mins, require_h1)
    ep, sig, atr4 = b['ep'], b['sig'], b['atr4']
    idx = ep.index
    tns = idx.as_unit('ns').asi8
    bar = pd.Timedelta(minutes=mins)
    ready = np.flatnonzero((b['side']['D1'] != 0) & (b['side']['H4'] != 0))
    if len(ready) == 0:
        return pd.DataFrame()
    if cut is None:
        t0, t1 = idx[ready[0]], idx[-1]
        cut = t0 + (t1 - t0) * (1 - S.TEST_FRAC)
    z = np.zeros(len(idx))
    trades, free = [], 0
    for i in np.flatnonzero(sig != 0):
        if i < free or i >= len(idx) - 1 or (start is not None and idx[i] < start):
            continue
        if symbol == 'XAU/USD' and S.gold_closed(idx[i]):
            continue
        if (tns[i + 1] - tns[i]) > 6 * HOUR_NS or no_entry_time(symbol, idx[i] + bar):
            continue
        s = int(sig[i])
        risk = SL_ATR_H4 * max(float(atr4[i]), 1e-9)
        entry_px = float(ep.close.values[i])
        j, px, why = S.simulate(ep, i, s, risk, 'tp', z, z, MAX_HOLD_H, TP_R, False)
        side = 'LONG' if s == 1 else 'SHORT'
        nights = S.rollover_units(symbol, idx[i] + bar, idx[j] + bar)
        swap_r = entry_px * S.SWAP_PCT_DAY[symbol][side] / 100 * nights / risk
        r = s * (px - entry_px) / risk
        trades.append({'symbol': symbol, 'time': idx[i], 'period': S.DEV if idx[i] < cut else S.TEST,
                       'dir': side, 'entry': entry_px, 'exit': float(px), 'risk': risk, 'exit_time': idx[j],
                       'result': why, 'days': (idx[j] - idx[i]).total_seconds() / 86400, 'nights': nights,
                       'R_gross': r, 'swap_R': swap_r, 'R_net': r - S.SPREAD[symbol] / risk - swap_r})
        free = j + 1
    return pd.DataFrame(trades)


def compare(symbol, frames):
    """เทียบจุดเข้า H1 กับ M30 (และ M30 + H1 ฝั่งเดียวกัน) ในช่วงเวลาเดียวกัน (ช่วงที่มีข้อมูล M30)
    frames = load_history(symbol, 'M30') | คืน (ตาราง, {ชื่อ: รายการเทรด})"""
    d1, h4, h1, m30 = frames
    t0, t1 = m30.index[0] + pd.Timedelta(days=3), m30.index[-1]
    cut = t0 + (t1 - t0) * (1 - S.TEST_FRAC)
    sets = {}
    for entry, req in (('H1', False), ('M30', False), ('M30', True)):
        e = h1 if entry == 'H1' else m30
        t = run_entry(symbol, d1, h4, h1, e, entry, req, cut=cut, start=t0)
        sets[name_of(entry, req) + (' (ระบบเดิม)' if entry == 'H1' else '')] = t
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


# ---------------- สแกนสด ----------------
def live(symbol, entry='M30', require_h1=False):
    d1 = core.fetch(symbol, '1day', 300)
    h4 = core.fetch(symbol, '4h', 300)
    h1 = core.fetch(symbol, '1h', 300)
    m30 = core.fetch(symbol, '30min', 300)
    e = m30 if entry == 'M30' else h1
    mins = ENTRY_TFS[entry][1]
    b = build(d1, h4, h1, e, mins, require_h1)
    ep = b['ep']
    frames = {'D1': b['frames']['D1'], 'H4': b['frames']['H4'], 'H1': b['frames']['H1'],
              'M30': S.prep(m30) if entry != 'M30' else ep}
    state = {}
    for tf, fp in frames.items():
        x = fp.iloc[-1]
        enough = not np.isnan(x.SMA)
        state[tf] = {'side': int(x.side) if enough else 0, 'close': float(x.close),
                     'sma': float(x.SMA) if enough else float('nan'),
                     'dist_atr': float((x.close - x.SMA) / x.ATR) if enough and x.ATR > 0 else float('nan')}
    bar = pd.Timedelta(minutes=mins)
    m30p = frames['M30']
    price = float(m30p.close.iloc[-1])
    last_t = m30p.index[-1] + pd.Timedelta(minutes=30)
    out = {'state': state, 'trend': int(b['trend'][-1]), 'frames': frames, 'time': last_t, 'price': price,
           'risk': SL_ATR_H4 * float(b['atr4'][-1]), 'signal': None, 'entry': entry,
           'entry_close': ep.index[-1] + bar, 'require_h1': require_h1,
           'd1h4': int(b['side']['D1'][-1]) if b['side']['D1'][-1] == b['side']['H4'][-1] else 0,
           'gold_closed': symbol == 'XAU/USD' and S.gold_closed(last_t - pd.Timedelta(minutes=1))}
    n = len(ep)
    for i in range(n - 1, n - 1 - FRESH_BARS, -1):
        if b['sig'][i] != 0 and not no_entry_time(symbol, ep.index[i] + bar):
            s = int(b['sig'][i])
            px = float(ep.close.values[i])
            rk = SL_ATR_H4 * float(b['atr4'][i])
            out['signal'] = {'dir': s, 'time': ep.index[i] + bar, 'entry': px, 'sl': px - s * rk,
                             'tp': px + s * TP_R * rk, 'risk': rk, 'moved_R': s * (price - px) / rk,
                             'close_by': ep.index[i] + bar + pd.Timedelta(hours=MAX_HOLD_H)}
            break
    return out


def lot_size(risk_price, account_usd, risk_pct, contract=100):
    """คืน (lot, งบความเสี่ยง USD, ความเสี่ยงจริง USD) ทอง 1 lot = 100 ออนซ์"""
    if account_usd <= 0 or risk_price <= 0:
        return None, None, None
    money = account_usd * risk_pct / 100
    lot = math.floor(money / (risk_price * contract) * 100) / 100
    return lot, money, max(lot, 0.01) * contract * risk_price
