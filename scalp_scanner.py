"""Gold & BTC Scalp Scanner — เล่นสั้น เก็บเร็ว จุดเข้า M15 setup + M5 trigger

ยังเป็น Trend Following: ทิศมาจาก D1 (ต้องไม่ขัด H1) ไม่มี counter-trend
D1/H1 แคชไว้ 60 นาที, M15 ดึงทุกรอบ, M5 ดึงเฉพาะตอนมี M15 setup -> ประหยัด credit
ใช้ฟังก์ชันร่วมกับ swing_scanner.py (fetch, ind, d1_trend, push)
"""
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
import numpy as np
import swing_scanner as core
from swing_scanner import fetch, ind, d1_trend, push, fmt

ASSETS = core.ASSETS
STATE = Path('scalp_state.json')

MIN_D1_STRENGTH = 60
BIAS_TTL_MIN = 60            # แคช D1/H1 bias กี่นาที
SETUP_MAX_AGE = 1            # M15 setup ย้อนหลังได้กี่แท่ง (0 = แท่งล่าสุด)
M5_MAX_AGE_MIN = 20          # ข้อมูล M5 เก่ากว่านี้ = ตลาดปิด/ข้อมูลค้าง
MAX_M5_EXTENSION = 1.5       # M5 ห่าง EMA20 เกิน N ATR = ไล่ราคา ไม่เข้า
SL_BUFFER_ATR = 0.25
MIN_RISK_ATR, MAX_RISK_ATR = 0.6, 2.5   # x ATR(M5)
TP1_R, TP2_R = 1.0, 1.5      # เก็บเร็ว
COOLDOWN_MIN = 30            # ทิศเดิมของสินทรัพย์เดิมแจ้งซ้ำไม่ถี่กว่านี้
MAX_ALERTS_PER_DAY = 6       # ต่อสินทรัพย์ (นับวัน UTC)
TIME_STOP_BARS = 12          # ไม่ถึง TP1 ใน 12 แท่ง M5 (~1 ชม.) -> ออก


def now_utc():
    return datetime.now(timezone.utc)


# ---------- Bias (D1 + H1) แคช ----------
def h1_agrees(d, direction):
    x = d.iloc[-1]
    if direction == 'LONG':
        return bool(x.EMA20 > x.EMA50 and x.close > x.EMA50)
    return bool(x.EMA20 < x.EMA50 and x.close < x.EMA50)


def get_bias(symbol, state):
    key = f'bias|{symbol}'
    b = state.get(key)
    if b and now_utc() - datetime.fromisoformat(b['ts']) < timedelta(minutes=BIAS_TTL_MIN):
        return b
    d1 = ind(fetch(symbol, '1day'))
    direction, strength, note = d1_trend(d1)
    ok = direction in ('LONG', 'SHORT') and strength >= MIN_D1_STRENGTH
    h1_ok = False
    if ok:
        h1_ok = h1_agrees(ind(fetch(symbol, '1h')), direction)
    b = {'ts': now_utc().isoformat(), 'direction': direction, 'strength': strength,
         'note': note, 'ok': bool(ok and h1_ok), 'h1_ok': bool(h1_ok)}
    state[key] = b
    print(f'[BIAS] {symbol}: D1={direction}({strength}) H1 agree={h1_ok} -> ok={b["ok"]}')
    return b


# ---------- M15 setup ----------
def _m15_names(d, direction):
    x = d.iloc[-1]
    atr = max(float(x.ATR), 1e-9)
    prior8 = d.iloc[-8:-1]
    prior20 = d.iloc[-21:-1]
    if direction == 'LONG':
        pull = (x.EMA20 > x.EMA50 and x.low <= x.EMA20 + 0.75 * atr and x.close > x.EMA20
                and x.close > x.open and x.body_ratio >= 0.30)
        sweep = x.low < prior8.low.min() and x.close > prior8.low.min() and x.close > x.open
        brk = x.close > prior20.high.max() and x.close > x.open and x.body_ratio >= 0.30
    else:
        pull = (x.EMA20 < x.EMA50 and x.high >= x.EMA20 - 0.75 * atr and x.close < x.EMA20
                and x.close < x.open and x.body_ratio >= 0.30)
        sweep = x.high > prior8.high.max() and x.close < prior8.high.max() and x.close < x.open
        brk = x.close < prior20.low.min() and x.close < x.open and x.body_ratio >= 0.30
    return [n for n, f in (('pullback', pull), ('sweep', sweep), ('breakout', brk)) if f]


def m15_setup(d, direction):
    if len(d) < 80:
        return None
    for age in range(SETUP_MAX_AGE + 1):
        sub = d if age == 0 else d.iloc[:-age]
        names = _m15_names(sub, direction)
        if names:
            return {'names': ' + '.join(names), 'age': age, 'score': min(100, 62 + 8 * len(names))}
    return None


# ---------- M5 trigger ----------
def m5_trigger(d, direction):
    if len(d) < 50:
        return False, 0, 'ข้อมูล M5 ไม่พอ'
    x, p = d.iloc[-1], d.iloc[-2]
    rg = max(float(x.range), 1e-9)
    atr = max(float(x.ATR), 1e-9)
    if direction == 'LONG':
        brk = x.close > p.high
        rec = x.close > x.EMA20 and p.close <= p.EMA20
        rej = x.lower_wick >= 0.30 * rg and x.close >= x.low + 0.60 * rg
        dirc = x.close > x.open and x.close > x.EMA20
        ext = (x.close - x.EMA20) / atr
    else:
        brk = x.close < p.low
        rec = x.close < x.EMA20 and p.close >= p.EMA20
        rej = x.upper_wick >= 0.30 * rg and x.close <= x.high - 0.60 * rg
        dirc = x.close < x.open and x.close < x.EMA20
        ext = (x.EMA20 - x.close) / atr
    if ext > MAX_M5_EXTENSION:
        return False, 0, f'M5 ไล่ราคา ({ext:.1f} ATR จาก EMA20)'
    ok = bool(dirc and x.body_ratio >= 0.25 and (brk or rec or rej))
    names = [n for n, f in (('break', brk), ('reclaim EMA20', rec), ('rejection', rej)) if f]
    score = min(100, 40 * int(brk) + 35 * int(rec) + 25 * int(rej) + 20 * int(dirc))
    return ok, score, ' + '.join(names) if ok else 'รอ M5 trigger'


