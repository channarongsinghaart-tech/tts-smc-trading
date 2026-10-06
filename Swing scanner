"""Gold & BTC Swing Scanner — ตาม Trend D1 (Trend Following เท่านั้น)

Flow:  D1 กำหนดทิศ  ->  H4 รอย่อ (pullback) เข้าโซน EMA  ->  H1 trigger  ->  ส่ง LINE
ไม่มี counter-trend. ดึงข้อมูลแบบ lazy (D1 ไม่ผ่านก็ไม่ดึง H4/H1) เพื่อประหยัด credit.
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
TF = {'D1': '1day', 'H4': '4h', 'H1': '1h'}

API_KEY = os.getenv('TWELVEDATA_API_KEY', '').strip()
TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '').strip()
USER_ID = os.getenv('LINE_USER_ID', '').strip()
DRY_RUN = os.getenv('DRY_RUN', '').strip() == '1'      # DRY_RUN=1 -> print แทนส่ง LINE
STATE = Path('swing_state.json')

# ไม่ส่งแจ้งเตือนช่วงดึก (เวลา Alberta) — swing ไม่ต้องรีบ
ACTIVE_TZ = ZoneInfo('America/Edmonton')
ACTIVE_START_HOUR, ACTIVE_END_HOUR = 6, 22

MIN_D1_STRENGTH = 70        # ความแรงเทรนด์ D1 ขั้นต่ำ (0-100)
MAX_D1_EXTENSION = 3.0      # ราคาห่าง D1 EMA20 ไม่เกิน N x ATR(D1) (กันไล่ราคา)
H4_ZONE_ATR = 0.5           # แตะโซน EMA20 H4 ภายใน N x ATR(H4)
H4_ZONE_LOOKBACK = 4        # มองย้อน H4 กี่แท่งที่ปิดแล้ว
SL_BUFFER_ATR = 0.3
MIN_RISK_ATR, MAX_RISK_ATR = 0.5, 3.5   # ช่วง risk (x ATR H4) ที่ยอมรับ
TP1_R, TP2_R = 1.5, 3.0
COOLDOWN_HOURS = 12         # แจ้ง ENTRY ซ้ำทิศเดิมของสินทรัพย์เดิมได้ไม่ถี่กว่านี้
MAX_H1_AGE_HOURS = 3        # ตลาดปิด/ข้อมูลเก่า -> ข้าม

# Free plan: 8 credits/min -> เว้นไว้ที่ 6
_CALLS = []
MAX_CALLS_PER_MIN = 6


def within_active_window():
    return ACTIVE_START_HOUR <= datetime.now(ACTIVE_TZ).hour < ACTIVE_END_HOUR


def _pace():
    now = time.monotonic()
    while _CALLS and now - _CALLS[0] > 60:
        _CALLS.pop(0)
    if len(_CALLS) >= MAX_CALLS_PER_MIN:
        time.sleep(max(0, 60 - (now - _CALLS[0]) + 0.5))
    _CALLS.append(time.monotonic())


def fetch(symbol, interval, outputsize=260, _retried=False):
    _pace()
    r = requests.get(f'{BASE}/time_series', timeout=20, params={
        'symbol': symbol, 'interval': interval, 'outputsize': outputsize,
        'apikey': API_KEY, 'timezone': 'UTC'})
    data = r.json()
    if r.status_code != 200 or 'values' not in data:
        msg = data.get('message', f'Twelve Data HTTP {r.status_code}')
        limited = r.status_code == 429 or 'credits' in msg.lower()
        if limited and not _retried:
            print(f'[RATE LIMIT] {symbol} {interval} -> รอ 65s แล้วลองใหม่')
            time.sleep(65)
            return fetch(symbol, interval, outputsize, _retried=True)
        raise RuntimeError(msg)
    d = pd.DataFrame(data['values'])
    for c in ('open', 'high', 'low', 'close', 'volume'):
        if c in d:
            d[c] = pd.to_numeric(d[c], errors='coerce')
    d['datetime'] = pd.to_datetime(d['datetime'], utc=True)
    d = d.sort_values('datetime').set_index('datetime').dropna(subset=['open', 'high', 'low', 'close'])
    return d.iloc[:-1].copy() if len(d) > 3 else d      # ตัดแท่งที่ยังไม่ปิด


def ind(d):
    d = d.copy()
    c, h, l = d.close, d.high, d.low
    for n in (20, 50, 200):
        d[f'EMA{n}'] = c.ewm(span=n, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d['ATR'] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    d['range'] = h - l
    d['body_ratio'] = (c - d.open).abs() / d['range'].replace(0, np.nan)
    d['upper_wick'] = h - d[['open', 'close']].max(axis=1)
    d['lower_wick'] = d[['open', 'close']].min(axis=1) - l
    return d


# ---------------- 1) D1 trend ----------------
def d1_trend(d):
    """คืน (direction, strength, note). direction: LONG/SHORT/NEUTRAL"""
    if len(d) < 210:
        return 'NEUTRAL', 0, 'ข้อมูล D1 ไม่พอ'
    x = d.iloc[-1]
    atr = max(float(x.ATR), 1e-9)
    slope50 = float(x.EMA50 - d.EMA50.iloc[-11])
    slope20 = float(x.EMA20 - d.EMA20.iloc[-6])
    rh, rl = d.iloc[-8:].high.max(), d.iloc[-8:].low.min()
    ph, pl = d.iloc[-30:-8].high.max(), d.iloc[-30:-8].low.min()
    hhhl, lhll = rh > ph and rl > pl, rh < ph and rl < pl
    ext = (float(x.close) - float(x.EMA20)) / atr

    if x.EMA20 > x.EMA50 > x.EMA200 and x.close > x.EMA50 and slope50 > 0:
        pts = 30 + 15 + 15 + 25 * int(hhhl) + 15 * int(slope20 > 0)
        return 'LONG', pts, f'D1 uptrend (EMA stack, ext {ext:+.1f} ATR)' + (' HH/HL' if hhhl else '')
    if x.EMA20 < x.EMA50 < x.EMA200 and x.close < x.EMA50 and slope50 < 0:
        pts = 30 + 15 + 15 + 25 * int(lhll) + 15 * int(slope20 < 0)
        return 'SHORT', pts, f'D1 downtrend (EMA stack, ext {ext:+.1f} ATR)' + (' LH/LL' if lhll else '')
    return 'NEUTRAL', 40, 'D1 ไม่มีเทรนด์ชัด'


def d1_extended(d, direction):
    x = d.iloc[-1]
    ext = (float(x.close) - float(x.EMA20)) / max(float(x.ATR), 1e-9)
    return (ext > MAX_D1_EXTENSION) if direction == 'LONG' else (-ext > MAX_D1_EXTENSION)


# ---------------- 2) H4 pullback zone ----------------
def h4_pullback(d, direction):
    """เทรนด์ H4 ยังอยู่ + ราคาย่อมาแตะโซน EMA20 (ไม่ลึกเกิน EMA50)"""
    if len(d) < 210:
        return False, 'ข้อมูล H4 ไม่พอ'
    x = d.iloc[-1]
    recent = d.iloc[-H4_ZONE_LOOKBACK:]
    atr = max(float(x.ATR), 1e-9)
    if direction == 'LONG':
        if not (x.EMA20 > x.EMA50 and x.close > x.EMA50 - 0.5 * atr):
            return False, 'H4 ไม่ยืนเหนือ EMA50'
        touched = (recent.low <= recent.EMA20 + H4_ZONE_ATR * atr).any()
        return bool(touched), 'H4 ย่อแตะ EMA20' if touched else 'รอ H4 ย่อลง EMA20'
    if not (x.EMA20 < x.EMA50 and x.close < x.EMA50 + 0.5 * atr):
        return False, 'H4 ไม่อยู่ใต้ EMA50'
    touched = (recent.high >= recent.EMA20 - H4_ZONE_ATR * atr).any()
    return bool(touched), 'H4 เด้งแตะ EMA20' if touched else 'รอ H4 เด้งขึ้น EMA20'


# ---------------- 3) H1 trigger ----------------
def h1_trigger(d, direction):
    if len(d) < 60:
        return False, 0, 'ข้อมูล H1 ไม่พอ'
    x, p = d.iloc[-1], d.iloc[-2]
    rg = max(float(x.range), 1e-9)
    if direction == 'LONG':
        brk = x.close > p.high
        rec = x.close > x.EMA20 and p.close <= p.EMA20
        rej = x.lower_wick >= 0.40 * rg and x.close >= x.low + 0.60 * rg
        dirc = x.close > x.open and x.close > x.EMA20
    else:
        brk = x.close < p.low
        rec = x.close < x.EMA20 and p.close >= p.EMA20
        rej = x.upper_wick >= 0.40 * rg and x.close <= x.high - 0.60 * rg
        dirc = x.close < x.open and x.close < x.EMA20
    ok = bool(dirc and x.body_ratio >= 0.35 and (brk or rec or rej))
    names = [n for n, f in (('break', brk), ('reclaim EMA20', rec), ('rejection', rej)) if f]
    score = 40 * int(brk) + 35 * int(rec) + 25 * int(rej) + 20 * int(dirc)
    return ok, min(100, score), ' + '.join(names) if ok else 'รอ H1 trigger'


# ---------------- Plan ----------------
def swing_plan(h4, h1, direction):
    entry = float(h1.close.iloc[-1])
    atr = max(float(h4.ATR.iloc[-1]), 1e-9)
    if direction == 'LONG':
        sl = float(h4.tail(8).low.min()) - SL_BUFFER_ATR * atr
        risk = entry - sl
    else:
        sl = float(h4.tail(8).high.max()) + SL_BUFFER_ATR * atr
        risk = sl - entry
    if not (MIN_RISK_ATR * atr <= risk <= MAX_RISK_ATR * atr):
        return None
    s = 1 if direction == 'LONG' else -1
    return {'entry': entry, 'sl': sl, 'tp1': entry + s * TP1_R * risk,
            'tp2': entry + s * TP2_R * risk, 'risk': risk, 'atr': atr}


# ---------------- State / LINE ----------------
def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(s):
    STATE.write_text(json.dumps(s, indent=2))


def push(text):
    if DRY_RUN:
        print('----- [DRY RUN] -----\n' + text + '\n---------------------')
        return
    r = requests.post('https://api.line.me/v2/bot/message/push', timeout=15,
                      headers={'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'},
                      json={'to': USER_ID, 'messages': [{'type': 'text', 'text': text}]})
    r.raise_for_status()


def fmt(v):
    return f'{v:,.2f}' if v >= 100 else f'{v:.4f}'


# ---------------- Scan ----------------
def scan_asset(name, symbol, state):
    """คืน list ของ (key, message). ดึงข้อมูลทีละ TF ตามลำดับ ไม่ผ่านก็หยุด"""
    print(f'[SCAN] {name}')
    d1 = ind(fetch(symbol, TF['D1']))
    direction, strength, note = d1_trend(d1)
    print(f'[D1] {direction} strength={strength} | {note}')
    if direction == 'NEUTRAL' or strength < MIN_D1_STRENGTH:
        return []
    if d1_extended(d1, direction):
        print('[SKIP] D1 ยืดเกินไป — รอย่อก่อน')
        return []

    h4 = ind(fetch(symbol, TF['H4']))
    ok_pb, pb_note = h4_pullback(h4, direction)
    print(f'[H4] pullback={ok_pb} | {pb_note}')
    if not ok_pb:
        return []

    h1 = ind(fetch(symbol, TF['H1']))
    age = datetime.now(timezone.utc) - h1.index[-1].to_pydatetime()
    if age > timedelta(hours=MAX_H1_AGE_HOURS):
        print(f'[SKIP] ข้อมูล H1 เก่า ({age}) — ตลาดอาจปิด')
        return []

    arrow = '🟢 LONG' if direction == 'LONG' else '🔴 SHORT'
    head = f'{name} | SWING\n{arrow} (ตาม D1 trend {strength})\n{note}\nH4: {pb_note}'
    out = []

    ok_trig, t_score, t_note = h1_trigger(h1, direction)
    print(f'[H1] trigger={ok_trig} score={t_score} | {t_note}')

    if ok_trig:
        cd_key = f'cd|{symbol}|{direction}'
        last = state.get(cd_key)
        in_cd = last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=COOLDOWN_HOURS)
        p = swing_plan(h4, h1, direction)
        if p and not in_cd:
            sig = f'entry|{symbol}|{direction}|{h1.index[-1]}'
            msg = (f'🚨 {head}\nH1 trigger: {t_note} ({t_score})\n\n'
                   f'ENTRY READY\nEntry: {fmt(p["entry"])}\nSL: {fmt(p["sl"])}\n'
                   f'TP1 ({TP1_R}R): {fmt(p["tp1"])}\nTP2 ({TP2_R}R): {fmt(p["tp2"])}\n'
                   f'Risk: {fmt(p["risk"])} ({p["risk"] / p["atr"]:.1f} ATR H4)\n'
                   f'แผน: ปิดครึ่งที่ TP1 แล้วเลื่อน SL มาทุน / ตามต่อด้วย H4 EMA50')
            out.append((sig, msg, cd_key))
        elif not p:
            print('[PLAN] risk อยู่นอกช่วงที่ยอมรับ — ไม่ส่ง')
        else:
            print('[COOLDOWN] เพิ่งแจ้งทิศนี้ไป — ข้าม')
    else:
        # แจ้งเตือนล่วงหน้า วันละครั้งต่อทิศ: อยู่ในโซนย่อแล้ว รอ H1 trigger
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        sig = f'watch|{symbol}|{direction}|{today}'
        out.append((sig, f'👀 {head}\nอยู่ในโซนย่อแล้ว — รอ H1 trigger (ยังไม่เข้า)', None))
    return out


def main():
    if not DRY_RUN and not within_active_window():
        print(f'[SKIP] นอกเวลาแจ้งเตือน {datetime.now(ACTIVE_TZ):%H:%M %Z}')
        return
    need = [API_KEY] if DRY_RUN else [API_KEY, TOKEN, USER_ID]
    if not all(need):
        raise SystemExit('Missing TWELVEDATA_API_KEY / LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID')
    state, changed = load_state(), False
    for name, symbol in ASSETS.items():
        try:
            alerts = scan_asset(name, symbol, state)
        except Exception as e:
            print(f'{name}: {e}')
            continue
        for sig, msg, cd_key in alerts:
            if sig in state:
                continue
            push(msg)
            now = datetime.now(timezone.utc).isoformat()
            state[sig] = now
            if cd_key:
                state[cd_key] = now
            changed = True
            print('sent', sig)
    if len(state) > 300:
        state = dict(list(state.items())[-200:])
        changed = True
    if changed and not DRY_RUN:
        save_state(state)


if __name__ == '__main__':
    main()
