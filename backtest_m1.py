"""M1 Swing Scanner — เทรดสวิงสั่นๆ ในกราฟ 1 นาที

D1 : ดูแค่ "แท่งก่อนหน้า" แท่งเดียว (ขึ้น/ลง/กลางๆ) เป็นตัวกรองทิศ + PDH/PDL
H1 : ตัวหลัก กำหนดโหมด (TREND / RANGE) ทิศ และ "โซน" ที่ยอมให้เข้า
M1 : หาจังหวะเข้า = ย่อ/เด้งเข้าโซน H1 แล้วเบรก micro swing กลับตามทิศ

M1 ถูกดึงเฉพาะตอนราคาอยู่ใกล้โซน H1 (armed) และมีงบ credit ต่อวันกันเกินโควต้า
"""
import os, time, json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import requests

# ---------------- Config ----------------
BASE = 'https://api.twelvedata.com'
ASSETS = {'Bitcoin BTC/USD': 'BTC/USD', 'Gold XAU/USD': 'XAU/USD'}

API_KEY = os.getenv('TWELVEDATA_API_KEY', '').strip()
TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '').strip()
USER_ID = os.getenv('LINE_USER_ID', '').strip()
DRY_RUN = os.getenv('DRY_RUN', '').strip() == '1'          # 1 = print แทนส่ง LINE
LOOP_MINUTES = int(os.getenv('LOOP_MINUTES', '0') or 0)     # 0 = สแกนรอบเดียว
STATE = Path('m1_state.json')

ACTIVE_TZ = ZoneInfo('America/Edmonton')
ACTIVE_START_HOUR, ACTIVE_END_HOUR = 6, 22

CREDIT_BUDGET = 650          # Free plan = 800/วัน เหลือกันไว้ ใช้เกินจะหยุดดึง M1
D1_REFRESH_MIN = 360
H1_REFRESH_MIN = 10
D1_BODY_MIN = 0.25           # body/range ของแท่ง D1 ต่ำกว่านี้ = กลางๆ
H1_RANGE_BARS = 24
ARMED_PAD_ATR = 1.0          # H1 close ห่างโซนไม่เกิน N ATR(H1) ถึงเริ่มดึง M1
ZONE_TOL_ATR = 0.3           # จุดต่ำ/สูงของการย่อเลยโซนได้ N ATR(H1)
PIVOT_LOOKBACK = 40
MIN_PULLBACK_ATR = 0.8       # ย่อ/เด้งต้องลึกอย่างน้อย N ATR(M1)
MAX_BREAK_EXT_ATR = 1.0      # ปิดเลยจุดเบรกเกิน N ATR(M1) = ไล่ราคา
SL_BUFFER_ATR = 0.2
MIN_RISK_ATR, MAX_RISK_ATR = 0.8, 4.0
TP1_R, TP2_R = 1.0, 1.5
TIME_STOP_BARS = 15
COOLDOWN_MIN = 10
MAX_ALERTS_PER_DAY = 10
M1_MAX_AGE_MIN = 4

CREDITS = 0
_CALLS = []
MAX_CALLS_PER_MIN = 6


class BudgetExceeded(Exception):
    pass


def now_utc():
    return datetime.now(timezone.utc)


def within_active_window():
    return ACTIVE_START_HOUR <= datetime.now(ACTIVE_TZ).hour < ACTIVE_END_HOUR


def _pace():
    t = time.monotonic()
    while _CALLS and t - _CALLS[0] > 60:
        _CALLS.pop(0)
    if len(_CALLS) >= MAX_CALLS_PER_MIN:
        time.sleep(max(0, 60 - (t - _CALLS[0]) + 0.5))
    _CALLS.append(time.monotonic())


