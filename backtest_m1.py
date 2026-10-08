"""Backtest ของ m1_swing_scanner.py — สุ่มช่วงเวลาย้อนหลัง ช่วงละ 2 ชั่วโมง

- ดึงข้อมูลจริงจาก Twelve Data (M1 / H1 / D1) ครั้งเดียว แล้วสุ่มหน้าต่างเวลา (ไม่ซ้อนกัน)
- เดินทีละแท่ง M1 โดยใช้ฟังก์ชันตัดสินใจตัวเดียวกับสแกนเนอร์ (h1_context / m1_signal)
- ใช้เฉพาะแท่งที่ "ปิดแล้ว" ณ เวลานั้น (D1/H1/M1) ไม่แอบดูอนาคต
- เข้าที่ราคาปิดแท่งสัญญาณ ถ้าแท่งเดียวชนทั้ง SL และ TP ถือว่า SL (เข้าข้างร้าย)
- ตั้งค่าผ่าน env: WINDOWS, WINDOW_MIN, SEED, M1_PAGES, SPREAD_BTC, SPREAD_XAU
"""
import os, math
import numpy as np
import pandas as pd
import requests
import m1_swing_scanner as s

WINDOWS = int(os.getenv('WINDOWS', '30'))          # จำนวนหน้าต่างต่อสินทรัพย์
WINDOW_MIN = int(os.getenv('WINDOW_MIN', '120'))   # ความยาวหน้าต่าง (นาที) = 2 ชม.
SEED = int(os.getenv('SEED', '42'))
M1_PAGES = int(os.getenv('M1_PAGES', '2'))         # 1 หน้า = 5000 แท่ง M1
SPREAD = {'BTC/USD': float(os.getenv('SPREAD_BTC', '10')),
          'XAU/USD': float(os.getenv('SPREAD_XAU', '0.30'))}   # ต้นทุนเป็นหน่วยราคา
TP1_SHARE = 0.7            # ปิด 70% ที่ TP1 ที่เหลือวิ่งต่อ (SL เลื่อนมาทุน)
RUNNER_MAX_BARS = 60
WARMUP = 300


# ---------- ข้อมูล ----------
def history(symbol, interval, size, pages=1):
    frames, end = [], None
    for p in range(pages):
        s._pace()
        params = {'symbol': symbol, 'interval': interval, 'outputsize': size,
                  'apikey': s.API_KEY, 'timezone': 'UTC'}
        if end:
            params['end_date'] = end
        data = requests.get(f'{s.BASE}/time_series', params=params, timeout=40).json()
        if 'values' not in data:
            raise RuntimeError(data.get('message', 'no data'))
        d = pd.DataFrame(data['values'])
        for c in ('open', 'high', 'low', 'close'):
            d[c] = pd.to_numeric(d[c], errors='coerce')
        d['datetime'] = pd.to_datetime(d['datetime'], utc=True)
        d = d.sort_values('datetime').set_index('datetime').dropna(subset=['open', 'high', 'low', 'close'])
        if p == 0 and len(d) > 1:
            d = d.iloc[:-1]                      # ตัดแท่งที่ยังไม่ปิด
        frames.append(d)
        end = (d.index[0] - pd.Timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')
    out = pd.concat(frames).sort_index()
    return out[~out.index.duplicated()]


def load_history(symbol):
    m1 = history(symbol, '1min', 5000, M1_PAGES)
    hours = int((m1.index[-1] - m1.index[0]).total_seconds() / 3600) + 200
    h1 = history(symbol, '1h', min(5000, hours))
    d1 = history(symbol, '1day', 120)
    return d1, h1, m1


# ---------- จำลองการถือเทรด ----------
def simulate(m1, i, direction, sig):
    hi, lo, cl = m1.high.values, m1.low.values, m1.close.values
    n = len(m1)
    sgn = 1 if direction == 'LONG' else -1
    entry, sl, tp1, tp2, risk = sig['entry'], sig['sl'], sig['tp1'], sig['tp2'], sig['risk']

    def hit_sl(j, level):
        return lo[j] <= level if sgn == 1 else hi[j] >= level

    def hit_tp(j, level):
        return hi[j] >= level if sgn == 1 else lo[j] <= level

    j = i
    for k in range(1, s.TIME_STOP_BARS + 1):
        j = i + k
        if j >= n:
            return 'END', sgn * (cl[n - 1] - entry) / risk, False, n - 1 - i
        if hit_sl(j, sl):
            return 'SL', -1.0, False, k
        if hit_tp(j, tp1):
            break
    else:
        return 'TIME', sgn * (cl[j] - entry) / risk, False, s.TIME_STOP_BARS

    # ถึง TP1 แล้ว: runner ใช้ SL = ทุน เป้า TP2
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
            runner = s.TP2_R
            break
    if runner is None:
        runner = sgn * (cl[min(j + RUNNER_MAX_BARS, n - 1)] - entry) / risk
    label = 'TP1+TP2' if runner == s.TP2_R else ('TP1+BE' if runner == 0.0 else 'TP1+TIME')
    return label, TP1_SHARE * s.TP1_R + (1 - TP1_SHARE) * runner, True, bars


def pick_windows(m1, rng, count):
    n = len(m1)
    idx = m1.index
    wins, tries = [], 0
    while len(wins) < count and tries < count * 200:
        tries += 1
        a = int(rng.integers(WARMUP, n - WINDOW_MIN - 80))
        b = a + WINDOW_MIN
        if (idx[b - 1] - idx[a]).total_seconds() / 60 > WINDOW_MIN + 5:
            continue                              # มีช่องว่าง (ตลาดปิด)
        if any(not (b <= x or a >= y) for x, y in wins):
            continue
        wins.append((a, b))
    return sorted(wins)


def run_asset(symbol, d1, h1, m1, rng):
    wins = pick_windows(m1, rng, WINDOWS)
    h1_close = (h1.index.tz_localize(None) + pd.Timedelta(hours=1)).values
    d1_close = (d1.index.tz_localize(None) + pd.Timedelta(days=1)).values
    m1_t = m1.index.tz_localize(None).values
    trades = []
    for (a, b) in wins:
        free_at, last_i, key, ctx = a, {}, None, None
        for i in range(a, b):
            if i < free_at:
                continue
            now = m1_t[i] + np.timedelta64(1, 'm')
            hc = int(np.searchsorted(h1_close, now, side='right'))
            dc = int(np.searchsorted(d1_close, now, side='right'))
            if hc < 60 or dc < 2:
                continue
            if (hc, dc) != key:
                key = (hc, dc)
                ctx, _ = s.h1_context(s.ind(h1.iloc[max(0, hc - 120):hc]), s.d1_prev_bias(d1.iloc[:dc]))
            if not ctx or not ctx['armed']:
                continue
            dr = ctx['direction']
            if i - last_i.get(dr, -10**9) < s.COOLDOWN_MIN:
                continue
            sig, _ = s.m1_signal(s.ind(m1.iloc[i - WARMUP + 1:i + 1]), ctx)
            if not sig:
                continue
            res, r_gross, tp1_hit, bars = simulate(m1, i, dr, sig)
            cost = SPREAD[symbol] / sig['risk']
            trades.append({'symbol': symbol, 'time': m1.index[i], 'dir': dr, 'mode': ctx['mode'],
                           'result': res, 'tp1': tp1_hit, 'R_gross': r_gross, 'R_net': r_gross - cost,
                           'risk_atr': sig['risk'] / sig['atr']})
            last_i[dr] = i
            free_at = i + bars + 1
    return pd.DataFrame(trades), len(wins)


# ---------- สรุปผล ----------
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
            f'เฉพาะที่จบ TP1/SL = {dec} | '
            f'SL {(t.result == "SL").mean() * 100:.0f}% | หมดเวลา {t.result.isin(["TIME", "END"]).mean() * 100:.0f}% | '
            f'เฉลี่ย/เทรด {t.R_gross.mean():+.2f}R (หักต้นทุน {t.R_net.mean():+.2f}R)\n')


