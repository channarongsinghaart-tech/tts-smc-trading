"""ระบบทอง SMA200 หลายกรอบ: D1 > H4 > H1 แล้วเข้าที่ M15

ทิศ      : D1 และ H4 อยู่ฝั่งเดียวกันของ SMA200 + H1 ต้องอยู่ฝั่งเดียวกันด้วย
จุดเข้า   : แท่ง M15 ปิดข้ามเส้น SMA200(M15) กลับมาฝั่งเดียวกับกรอบใหญ่ทั้งหมด
SL / TP  : SL 1.5 x ATR(H4) | TP 2R | ถือนานสุด 48 ชม. (ปิดก่อนตลาดหยุดสุดสัปดาห์)
ใช้เฉพาะแท่งที่ปิดแล้ว | ทองไม่เข้าตอนตลาดปิด/พัก (20:58-22:00 UTC) และชั่วโมงสุดท้ายวันศุกร์ | หักสเปรดและค่าข้ามคืนตามจริง
"""
import math
import numpy as np
import pandas as pd
import m1_swing_scanner as core
import simple200 as S

TFS = ('D1', 'H4', 'H1', 'M15')
SL_ATR_H4 = 1.5
TP_R = 2.0
MAX_HOLD_H = 48
FRESH_BARS = 4                 # สัญญาณ M15 ที่เกิดภายใน 4 แท่งล่าสุด (1 ชม.) ถือว่ายังสด
HOUR_NS = 3_600_000_000_000


def load_history(symbol, m15_pages=4):
    m15 = S.history(symbol, '15min', 5000, m15_pages)
    days = (m15.index[-1] - m15.index[0]).days + 2
    h1 = S.history(symbol, '1h', 5000, max(1, math.ceil((days * 24 + 260) / 5000)))
    h4 = S.history(symbol, '4h', 5000, max(1, math.ceil((days * 6 + 260) / 5000)))
    d1 = S.history(symbol, '1day', min(5000, days + 300))
    return d1, h4, h1, m15


def no_entry_time(symbol, close_t):
    """ช่วงที่เปิดไม้ทองจริงไม่ได้/ไม่ควรเปิด: พักตลาด 20:58-22:00 UTC, ชั่วโมงสุดท้ายก่อนปิดวันศุกร์, ตลาดปิด"""
    if symbol != 'XAU/USD':
        return False
    hm = close_t.hour * 60 + close_t.minute
    if 20 * 60 + 58 <= hm < 22 * 60:
        return True
    if close_t.dayofweek == 4 and hm >= 20 * 60:
        return True
    return S.gold_closed(close_t - pd.Timedelta(minutes=15))


def _asof(frame_p, offset, target):
    closes = (frame_p.index.tz_localize(None) + offset).values
    cnt = np.searchsorted(closes, target, side='right')
    return cnt


def build(d1, h4, h1, m15):
    """คำนวณสถานะทุกกรอบ ณ เวลาปิดของแต่ละแท่ง M15 และสัญญาณเข้า"""
    d1p, h4p, h1p, m15p = S.prep(d1), S.prep(h4), S.prep(h1), S.prep(m15)
    target = (m15p.index.tz_localize(None) + pd.Timedelta(minutes=15)).values
    side = {}
    for name, fp, off in (('D1', d1p, pd.Timedelta(days=1)), ('H4', h4p, pd.Timedelta(hours=4)),
                          ('H1', h1p, pd.Timedelta(hours=1))):
        cnt = _asof(fp, off, target)
        side[name] = np.where(cnt >= S.SMA_N, fp.side.values[np.maximum(cnt - 1, 0)], 0)
        if name == 'H4':
            atr4 = h4p.ATR.values[np.maximum(cnt - 1, 0)]
    trend = np.where((side['D1'] == side['H4']) & (side['D1'] != 0) & (side['H1'] == side['D1']), side['D1'], 0)
    s15 = m15p.side.values
    prev = np.r_[0, s15[:-1]]
    sig = np.where((trend != 0) & (s15 == trend) & (prev == -trend), trend, 0).astype(int)
    return {'m15p': m15p, 'side': side, 'trend': trend, 'sig': sig, 'atr4': atr4}