def fetch(symbol, interval, size, kind='ctx', _retried=False):
    global CREDITS
    if kind == 'm1' and CREDITS >= CREDIT_BUDGET:
        raise BudgetExceeded(f'credit วันนี้ครบงบ {CREDIT_BUDGET} แล้ว')
    _pace()
    r = requests.get(f'{BASE}/time_series', timeout=20, params={
        'symbol': symbol, 'interval': interval, 'outputsize': size,
        'apikey': API_KEY, 'timezone': 'UTC'})
    CREDITS += 1
    data = r.json()
    if r.status_code != 200 or 'values' not in data:
        msg = data.get('message', f'Twelve Data HTTP {r.status_code}')
        if (r.status_code == 429 or 'credits' in msg.lower()) and not _retried:
            print(f'[RATE LIMIT] {symbol} {interval} -> รอ 65s')
            time.sleep(65)
            return fetch(symbol, interval, size, kind, True)
        raise RuntimeError(msg)
    d = pd.DataFrame(data['values'])
    for c in ('open', 'high', 'low', 'close'):
        d[c] = pd.to_numeric(d[c], errors='coerce')
    d['datetime'] = pd.to_datetime(d['datetime'], utc=True)
    d = d.sort_values('datetime').set_index('datetime').dropna(subset=['open', 'high', 'low', 'close'])
    return d.iloc[:-1].copy() if len(d) > 1 else d          # ตัดแท่งที่ยังไม่ปิด