def report(all_t, n_wins):
    lines = [f'## ผล Backtest M1 Swing\nหน้าต่างทดสอบ {n_wins} ช่วง × {WINDOW_MIN} นาที '
             f'(รวม {n_wins * WINDOW_MIN / 60:.0f} ชม.) | seed {SEED}\n']
    lines.append(block(all_t, 'รวมทุกสินทรัพย์'))
    for sym in all_t.symbol.unique() if len(all_t) else []:
        lines.append(block(all_t[all_t.symbol == sym], sym))
    for d in ('LONG', 'SHORT'):
        if len(all_t) and (all_t.dir == d).any():
            lines.append(block(all_t[all_t.dir == d], d))
    for m in ('TREND', 'RANGE'):
        if len(all_t) and (all_t['mode'] == m).any():
            lines.append(block(all_t[all_t['mode'] == m], f'โหมด H1 {m}'))
    lines.append('\nหมายเหตุ: TP1 = SL = 1R ดังนั้นต้องชนะ >50% (บวกต้นทุน) ถึงจะเริ่มไม่ขาดทุน | '
                 'เทรดน้อยกว่า ~30 รายการ ตัวเลข % ยังไม่น่าเชื่อถือ | ผลย้อนหลังไม่รับประกันอนาคต')
    return '\n'.join(lines)


def main():
    if not s.API_KEY:
        raise SystemExit('Missing TWELVEDATA_API_KEY')
    rng = np.random.default_rng(SEED)
    parts, total_wins = [], 0
    for name, sym in s.ASSETS.items():
        print(f'[LOAD] {sym}')
        d1, h1, m1 = load_history(sym)
        print(f'  M1 {len(m1)} แท่ง {m1.index[0]} → {m1.index[-1]} | H1 {len(h1)} | D1 {len(d1)}')
        t, nw = run_asset(sym, d1, h1, m1, rng)
        total_wins += nw
        parts.append(t)
    all_t = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    md = report(all_t, total_wins)
    print(md)
    all_t.to_csv('backtest_trades.csv', index=False)
    path = os.getenv('GITHUB_STEP_SUMMARY')
    if path:
        with open(path, 'a') as f:
            f.write(md + '\n')


if __name__ == '__main__':
    main()
