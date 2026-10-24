"""BTC วิเคราะห์แบบ Market Structure + Support/Resistance + Liquidity & Stop Run + Multi-Timeframe

นิยาม (คำนวณจากแท่งที่ปิดแล้วเท่านั้น จุดสวิงยืนยันช้า n แท่ง จึงไม่แอบดูอนาคต)
- จุดสวิง      : High/Low ที่สูง/ต่ำกว่า n แท่งซ้ายและขวา (H1 n=3, H4 n=2, D1 n=2)
- โครงสร้าง    : HH+HL = ขาขึ้น (+1) | LH+LL = ขาลง (-1) | นอกนั้นไซด์เวย์ (0)
- BOS / CHoCH : ปิดทะลุจุดสวิงล่าสุดตามเทรนด์ / สวนเทรนด์
- แนวรับแนวต้าน: รวมจุดสวิง H4/D1 ที่ใกล้กันเป็นโซน + High/Low เมื่อวานและสัปดาห์ก่อน
- Liquidity   : จุดสวิงที่สูง/ต่ำเท่ากัน 2 ครั้งขึ้นไป | Stop run = ไส้ทะลุจุดสวิงแล้วปิดกลับ

กฎจุดเข้า (ซื้อ; ขายกลับกัน)
1) H4 ขาขึ้น และ D1 ไม่ใช่ขาลง
2) H1 กวาดจุดสวิงต่ำล่าสุด: Low ต่ำกว่าแต่ปิดกลับเหนือจุดนั้น
3) ภายใน 12 แท่ง H1 ปิดเหนือจุดสวิงสูงล่าสุด (CHoCH) โดยไม่ทำ Low ใหม่ต่ำกว่าไส้ที่กวาด -> เข้าตอนแท่งนั้นปิด
4) (แบบมีแนวรับ) จุดที่ถูกกวาดอยู่ห่างแนวรับ H4 / Low เมื่อวาน / Low สัปดาห์ก่อน ไม่เกิน 1 ATR(H4)
SL ใต้ไส้ที่กวาด 0.2 ATR(H1) (ต้องอยู่ระหว่าง 0.5-4 ATR) | TP 2R | ถือนานสุด 48 ชม.
"""
import math
import numpy as np
import pandas as pd
import m1_swing_scanner as core
import simple200 as S

SYMBOL = 'BTC/USD'
PIV = {'H1': 3, 'H4': 2, 'D1': 2}
CHOCH_WINDOW = 12
SL_BUF_ATR = 0.2
MIN_RISK_ATR, MAX_RISK_ATR = 0.5, 4.0
SR_ATR4 = 1.0
TP_R = 2.0
MAX_HOLD_H = 48
FRESH_BARS = 2
NAMES = {False: 'SMC: MTF + กวาดสต็อป + CHoCH', True: 'SMC + แนวรับ/แนวต้าน (S/R)'}
OFFSET = {'H1': pd.Timedelta(hours=1), 'H4': pd.Timedelta(hours=4), 'D1': pd.Timedelta(days=1)}


# ---------------- โครงสร้างตลาด ----------------
def pivots(df, n):
    h, l = df.high.values, df.low.values
    N = len(df)
    ph, pl = np.zeros(N, bool), np.zeros(N, bool)
    for k in range(n, N - n):
        if h[k] > h[k - n:k].max() and h[k] >= h[k + 1:k + n + 1].max():
            ph[k] = True
        if l[k] < l[k - n:k].min() and l[k] <= l[k + 1:k + n + 1].min():
            pl[k] = True
    return ph, pl


