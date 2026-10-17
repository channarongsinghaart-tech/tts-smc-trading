"""แจ้งเตือน LINE: ทอง SMA200 หลายกรอบ (D1 > H4 > H1 ทิศเดียวกัน, เข้าเมื่อ M15 ปิดข้ามเส้น 200)
SL 1.5 x ATR(H4) | TP 2R | ถือไม่เกิน 48 ชม. — กฎเดียวกับแอป (five200.py)

รันบน GitHub Actions ทุก 15 นาที ทำงานแม้ปิดแอป/มือถือ | สแกนเฉพาะช่วงเวลาที่ตั้ง (ค่าเริ่มต้น 04:00-21:30 เวลา Alberta)
- ไล่ตรวจทุกแท่ง M15 ใหม่ตั้งแต่รอบที่แล้ว จึงไม่พลาดสัญญาณถ้า GitHub รันช้า
- ติดตามไม้ตามระบบทีละ 1 ไม้: แจ้งถึง TP / โดน SL / ครบ 48 ชม. / ใกล้ตลาดปิดวันศุกร์
- นอกช่วงเวลา ไม่ดึงข้อมูล (ไม่เสีย credits) และไม่นับสัญญาณที่เกิดนอกเวลา รอบแรกของวันจะสรุปผลไม้ที่จบระหว่างคืนให้
ใช้ 4 credits ต่อรอบ (~280 ต่อวัน)
"""
import os, sys, json, math
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import m1_swing_scanner as core
import simple200 as S
import five200 as F

SYMBOL = 'XAU/USD'
BROKER_SYMBOL = os.getenv('BROKER_SYMBOL', 'XAUUSDm')
STATE = Path(os.getenv('ALERT_STATE', 'alert_m15_state.json'))
LOCAL_TZ = ZoneInfo(os.getenv('LOCAL_TZ', 'America/Edmonton'))
ACTIVE_START = os.getenv('ACTIVE_START', '04:00')
ACTIVE_END = os.getenv('ACTIVE_END', '21:30')
ACCOUNT_USD = float(os.getenv('ACCOUNT_USD', '0') or 0)
RISK_PCT = float(os.getenv('RISK_PCT', '0.5') or 0.5)
FRESH_MIN = 30                  # แจ้งสัญญาณเข้าเฉพาะแท่งที่ปิดไม่เกิน 30 นาทีก่อน
MAX_MOVED_R = 0.5               # ราคาวิ่งเลยจุดเข้าเกินนี้ แนะนำให้ข้าม
Q = pd.Timedelta(minutes=15)


def in_window(now_utc):
    loc = now_utc.astimezone(LOCAL_TZ).time()
    a, b = dtime.fromisoformat(ACTIVE_START), dtime.fromisoformat(ACTIVE_END)
    return a <= loc <= b


def tstr(ts):
    return f'{ts.tz_convert(LOCAL_TZ):%a %d %b %H:%M} ({ts:%H:%M} UTC)'


def lot_text(risk):
    if ACCOUNT_USD <= 0:
        return f'ขนาดไม้: lot = เงินที่ยอมเสีย ÷ ({risk:.2f} × 100)'
    lot, money = F.lot_size(risk, ACCOUNT_USD, RISK_PCT)
    if lot < 0.01:
        return (f'⚠️ ทุน {ACCOUNT_USD:,.0f} USD เสี่ยง {RISK_PCT}% = {money:.2f} USD แต่ไม้ขั้นต่ำ 0.01 lot '
                f'เสี่ยงราว {risk:.2f} USD (เกินแผน)')
    return f'ขนาดไม้แนะนำ: {lot:.2f} lot (เสี่ยงราว {money:.2f} USD = {RISK_PCT}%)'


def tally(state):
    r = [x['R'] for x in state.get('history', [])]
    if not r:
        return ''
    return f'\nผลสะสมของระบบ: {len(r)} ไม้ | ชนะ {sum(1 for x in r if x > 0)} | รวม {sum(r):+.1f}R'