def ind(d):
    d = d.copy()
    c, h, l = d.close, d.high, d.low
    d['EMA20'] = c.ewm(span=20, adjust=False).mean()
    d['EMA50'] = c.ewm(span=50, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d['ATR'] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    d['range'] = h - l
    d['body_ratio'] = (c - d.open).abs() / d['range'].replace(0, np.nan)
    return d


def fmt(v):
    return f'{v:,.2f}' if v >= 100 else f'{v:.4f}'


# ---------------- D1: แท่งก่อนหน้าแท่งเดียว ----------------
def d1_prev_bias(d1):
    x = d1.iloc[-1]
    rng = float(x.high - x.low)
    br = abs(float(x.close - x.open)) / rng if rng > 0 else 0
    if br < D1_BODY_MIN:
        bias = 'NEUTRAL'
    else:
        bias = 'UP' if x.close > x.open else 'DOWN'
    return {'bias': bias, 'pdh': float(x.high), 'pdl': float(x.low)}


# ---------------- H1: ตัวหลัก ----------------
def h1_context(h1, d1b):
    """คืน dict (mode, direction, zone_lo, zone_hi, atr, armed, ...) หรือ (None, เหตุผล)"""
    if len(h1) < 60:
        return None, 'ข้อมูล H1 ไม่พอ'
    x = h1.iloc[-1]
    atr = max(float(x.ATR), 1e-9)
    e20, e50, close = float(x.EMA20), float(x.EMA50), float(x.close)
    slope = e20 - float(h1.EMA20.iloc[-6])
    rhi = float(h1.iloc[-H1_RANGE_BARS:].high.max())
    rlo = float(h1.iloc[-H1_RANGE_BARS:].low.min())
    bias = d1b['bias']

    if e20 > e50 and close > e50 and slope > 0:
        mode, direction = 'TREND', 'LONG'
    elif e20 < e50 and close < e50 and slope < 0:
        mode, direction = 'TREND', 'SHORT'
    else:
        mode, direction = 'RANGE', None

    if mode == 'TREND':
        if (direction == 'LONG' and bias == 'DOWN') or (direction == 'SHORT' and bias == 'UP'):
            return None, f'H1 {direction} แต่ D1 แท่งก่อนหน้า {bias} ขัดกัน'
    else:
        if bias == 'NEUTRAL':
            return None, 'H1 sideways และ D1 แท่งก่อนหน้ากลางๆ'
        if rhi - rlo < 1.5 * atr:
            return None, 'กรอบ H1 แคบเกินไป'
        direction = 'LONG' if bias == 'UP' else 'SHORT'

    span = rhi - rlo
    if mode == 'TREND':
        if direction == 'LONG':
            lo, hi = e50 - 0.3 * atr, e20 + 0.6 * atr
        else:
            lo, hi = e20 - 0.6 * atr, e50 + 0.3 * atr
    else:
        if direction == 'LONG':
            lo, hi = rlo - 0.3 * atr, rlo + 0.3 * span
        else:
            lo, hi = rhi - 0.3 * span, rhi + 0.3 * atr
    armed = (lo - ARMED_PAD_ATR * atr) <= close <= (hi + ARMED_PAD_ATR * atr)
    return {'mode': mode, 'direction': direction, 'lo': lo, 'hi': hi, 'atr': atr,
            'armed': bool(armed), 'close': close, 'rhi': rhi, 'rlo': rlo}, 'ok'


# ---------------- M1: swing entry ----------------
def m1_signal(m1, ctx):
    """ย่อ/เด้งเข้าโซน H1 แล้วปิดเบรก micro swing สุดท้ายตามทิศ"""
    if len(m1) < 80:
        return None, 'ข้อมูล M1 ไม่พอ'
    direction, lo, hi = ctx['direction'], ctx['lo'], ctx['hi']
    tol = ZONE_TOL_ATR * ctx['atr']
    h, l = m1.high.values, m1.low.values
    n = len(m1)
    x, p = m1.iloc[-1], m1.iloc[-2]
    atr = max(float(x.ATR), 1e-9)
    c, pc = float(x.close), float(p.close)

    piv = None
    for i in range(n - 3, max(n - PIVOT_LOOKBACK, 3), -1):
        if direction == 'LONG':
            ok = h[i] > h[i - 1] and h[i] > h[i - 2] and h[i] > h[i + 1] and h[i] > h[i + 2]
        else:
            ok = l[i] < l[i - 1] and l[i] < l[i - 2] and l[i] < l[i + 1] and l[i] < l[i + 2]
        if ok:
            piv = i
            break
    if piv is None:
        return None, 'ยังไม่มี micro swing'

    if direction == 'LONG':
        level = float(h[piv])
        j = piv + 1 + int(np.argmin(l[piv + 1:]))
        extreme = float(l[j])
        depth = level - extreme
        in_zone = (extreme <= hi) and (extreme >= lo - tol)
        broke = c > level and pc <= level and x.close > x.open
        ext = (c - level) / atr
    else:
        level = float(l[piv])
        j = piv + 1 + int(np.argmax(h[piv + 1:]))
        extreme = float(h[j])
        depth = extreme - level
        in_zone = (extreme >= lo) and (extreme <= hi + tol)
        broke = c < level and pc >= level and x.close < x.open
        ext = (level - c) / atr

    if j - piv < 3:
        return None, 'การย่อสั้นเกินไป'
    if depth < MIN_PULLBACK_ATR * atr:
        return None, f'ย่อ/เด้งตื้น ({depth / atr:.1f} ATR)'
    if not in_zone:
        return None, 'จุดย่อ/เด้งไม่อยู่ในโซน H1'
    if not (broke and float(x.body_ratio) >= 0.35):
        return None, 'รอเบรก micro swing'
    if ext > MAX_BREAK_EXT_ATR:
        return None, f'ไล่ราคา ({ext:.1f} ATR เลยจุดเบรก)'

    if direction == 'LONG':
        sl = extreme - SL_BUFFER_ATR * atr
        risk = c - sl
    else:
        sl = extreme + SL_BUFFER_ATR * atr
        risk = sl - c
    if not (MIN_RISK_ATR * atr <= risk <= MAX_RISK_ATR * atr):
        return None, f'risk นอกช่วง ({risk / atr:.1f} ATR)'
    s = 1 if direction == 'LONG' else -1
    return {'entry': c, 'sl': sl, 'tp1': c + s * TP1_R * risk, 'tp2': c + s * TP2_R * risk,
            'risk': risk, 'atr': atr, 'break_level': level}, 'ok'


# ---------------- State / LINE ----------------
def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(s):
    keep = {k: v for k, v in s.items() if k.startswith(('credits|', 'cd|', 'cnt|'))}
    rest = [(k, v) for k, v in s.items() if not k.startswith(('credits|', 'cd|', 'cnt|'))][-150:]
    keep.update(rest)
    STATE.write_text(json.dumps(keep, indent=2))


def push(text):
    if DRY_RUN:
        print('----- [DRY RUN] -----\n' + text + '\n---------------------')
        return
    r = requests.post('https://api.line.me/v2/bot/message/push', timeout=15,
                      headers={'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'},
                      json={'to': USER_ID, 'messages': [{'type': 'text', 'text': text}]})
    r.raise_for_status()


# ---------------- Scan ----------------
CTX = {}   # แคช D1/H1 ในหน่วยความจำระหว่าง loop


def get_context(symbol):
    c = CTX.setdefault(symbol, {})
    t = now_utc()
    if 'd1' not in c or t - c['d1_ts'] > timedelta(minutes=D1_REFRESH_MIN):
        c['d1'] = d1_prev_bias(fetch(symbol, '1day', 3))
        c['d1_ts'] = t
    if 'ctx' not in c or t - c['h1_ts'] > timedelta(minutes=H1_REFRESH_MIN):
        h1 = ind(fetch(symbol, '1h', 120))
        c['ctx'], c['why'] = h1_context(h1, c['d1'])
        c['h1_ts'] = t
        print(f'[H1] {symbol}: D1prev={c["d1"]["bias"]} -> {c["ctx"] if c["ctx"] else c["why"]}')
    return c


def scan_asset(name, symbol, state):
    c = get_context(symbol)
    ctx = c['ctx']
    if not ctx or not ctx['armed']:
        return
    day = now_utc().strftime('%Y-%m-%d')
    direction = ctx['direction']
    cd_key, cnt_key = f'cd|{symbol}|{direction}', f'cnt|{symbol}|{day}'
    last = state.get(cd_key)
    if last and now_utc() - datetime.fromisoformat(last) < timedelta(minutes=COOLDOWN_MIN):
        return
    if state.get(cnt_key, 0) >= MAX_ALERTS_PER_DAY:
        return

    m1 = ind(fetch(symbol, '1min', 300, kind='m1'))
    age = now_utc() - m1.index[-1].to_pydatetime()
    if age > timedelta(minutes=M1_MAX_AGE_MIN):
        print(f'[SKIP] {symbol} M1 เก่า ({age}) — ตลาดปิด/ข้อมูลช้า')
        return
    sig, why = m1_signal(m1, ctx)
    print(f'[M1] {symbol} {m1.index[-1]:%H:%M} {direction} -> {why}')
    if not sig:
        return
    key = f'm1|{symbol}|{direction}|{m1.index[-1]}'
    if key in state:
        return
    d1 = c['d1']
    arrow = '🟢 LONG' if direction == 'LONG' else '🔴 SHORT'
    msg = (f'⚡ {name} | M1 SWING\n{arrow}\n'
           f'D1 แท่งก่อนหน้า: {d1["bias"]} (PDH {fmt(d1["pdh"])} / PDL {fmt(d1["pdl"])})\n'
           f'H1 ({ctx["mode"]}): โซน {fmt(ctx["lo"])} - {fmt(ctx["hi"])}\n'
           f'M1: ย่อ/เด้งเข้าโซน แล้วเบรก {fmt(sig["break_level"])}\n\n'
           f'Entry: {fmt(sig["entry"])}\nSL: {fmt(sig["sl"])}\n'
           f'TP1 ({TP1_R}R): {fmt(sig["tp1"])}\nTP2 ({TP2_R}R): {fmt(sig["tp2"])}\n'
           f'Risk: {fmt(sig["risk"])} ({sig["risk"] / sig["atr"]:.1f} ATR M1)\n'
           f'แผน: ปิดส่วนใหญ่ที่ TP1 แล้วเลื่อน SL มาทุน | ไม่ถึง TP1 ใน {TIME_STOP_BARS} แท่ง M1 ให้ออก')
    push(msg)
    state[key] = state[cd_key] = now_utc().isoformat()
    state[cnt_key] = state.get(cnt_key, 0) + 1


def run_once(state):
    if not DRY_RUN and not within_active_window():
        print(f'[SKIP] นอกเวลา {datetime.now(ACTIVE_TZ):%H:%M %Z}')
        return
    for name, symbol in ASSETS.items():
        try:
            scan_asset(name, symbol, state)
        except BudgetExceeded as e:
            print(f'[BUDGET] {e}')
        except Exception as e:
            print(f'{name}: {e}')


def main():
    global CREDITS
    need = [API_KEY] if DRY_RUN else [API_KEY, TOKEN, USER_ID]
    if not all(need):
        raise SystemExit('Missing TWELVEDATA_API_KEY / LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID')
    state = load_state()
    ckey = f'credits|{now_utc():%Y-%m-%d}'
    CREDITS = int(state.get(ckey, 0))
    end = time.time() + LOOP_MINUTES * 60
    while True:
        ckey = f'credits|{now_utc():%Y-%m-%d}'
        run_once(state)
        state[ckey] = CREDITS
        if not DRY_RUN:
            save_state(state)
        if time.time() >= end:
            break
        time.sleep(60 - (time.time() % 60) + 4)       # รอแท่ง M1 ถัดไปปิด
        if time.time() >= end:
            break
    print(f'[DONE] credits ใช้วันนี้ ~{CREDITS}/{CREDIT_BUDGET}')


if __name__ == '__main__':
    main()