def swings(df, n):
    """จุดสวิงที่ยืนยันแล้ว ณ แต่ละแท่ง: ระดับล่าสุด/ก่อนหน้า และโครงสร้าง (+1/-1/0)"""
    ph, pl = pivots(df, n)
    h, l = df.high.values, df.low.values
    N = len(df)
    out = {k: np.full(N, np.nan) for k in ('sh1', 'sh2', 'sl1', 'sl2')}
    hi_idx, lo_idx = np.flatnonzero(ph), np.flatnonzero(pl)
    hp = lp = 0
    lh, ll = [], []
    for i in range(N):
        while hp < len(hi_idx) and hi_idx[hp] + n <= i:
            lh.append(h[hi_idx[hp]]); hp += 1
        while lp < len(lo_idx) and lo_idx[lp] + n <= i:
            ll.append(l[lo_idx[lp]]); lp += 1
        if lh:
            out['sh1'][i] = lh[-1]
            if len(lh) > 1:
                out['sh2'][i] = lh[-2]
        if ll:
            out['sl1'][i] = ll[-1]
            if len(ll) > 1:
                out['sl2'][i] = ll[-2]
    st = np.where((out['sh1'] > out['sh2']) & (out['sl1'] > out['sl2']), 1,
                  np.where((out['sh1'] < out['sh2']) & (out['sl1'] < out['sl2']), -1, 0))
    out['struct'] = st
    out['ph'], out['pl'] = ph, pl
    return out


def asof_index(frame, tf, target):
    """จำนวนแท่ง tf ที่ปิดแล้ว ณ เวลา target (numpy datetime64 ไม่มี tz)"""
    return np.searchsorted((frame.index.tz_localize(None) + OFFSET[tf]).values, target, side='right')


def _support_levels(h4p, sw4, d1p, kind):
    """รายการแนวรับ (kind='low') หรือแนวต้าน ('high') ของ H4 พร้อมเวลาที่ยืนยัน"""
    n = PIV['H4']
    if kind == 'low':
        idx = np.flatnonzero(sw4['pl'])
        lv = h4p.low.values[idx]
    else:
        idx = np.flatnonzero(sw4['ph'])
        lv = h4p.high.values[idx]
    conf = (h4p.index[np.minimum(idx + n, len(h4p) - 1)].tz_localize(None) + OFFSET['H4']).values
    return lv, conf