def no_entry_time(close_t):
    return F.no_entry_time(SYMBOL, close_t)              # กฎเดียวกับ Backtest ในแอป


def process(state, b, now_utc, in_win=True, manual=False):
    """b = five200.build(...) จากข้อมูลล่าสุด | คืนรายการข้อความ"""
    msgs = []
    m15p = b['m15p']
    idx = m15p.index
    last_seen = pd.Timestamp(state['last_bar']) if state.get('last_bar') else idx[-2]
    new = np.flatnonzero(idx > last_seen)[-96:]           # ไล่ทุกแท่งใหม่ (ย้อนได้สูงสุด 1 วัน)
    risk_now = F.SL_ATR_H4 * float(b['atr4'][-1])
    for i in new:
        bar_close = idx[i] + Q
        pos = state.get('pos')
        if pos:                                          # ---- ตรวจไม้ที่เปิดอยู่ ----
            s = 1 if pos['dir'] == 'LONG' else -1
            risk = abs(pos['entry'] - pos['sl'])
            hi, lo, cl = float(m15p.high.iloc[i]), float(m15p.low.iloc[i]), float(m15p.close.iloc[i])
            hit_sl = lo <= pos['sl'] if s == 1 else hi >= pos['sl']
            hit_tp = hi >= pos['tp'] if s == 1 else lo <= pos['tp']
            held_h = (bar_close - pd.Timestamp(pos['entry_close'])).total_seconds() / 3600
            out = None
            if hit_sl or hit_tp:                         # แท่งเดียวชนทั้งคู่ ถือว่า SL (เข้าข้างร้าย)
                out = ('🛑 โดน SL', -1.0) if hit_sl else ('✅ ถึง TP', F.TP_R)
            elif held_h >= F.MAX_HOLD_H:
                out = ('⏰ ครบ 48 ชม. ให้ปิดไม้ที่ราคาตลาด', s * (cl - pos['entry']) / risk)
            elif bar_close.dayofweek == 4 and bar_close.hour * 60 + bar_close.minute >= 20 * 60:
                out = ('📅 ใกล้ตลาดทองปิดวันศุกร์ (20:58 UTC) ให้ปิดไม้', s * (cl - pos['entry']) / risk)
            if out:
                state.pop('pos')
                state.setdefault('history', []).append({'time': pos['time'], 'dir': pos['dir'],
                                                        'result': out[0], 'R': round(out[1], 3)})
                msgs.append(f'{out[0]} {BROKER_SYMBOL} {pos["dir"]} ({out[1]:+.2f}R) เมื่อ {tstr(bar_close)}{tally(state)}')
                continue                                 # แท่งที่เพิ่งปิดไม้ ยังไม่เปิดไม้ใหม่ (เหมือน Backtest)
            continue
        # ---- หาสัญญาณใหม่ ----
        s = int(b['sig'][i])
        if s == 0 or no_entry_time(bar_close) or not in_window(bar_close):
            continue                                     # นอกช่วงเวลาที่คุณเทรด ไม่นับไม้นี้
        entry = float(m15p.close.iloc[i])
        risk = F.SL_ATR_H4 * float(b['atr4'][i])
        sl, tp = entry - s * risk, entry + s * F.TP_R * risk
        state['pos'] = {'time': str(idx[i]), 'entry_close': str(bar_close), 'dir': 'LONG' if s > 0 else 'SHORT',
                        'entry': entry, 'sl': sl, 'tp': tp}
        age_min = (now_utc - bar_close).total_seconds() / 60
        late = f'\n⏱️ แจ้งช้า {age_min:.0f} นาที (GitHub รันช้า)' if age_min > FRESH_MIN else ''
        price = float(m15p.close.iloc[-1])
        moved = s * (price - entry) / risk
        warn = (f'\n⚠️ ตอนนี้ราคาวิ่งไปแล้ว {moved:+.2f}R เลยจุดเข้าเกินครึ่งทางไป TP แนะนำให้ข้ามไม้นี้'
                if moved > MAX_MOVED_R else '')
        side = 'ซื้อ (BUY)' if s > 0 else 'ขาย (SELL)'
        msgs.append(f'{"🟢" if s > 0 else "🔴"} สัญญาณ {side} {BROKER_SYMBOL} (บัญชีเดโม)\n'
                    f'แท่ง M15 ปิด: {tstr(bar_close)}\n'
                    f'ราคาเข้า ~{core.fmt(entry)}\nSL: {core.fmt(sl)}\nTP (2R): {core.fmt(tp)}\n'
                    f'{lot_text(risk)}\n'
                    f'ถ้ายังไม่จบ ให้ปิดภายใน {tstr(bar_close + pd.Timedelta(hours=F.MAX_HOLD_H))} หรือก่อนตลาดปิดวันศุกร์'
                    f'{late}{warn}')
    if len(idx):
        state['last_bar'] = str(idx[-1])
    if manual:
        sd = {tf: int(np.sign(b['side'][tf][-1])) if tf != 'M15' else int(m15p.side.iloc[-1]) for tf in ('D1', 'H4', 'H1', 'M15')}
        word = lambda x: 'เหนือ' if x > 0 else ('ใต้' if x < 0 else '-')
        trend = int(b['trend'][-1])
        pos = state.get('pos')
        msgs.append(f'ℹ️ ระบบแจ้งเตือน M15 ทำงานปกติ ({tstr(idx[-1] + Q)})\n'
                    + ' | '.join(f'{tf} {word(v)}เส้น 200' for tf, v in sd.items()) + '\n'
                    + (f'ทิศที่เล่นได้: {"ซื้อ" if trend > 0 else "ขาย"} รอ M15 ข้ามเส้น' if trend else 'กรอบใหญ่ยังไม่ตรงกัน ยังไม่เล่น')
                    + (f'\nไม้ที่ระบบถืออยู่: {pos["dir"]} เข้า {core.fmt(pos["entry"])} SL {core.fmt(pos["sl"])} '
                       f'TP {core.fmt(pos["tp"])}' if pos else '')
                    + f'\nสแกนช่วง {ACTIVE_START}-{ACTIVE_END} ({LOCAL_TZ.key}) ทุก 15 นาที{tally(state)}')
    return msgs