def run(symbol, d1, h4, h1, m15, tp_r=TP_R, max_hold=MAX_HOLD_H, cut=None):
    b = build(d1, h4, h1, m15)
    m15p, sig, atr4 = b['m15p'], b['sig'], b['atr4']
    idx = m15p.index
    tns = idx.as_unit('ns').asi8
    ready = np.flatnonzero((b['side']['D1'] != 0) & (b['side']['H4'] != 0) & (b['side']['H1'] != 0))
    if len(ready) == 0:
        return pd.DataFrame()
    if cut is None:
        t0, t1 = idx[ready[0]], idx[-1]
        cut = t0 + (t1 - t0) * (1 - S.TEST_FRAC)
    z = np.zeros(len(idx))
    trades, free = [], 0
    for i in np.flatnonzero(sig != 0):
        if i < free or i >= len(idx) - 1:
            continue
        if symbol == 'XAU/USD' and S.gold_closed(idx[i]):
            continue
        if no_entry_time(symbol, idx[i] + pd.Timedelta(minutes=15)) or (tns[i + 1] - tns[i]) > 6 * HOUR_NS:
            continue
        s = int(sig[i])
        risk = SL_ATR_H4 * max(float(atr4[i]), 1e-9)
        entry_px = float(m15p.close.values[i])
        j, px, why = S.simulate(m15p, i, s, risk, 'tp', z, z, max_hold, tp_r, False)
        side = 'LONG' if s == 1 else 'SHORT'
        t_in, t_out = idx[i] + pd.Timedelta(minutes=15), idx[j] + pd.Timedelta(minutes=15)
        nights = S.rollover_units(symbol, t_in, t_out)
        swap_r = entry_px * S.SWAP_PCT_DAY[symbol][side] / 100 * nights / risk
        r = s * (px - entry_px) / risk
        trades.append({'symbol': symbol, 'time': idx[i], 'period': S.DEV if idx[i] < cut else S.TEST,
                       'dir': side, 'entry': entry_px, 'exit': float(px), 'risk': risk, 'exit_time': idx[j],
                       'result': why, 'days': (idx[j] - idx[i]).total_seconds() / 86400, 'nights': nights,
                       'R_gross': r, 'swap_R': swap_r, 'R_net': r - S.SPREAD[symbol] / risk - swap_r})
        free = j + 1
    return pd.DataFrame(trades)


def compare(symbol, frames):
    """เทียบจุดเข้า H1 (ระบบเดิม) กับ M15 (ระบบใหม่) ในช่วงเวลาเดียวกัน (ช่วงที่มีข้อมูล M15)
    คืน (ตาราง, {ชื่อแบบ: รายการเทรด})"""
    d1, h4, h1, m15 = frames
    t0, t1 = m15.index[0] + pd.Timedelta(days=3), m15.index[-1]
    cut = t0 + (t1 - t0) * (1 - S.TEST_FRAC)
    rows, sets = [], {}
    for label, kind in (('H1 ข้ามเส้น 200 (ระบบเดิม)', 'H1'), ('M15 ข้ามเส้น 200 (ระบบใหม่)', 'M15')):
        if kind == 'H1':
            t = S.run(symbol, d1, h4, h1, 'cross', 'tp', max_hold=MAX_HOLD_H, tp_r=TP_R)
        else:
            t = run(symbol, d1, h4, h1, m15, cut=cut)
        if len(t):
            t = t[t.time >= t0].copy()
            t['period'] = np.where(t.time < cut, S.DEV, S.TEST)
        rows.append(_row(label, t))
        sets[label] = t
    return pd.DataFrame(rows), sets


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
def live(symbol):
    d1 = core.fetch(symbol, '1day', 300)
    h4 = core.fetch(symbol, '4h', 300)
    h1 = core.fetch(symbol, '1h', 300)
    m15 = core.fetch(symbol, '15min', 300)
    b = build(d1, h4, h1, m15)
    m15p = b['m15p']
    frames = {'D1': S.prep(d1), 'H4': S.prep(h4), 'H1': S.prep(h1), 'M15': m15p}
    state = {}
    for tf, fp in frames.items():
        x = fp.iloc[-1] if len(fp) else None
        enough = x is not None and not np.isnan(x.SMA)
        state[tf] = {'side': int(x.side) if enough else 0, 'close': float(x.close) if x is not None else float('nan'),
                     'sma': float(x.SMA) if enough else float('nan'),
                     'dist_atr': float((x.close - x.SMA) / x.ATR) if enough and x.ATR > 0 else float('nan'),
                     'time': fp.index[-1] if len(fp) else None}
    trend = int(b['trend'][-1])
    base = len(m15p) - FRESH_BARS
    recent = [k for k in np.flatnonzero(b['sig'][-FRESH_BARS:] != 0)
              if not no_entry_time(symbol, m15p.index[base + k] + pd.Timedelta(minutes=15))]
    sig_i = base + int(recent[-1]) if len(recent) else None
    risk = SL_ATR_H4 * float(b['atr4'][-1])
    out = {'state': state, 'trend': trend, 'frames': frames, 'time': m15p.index[-1], 'risk': risk,
           'price': float(m15p.close.iloc[-1]), 'signal': None,
           'gold_closed': symbol == 'XAU/USD' and S.gold_closed(m15p.index[-1])}
    if sig_i is not None:
        s = int(b['sig'][sig_i])
        entry = float(m15p.close.values[sig_i])
        out['signal'] = {'dir': s, 'time': m15p.index[sig_i], 'entry': entry, 'sl': entry - s * risk,
                         'tp': entry + s * TP_R * risk, 'moved_R': s * (out['price'] - entry) / risk,
                         'close_by': m15p.index[sig_i] + pd.Timedelta(minutes=15) + pd.Timedelta(hours=MAX_HOLD_H)}
    return out


def lot_size(risk_price, account_usd, risk_pct, contract=100):
    """จำนวน lot ที่เสี่ยงไม่เกิน risk_pct ของทุน (ทอง 1 lot = 100 ออนซ์)"""
    if account_usd <= 0 or risk_price <= 0:
        return None, None
    money = account_usd * risk_pct / 100
    lot = math.floor(money / (risk_price * contract) * 100) / 100
    return lot, money