# ---------------- สัญญาณ ----------------
def signals(d1, h4, h1, sr=False):
    d1p, h4p, h1p = S.prep(d1), S.prep(h4), S.prep(h1)
    sw1, sw4, swd = swings(h1p, PIV['H1']), swings(h4p, PIV['H4']), swings(d1p, PIV['D1'])
    target = (h1p.index.tz_localize(None) + OFFSET['H1']).values
    c4, cd = asof_index(h4p, 'H4', target), asof_index(d1p, 'D1', target)
    s4 = np.where(c4 > 0, sw4['struct'][np.maximum(c4 - 1, 0)], 0)
    sd = np.where(cd > 0, swd['struct'][np.maximum(cd - 1, 0)], 0)
    atr4 = h4p.ATR.values[np.maximum(c4 - 1, 0)]
    pdh, pdl = d1p.high.values[np.maximum(cd - 1, 0)], d1p.low.values[np.maximum(cd - 1, 0)]
    wk = d1p[['high', 'low']].resample('W-SUN', label='right', closed='right').agg({'high': 'max', 'low': 'min'})
    cw = np.searchsorted((wk.index.tz_localize(None) + pd.Timedelta(days=1)).values, target, side='right')
    pwh, pwl = wk.high.values[np.maximum(cw - 1, 0)], wk.low.values[np.maximum(cw - 1, 0)]
    sup_lv, sup_t = _support_levels(h4p, sw4, d1p, 'low')
    res_lv, res_t = _support_levels(h4p, sw4, d1p, 'high')
    o, h, l, c, a1 = (h1p[k].values for k in ('open', 'high', 'low', 'close', 'ATR'))
    N = len(h1p)
    sig, risk, info = np.zeros(N, int), np.full(N, np.nan), [None] * N
    pend = {1: None, -1: None}

    def near_sr(level, i, s):
        tol = SR_ATR4 * atr4[i]
        ref = [pdl[i], pwl[i]] if s == 1 else [pdh[i], pwh[i]]
        lv, tt = (sup_lv, sup_t) if s == 1 else (res_lv, res_t)
        ok_lv = lv[tt <= target[i]][-20:]
        return any(abs(level - x) <= tol for x in list(ok_lv) + ref)

    for i in range(1, N):
        for s in (1, -1):
            lvl = sw1['sl1'][i - 1] if s == 1 else sw1['sh1'][i - 1]       # จุดสวิงที่รู้ก่อนแท่งนี้
            if np.isfinite(lvl):
                swept = (l[i] < lvl and c[i] > lvl) if s == 1 else (h[i] > lvl and c[i] < lvl)
                if swept and (not sr or near_sr(lvl, i, s)):
                    trig = sw1['sh1'][i - 1] if s == 1 else sw1['sl1'][i - 1]
                    if np.isfinite(trig):
                        pend[s] = {'j': i, 'level': lvl, 'trig': trig, 'ext': l[i] if s == 1 else h[i]}
            p = pend[s]
            if p is None:
                continue
            if i - p['j'] > CHOCH_WINDOW:
                pend[s] = None
                continue
            if i == p['j']:
                continue
            if (s == 1 and l[i] < p['ext']) or (s == -1 and h[i] > p['ext']):
                pend[s] = None                              # ทำ Low/High ใหม่เลยไส้ที่กวาด = setup ล้ม
                continue
            bias = (s4[i] == 1 and sd[i] != -1) if s == 1 else (s4[i] == -1 and sd[i] != 1)
            broke = c[i] > p['trig'] if s == 1 else c[i] < p['trig']
            if broke and bias:
                sl = p['ext'] - s * SL_BUF_ATR * a1[i]
                rk = s * (c[i] - sl)
                if MIN_RISK_ATR * a1[i] <= rk <= MAX_RISK_ATR * a1[i] and sig[i] == 0:
                    sig[i], risk[i] = s, rk
                    info[i] = {'sweep_time': h1p.index[p['j']], 'swept_level': p['level'], 'choch_level': p['trig'],
                               'sweep_ext': p['ext']}
                pend[s] = None
    return {'sig': sig, 'risk': risk, 'info': info, 'h1p': h1p, 'h4p': h4p, 'd1p': d1p, 'sw1': sw1, 'sw4': sw4,
            'swd': swd, 's4': s4, 'sd': sd, 'atr4': atr4, 'pdh': pdh, 'pdl': pdl, 'pwh': pwh, 'pwl': pwl}


# ---------------- Backtest ----------------
def run(d1, h4, h1, sr=False, symbol=SYMBOL):
    b = signals(d1, h4, h1, sr)
    h1p, sig, risk_arr = b['h1p'], b['sig'], b['risk']
    idx = h1p.index
    ready = np.flatnonzero((b['s4'] != 0) | (b['sd'] != 0))
    if len(ready) == 0:
        return pd.DataFrame()
    t0, t1 = idx[ready[0]], idx[-1]
    cut = t0 + (t1 - t0) * (1 - S.TEST_FRAC)
    z = np.zeros(len(idx))
    tns = idx.as_unit('ns').asi8
    trades, free = [], 0
    for i in np.flatnonzero(sig != 0):
        if i < free or i >= len(idx) - 1 or (tns[i + 1] - tns[i]) > 6 * 3_600_000_000_000:
            continue
        s, rk = int(sig[i]), float(risk_arr[i])
        entry = float(h1p.close.values[i])
        j, px, why = S.simulate(h1p, i, s, rk, 'tp', z, z, MAX_HOLD_H, TP_R, False)
        side = 'LONG' if s == 1 else 'SHORT'
        nights = S.rollover_units(symbol, idx[i] + OFFSET['H1'], idx[j] + OFFSET['H1'])
        swap_r = entry * S.SWAP_PCT_DAY[symbol][side] / 100 * nights / rk
        r = s * (px - entry) / rk
        trades.append({'symbol': symbol, 'time': idx[i], 'period': S.DEV if idx[i] < cut else S.TEST, 'dir': side,
                       'entry': entry, 'exit': float(px), 'risk': rk, 'exit_time': idx[j], 'result': why,
                       'days': (idx[j] - idx[i]).total_seconds() / 86400, 'nights': nights, 'R_gross': r,
                       'swap_R': swap_r, 'R_net': r - S.SPREAD[symbol] / rk - swap_r,
                       'weekday': int(idx[i].dayofweek), 'hour': int(idx[i].hour)})
        free = j + 1
    return pd.DataFrame(trades)