def main():
    now = pd.Timestamp.now(tz='UTC')
    manual = os.getenv('GITHUB_EVENT_NAME') == 'workflow_dispatch'
    win = in_window(now)
    try:
        state = json.loads(STATE.read_text())
    except Exception:
        state = {}
    if not win and not manual:
        print(f'[SKIP] นอกช่วงเวลา {now.astimezone(LOCAL_TZ):%H:%M} {LOCAL_TZ.key}')
        STATE.write_text(json.dumps(state, indent=2, default=str))
        return
    if not core.API_KEY:
        sys.exit('Missing TWELVEDATA_API_KEY')
    if not core.DRY_RUN and not (core.TOKEN and core.USER_ID):
        sys.exit('Missing LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID (ตั้งใน GitHub Secrets)')
    d1 = core.fetch(SYMBOL, '1day', 300)
    h4 = core.fetch(SYMBOL, '4h', 300)
    h1 = core.fetch(SYMBOL, '1h', 300)
    m15 = core.fetch(SYMBOL, '15min', 300)
    b = F.build(d1, h4, h1, m15)
    for m in process(state, b, now, in_win=win, manual=manual):
        core.push(m)
        print('sent:', m.split('\n')[0])
    STATE.write_text(json.dumps(state, indent=2, default=str))
    print(f'[DONE] last M15 {b["m15p"].index[-1]} | trend {int(b["trend"][-1])} | pos {"yes" if "pos" in state else "no"}')


if __name__ == '__main__':
    main()
