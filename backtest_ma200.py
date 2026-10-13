"""Backtest กลยุทธ์ MA200 + Market Profile (จุดเข้า M15)

- ดึง D1 / H4 / M15 จริงจาก Twelve Data ครั้งเดียว แล้วเดินทีละแท่ง M15
- ใช้เฉพาะแท่งที่ "ปิดแล้ว" ณ เวลานั้น (D1/H4/M15) ไม่แอบดูอนาคต
- เข้าที่ราคาปิดแท่งสัญญาณ ถ้าแท่งเดียวชนทั้ง SL และ TP ถือว่า SL (เข้าข้างร้าย)
- WINDOWS=0 หมายถึงทดสอบต่อเนื่องทั้งช่วงข้อมูล (ได้จำนวนเทรดมากที่สุด)
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
WARMUP = 260


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
    h4 = history(symbol, '4h', min(5000, days * 6 + 260))
    d1 = history(symbol, '1day', min(5000, days + 260))
    return d1, h4, m15


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


def run_asset(symbol, d1, h4, m15, rng):
    d1p, h4p, m15p = st.prep(d1), st.prep(h4), st.prep(m15)
    d1_close = (d1.index.tz_localize(None) + pd.Timedelta(days=1)).values
    h4_close = (h4.index.tz_localize(None) + pd.Timedelta(hours=4)).values
    asof = (m15.index.tz_localize(None) + pd.Timedelta(minutes=15)).values
    dc_all = np.searchsorted(d1_close, asof, side='right')
    hc_all = np.searchsorted(h4_close, asof, side='right')
    need = st.SMA_N + st.SLOPE_BARS + 2
    ok_idx = np.flatnonzero((dc_all >= need) & (hc_all >= need))
    if len(ok_idx) == 0:
        return pd.DataFrame(), 0
    lo_idx = max(WARMUP, int(ok_idx[0]))
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
            res = st.decide(d1p.iloc[:dc_all[i]], h4p.iloc[:hc_all[i]], m15p.iloc[:i + 1], fast=True)
            sig, dr = res['setup'], res['direction']
            if not sig:
                continue
            if i - last_i.get(dr, -10 ** 9) < COOLDOWN_BARS:
                continue
            label, r_gross, tp1_hit, bars = simulate(m15, i, dr, sig)
            cost = SPREAD[symbol] / sig['risk']
            trades.append({'symbol': symbol, 'time': m15.index[i], 'dir': dr, 'zone': sig['names'],
                           'score': sig['score'], 'result': label, 'tp1': tp1_hit,
                           'R_gross': r_gross, 'R_net': r_gross - cost, 'risk_atr': sig['risk'] / sig['atr']})
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


def main():
    if not core.API_KEY:
        raise SystemExit('Missing TWELVEDATA_API_KEY')
    rng = np.random.default_rng(SEED)
    parts, total = [], 0
    for name, sym in core.ASSETS.items():
        print(f'[LOAD] {sym}')
        d1, h4, m15 = load_history(sym)
        print(f'  M15 {len(m15)} แท่ง {m15.index[0]} → {m15.index[-1]} | H4 {len(h4)} | D1 {len(d1)}')
        t, nw = run_asset(sym, d1, h4, m15, rng)
        parts.append(t)
        total += nw
    all_t = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    md = report(all_t, total)
    print(md)
    all_t.to_csv('backtest_trades.csv', index=False)
    path = os.getenv('GITHUB_STEP_SUMMARY')
    if path:
        with open(path, 'a') as f:
            f.write(md + '\n')


if __name__ == '__main__':
    main()