# ---------- Plan ----------
def scalp_plan(m5, direction):
    entry = float(m5.close.iloc[-1])
    atr = max(float(m5.ATR.iloc[-1]), 1e-9)
    if direction == 'LONG':
        sl = float(m5.tail(8).low.min()) - SL_BUFFER_ATR * atr
        risk = entry - sl
    else:
        sl = float(m5.tail(8).high.max()) + SL_BUFFER_ATR * atr
        risk = sl - entry
    if not (MIN_RISK_ATR * atr <= risk <= MAX_RISK_ATR * atr):
        return None
    s = 1 if direction == 'LONG' else -1
    return {'entry': entry, 'sl': sl, 'tp1': entry + s * TP1_R * risk,
            'tp2': entry + s * TP2_R * risk, 'risk': risk, 'atr': atr}


# ---------- Scan ----------
def scan_asset(name, symbol, state):
    print(f'[SCAN] {name}')
    bias = get_bias(symbol, state)
    if not bias['ok']:
        return []
    direction = bias['direction']

    m15 = ind(fetch(symbol, '15min'))
    setup = m15_setup(m15, direction)
    print(f'[M15] setup={setup}')
    if not setup:
        return []

    m5 = ind(fetch(symbol, '5min'))
    age = now_utc() - m5.index[-1].to_pydatetime()
    if age > timedelta(minutes=M5_MAX_AGE_MIN):
        print(f'[SKIP] ข้อมูล M5 เก่า ({age}) — ตลาดอาจปิด')
        return []
    ok, t_score, t_note = m5_trigger(m5, direction)
    print(f'[M5] trigger={ok} score={t_score} | {t_note}')
    if not ok:
        return []

    p = scalp_plan(m5, direction)
    if not p:
        print('[PLAN] risk อยู่นอกช่วงที่ยอมรับ — ไม่ส่ง')
        return []

    cd_key, cnt_key = f'cd|{symbol}|{direction}', f'cnt|{symbol}|{now_utc():%Y-%m-%d}'
    last = state.get(cd_key)
    if last and now_utc() - datetime.fromisoformat(last) < timedelta(minutes=COOLDOWN_MIN):
        print('[COOLDOWN] เพิ่งแจ้งทิศนี้ไป — ข้าม')
        return []
    if state.get(cnt_key, 0) >= MAX_ALERTS_PER_DAY:
        print('[LIMIT] ครบโควตาต่อวันแล้ว')
        return []

    arrow = '🟢 LONG' if direction == 'LONG' else '🔴 SHORT'
    msg = (f'⚡ {name} | SCALP\n{arrow} (ตาม D1 {bias["strength"]} + H1)\n'
           f'M15: {setup["names"]} ({setup["score"]})\nM5: {t_note} ({t_score})\n\n'
           f'Entry: {fmt(p["entry"])}\nSL: {fmt(p["sl"])}\n'
           f'TP1 ({TP1_R}R): {fmt(p["tp1"])}\nTP2 ({TP2_R}R): {fmt(p["tp2"])}\n'
           f'Risk: {fmt(p["risk"])} ({p["risk"] / p["atr"]:.1f} ATR M5)\n'
           f'แผน: ปิดส่วนใหญ่ที่ TP1 แล้วเลื่อน SL มาทุน | ไม่ถึง TP1 ใน {TIME_STOP_BARS} แท่ง M5 ให้ออก')
    sig = f'scalp|{symbol}|{direction}|{m5.index[-1]}'
    return [(sig, msg, cd_key, cnt_key)]


def main():
    if not core.DRY_RUN and not core.within_active_window():
        print(f'[SKIP] นอกเวลาแจ้งเตือน {datetime.now(core.ACTIVE_TZ):%H:%M %Z}')
        return
    need = [core.API_KEY] if core.DRY_RUN else [core.API_KEY, core.TOKEN, core.USER_ID]
    if not all(need):
        raise SystemExit('Missing TWELVEDATA_API_KEY / LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID')
    try:
        state = json.loads(STATE.read_text())
    except Exception:
        state = {}
    for name, symbol in ASSETS.items():
        try:
            alerts = scan_asset(name, symbol, state)
        except Exception as e:
            print(f'{name}: {e}')
            continue
        for sig, msg, cd_key, cnt_key in alerts:
            if sig in state:
                continue
            push(msg)
            state[sig] = now_utc().isoformat()
            state[cd_key] = now_utc().isoformat()
            state[cnt_key] = state.get(cnt_key, 0) + 1
            print('sent', sig)
    # เก็บเฉพาะ key ที่ยังใช้: bias + 150 รายการล่าสุด
    keep = {k: v for k, v in state.items() if k.startswith('bias|')}
    rest = [(k, v) for k, v in state.items() if not k.startswith('bias|')][-150:]
    keep.update(rest)
    if not core.DRY_RUN:
        STATE.write_text(json.dumps(keep, indent=2))


if __name__ == '__main__':
    main()