def compare(frames, symbol=SYMBOL):
    d1, h4, h1 = frames
    sets = {NAMES[sr]: run(d1, h4, h1, sr, symbol) for sr in (False, True)}
    rows = []
    for k, t in sets.items():
        if len(t) == 0:
            rows.append({'แบบ': k, 'เทรด': 0, 'ผล': 'ไม่มีเทรด'})
            continue
        sa = S.stats(t)
        dev, test = t[t.period == S.DEV], t[t.period == S.TEST]
        rows.append({'แบบ': k, 'เทรด': sa['เทรด'], 'ชนะ %': sa['ชนะ %'], 'R/เทรด สุทธิ': sa['R/เทรด สุทธิ'],
                     'ช่วงเชื่อมั่น 95%': sa['ช่วงเชื่อมั่น 95%'], 'PF': sa['PF'], 'R รวม': sa['R รวม'],
                     'ขาดทุนสะสมสูงสุด R': sa['ขาดทุนสะสมสูงสุด R'], 'ถือเฉลี่ย (วัน)': sa['ถือเฉลี่ย (วัน)'],
                     'ซื้อ/ขาย': f"{(t.dir == 'LONG').sum()}/{(t.dir == 'SHORT').sum()}",
                     'พัฒนา: R สุทธิ': S.stats(dev).get('R/เทรด สุทธิ') if len(dev) else None,
                     'ทดสอบ: R สุทธิ': S.stats(test).get('R/เทรด สุทธิ') if len(test) else None,
                     'ผล': S._verdict(dev, test)})
    return pd.DataFrame(rows), sets


# ---------------- วิเคราะห์สด ----------------
def zones(levels, tol):
    """รวมระดับราคาที่ใกล้กันเป็นโซน: [(ล่าง, บน, จำนวนครั้ง)]"""
    out = []
    for x in sorted(levels):
        if out and x - out[-1][1] <= tol:
            out[-1] = (out[-1][0], x, out[-1][2] + 1)
        else:
            out.append((x, x, 1))
    return out


def swing_labels(df, sw):
    """ป้าย HH/HL/LH/LL ของจุดสวิงที่ยืนยันแล้ว"""
    lab = []
    prev_h = prev_l = None
    n_conf = len(df)
    for k in range(len(df)):
        if sw['ph'][k]:
            v = df.high.values[k]
            if prev_h is not None:
                lab.append((df.index[k], v, 'HH' if v > prev_h else 'LH', 'high'))
            prev_h = v
        if sw['pl'][k]:
            v = df.low.values[k]
            if prev_l is not None:
                lab.append((df.index[k], v, 'HL' if v > prev_l else 'LL', 'low'))
            prev_l = v
    return lab


def events(df, sw, last=6):
    """BOS / CHoCH ล่าสุด: ปิดทะลุจุดสวิงล่าสุด (เทียบกับโครงสร้าง ณ ตอนนั้น)"""
    ev = []
    c = df.close.values
    for i in range(1, len(df)):
        sh, sl, st = sw['sh1'][i - 1], sw['sl1'][i - 1], sw['struct'][i - 1]
        if np.isfinite(sh) and c[i] > sh and c[i - 1] <= sh:
            ev.append((df.index[i], sh, 'BOS ↑' if st == 1 else 'CHoCH ↑'))
        if np.isfinite(sl) and c[i] < sl and c[i - 1] >= sl:
            ev.append((df.index[i], sl, 'BOS ↓' if st == -1 else 'CHoCH ↓'))
    return ev[-last:]


def liquidity_pools(df, sw, tol, lookback=120):
    """จุดสวิงสูง/ต่ำที่เท่ากัน (ห่างไม่เกิน tol) และยังไม่ถูกกวาด"""
    pools = []
    d = df.iloc[-lookback:]
    off = len(df) - len(d)
    for kind, flag, col in (('high', sw['ph'], 'high'), ('low', sw['pl'], 'low')):
        idx = [k for k in np.flatnonzero(flag) if k >= off]
        lv = df[col].values
        for a in range(len(idx)):
            for b2 in range(a + 1, len(idx)):
                if abs(lv[idx[a]] - lv[idx[b2]]) <= tol:
                    level = max(lv[idx[a]], lv[idx[b2]]) if kind == 'high' else min(lv[idx[a]], lv[idx[b2]])
                    after = df.iloc[idx[b2] + 1:]
                    taken = (after.high > level).any() if kind == 'high' else (after.low < level).any()
                    if not taken:
                        pools.append((kind, level, df.index[idx[a]], df.index[idx[b2]]))
    uniq = []
    for p in sorted(pools, key=lambda x: x[1]):
        if not any(q[0] == p[0] and abs(q[1] - p[1]) <= tol for q in uniq):
            uniq.append(p)
    return uniq


def live():
    d1 = core.fetch(SYMBOL, '1day', 300)
    h4 = core.fetch(SYMBOL, '4h', 300)
    h1 = core.fetch(SYMBOL, '1h', 300)
    m15 = core.fetch(SYMBOL, '15min', 40)
    b = signals(d1, h4, h1, sr=False)
    bs = signals(d1, h4, h1, sr=True)
    h1p, h4p, d1p = b['h1p'], b['h4p'], b['d1p']
    price = float(m15.close.iloc[-1]) if len(m15) else float(h1p.close.iloc[-1])
    atr4, atr1, atrd = float(h4p.ATR.iloc[-1]), float(h1p.ATR.iloc[-1]), float(d1p.ATR.iloc[-1])
    struct = {'D1': int(b['swd']['struct'][-1]), 'H4': int(b['sw4']['struct'][-1]), 'H1': int(b['sw1']['struct'][-1])}
    sma = {tf: int(df.side.iloc[-1]) for tf, df in (('D1', d1p), ('H4', h4p), ('H1', h1p))}
    # แนวรับแนวต้าน: จุดสวิง H4 (120 แท่ง) + D1 (120 แท่ง) รวมเป็นโซน
    lv = []
    for df, sw, n in ((h4p.iloc[-120:], None, PIV['H4']), (d1p.iloc[-120:], None, PIV['D1'])):
        ph, pl = pivots(df, n)
        lv += list(df.high.values[ph]) + list(df.low.values[pl])
    zs = zones(lv, 0.35 * atrd)
    sup = sorted([z for z in zs if z[1] < price], key=lambda z: -z[1])[:3]
    res = sorted([z for z in zs if z[0] > price], key=lambda z: z[0])[:3]
    refs = {'High เมื่อวาน': float(b['pdh'][-1]), 'Low เมื่อวาน': float(b['pdl'][-1]),
            'High สัปดาห์ก่อน': float(b['pwh'][-1]), 'Low สัปดาห์ก่อน': float(b['pwl'][-1])}
    pools = liquidity_pools(h1p, b['sw1'], 0.15 * atr4)
    sweeps = []
    for i in range(max(1, len(h1p) - 24), len(h1p)):
        for s in (1, -1):
            lvl = b['sw1']['sl1'][i - 1] if s == 1 else b['sw1']['sh1'][i - 1]
            if np.isfinite(lvl):
                if (s == 1 and h1p.low.values[i] < lvl < h1p.close.values[i]) or \
                   (s == -1 and h1p.high.values[i] > lvl > h1p.close.values[i]):
                    sweeps.append((h1p.index[i], lvl, 'กวาด Low (ฝั่งซื้อรอ CHoCH ↑)' if s == 1 else 'กวาด High (ฝั่งขายรอ CHoCH ↓)', s))
    bias = 'ซื้อ' if (struct['H4'] == 1 and struct['D1'] != -1) else ('ขาย' if (struct['H4'] == -1 and struct['D1'] != 1) else None)
    signal = None
    for name, bb in (('S/R', bs), ('พื้นฐาน', b)):
        n = len(bb['h1p'])
        for i in range(n - 1, n - 1 - FRESH_BARS, -1):
            if bb['sig'][i] != 0:
                s = int(bb['sig'][i]); e = float(bb['h1p'].close.values[i]); rk = float(bb['risk'][i])
                signal = {'dir': s, 'time': bb['h1p'].index[i] + OFFSET['H1'], 'entry': e, 'sl': e - s * rk,
                          'tp': e + s * TP_R * rk, 'risk': rk, 'moved_R': s * (price - e) / rk, 'sr': name == 'S/R',
                          'info': bb['info'][i], 'close_by': bb['h1p'].index[i] + OFFSET['H1'] + pd.Timedelta(hours=MAX_HOLD_H)}
                break
        if signal:
            break
    pending = None
    if signal is None and bias and sweeps:
        want = 1 if bias == 'ซื้อ' else -1
        sw_ = [x for x in sweeps if x[3] == want]
        if sw_:
            t, lvl, _, s = sw_[-1]
            k = int(np.searchsorted(h1p.index.values, t.to_datetime64()))
            trig = b['sw1']['sh1'][k - 1] if s == 1 else b['sw1']['sl1'][k - 1]
            left = CHOCH_WINDOW - (len(h1p) - 1 - k)
            if left > 0 and np.isfinite(trig):
                pending = {'dir': s, 'sweep_time': t, 'level': lvl, 'trigger': float(trig), 'bars_left': left}
    score = struct['D1'] + struct['H4'] + struct['H1']
    return {'price': price, 'time': h1p.index[-1] + OFFSET['H1'], 'struct': struct, 'sma': sma, 'score': score,
            'bias': bias, 'support': sup, 'resistance': res, 'refs': refs, 'pools': pools, 'sweeps': sweeps,
            'signal': signal, 'pending': pending, 'atr': {'H1': atr1, 'H4': atr4, 'D1': atrd},
            'h1p': h1p, 'h4p': h4p, 'd1p': d1p, 'labels_h1': swing_labels(h1p, b['sw1']),
            'labels_h4': swing_labels(h4p, b['sw4']), 'events_h1': events(h1p, b['sw1']), 'events_h4': events(h4p, b['sw4']),
            'last_sw': {'H1': (b['sw1']['sh1'][-1], b['sw1']['sl1'][-1]), 'H4': (b['sw4']['sh1'][-1], b['sw4']['sl1'][-1]),
                        'D1': (b['swd']['sh1'][-1], b['swd']['sl1'][-1])}}


def lot_size(risk_price, account_usd, risk_pct, contract=1.0, min_lot=0.01):
    """คืน (lot, งบ USD, ความเสี่ยงจริง USD) | contract = จำนวน BTC ต่อ 1 lot (ดูในข้อมูลจำเพาะ BTCUSDm)"""
    if account_usd <= 0 or risk_price <= 0:
        return None, None, None
    money = account_usd * risk_pct / 100
    lot = math.floor(money / (risk_price * contract) * 100) / 100
    return lot, money, max(lot, min_lot) * contract * risk_price
