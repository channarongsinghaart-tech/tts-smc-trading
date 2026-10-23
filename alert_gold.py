"""แจ้งเตือน LINE: ทอง SMA200 (D1 + H4 ทิศเดียวกัน, เข้าเมื่อแท่งกรอบจุดเข้าปิดข้ามเส้น 200)
กรอบจุดเข้าตั้งใน workflow: ENTRY_TF = "M30" หรือ "H1" | REQUIRE_H1 = "1" ให้ H1 อยู่ฝั่งเดียวกันด้วย
SL 1.5 x ATR(H4) | TP 2R | ถือไม่เกิน 48 ชม. — กฎเดียวกับแอป (five200.py)

รันบน GitHub Actions ทุก 15 นาที ทำงานแม้ปิดแอป/มือถือ | สแกนเฉพาะช่วงเวลาที่ตั้ง (ค่าเริ่มต้น 04:00-21:30 เวลา Alberta)
- สัญญาณเข้าเกิดตอนแท่งกรอบจุดเข้าปิด | ตรวจ TP / SL / ครบ 48 ชม. / ใกล้ตลาดปิดวันศุกร์ ทุกแท่ง M15 (แจ้งไวขึ้น)
- ไล่ตรวจทุกแท่งที่พลาดไปถ้า GitHub รันช้า | ไม่นับสัญญาณที่เกิดนอกช่วงเวลา | นอกเวลาไม่ดึงข้อมูล
ใช้ 5 credits ต่อรอบเมื่อเข้าที่ M30 (~350 ต่อวัน), 4 credits เมื่อเข้าที่ H1
"""
import os, sys, json
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import m1_swing_scanner as core
import simple200 as S
import five200 as F

SYMBOL = 'XAU/USD'
BROKER_SYMBOL = os.getenv('BROKER_SYMBOL', 'XAUUSDm')
STATE = Path(os.getenv('ALERT_STATE', 'alert_gold_state.json'))
ENTRY = os.getenv('ENTRY_TF', F.DEFAULT_ENTRY).upper()
REQ_H1 = os.getenv('REQUIRE_H1', '0') == '1' and ENTRY == 'M30'
MINS = F.ENTRY_TFS[ENTRY][1]
BAR = pd.Timedelta(minutes=MINS)
LOCAL_TZ = ZoneInfo(os.getenv('LOCAL_TZ', 'America/Edmonton'))
ACTIVE_START = os.getenv('ACTIVE_START', '04:00')
ACTIVE_END = os.getenv('ACTIVE_END', '21:30')
ACCOUNT_USD = float(os.getenv('ACCOUNT_USD', '0') or 0)
RISK_PCT = float(os.getenv('RISK_PCT', '0.5') or 0.5)
FRESH_MIN = 30
MAX_MOVED_R = 0.5
Q, H = pd.Timedelta(minutes=15), pd.Timedelta(hours=1)


def in_window(t):
    loc = t.astimezone(LOCAL_TZ).time()
    return dtime.fromisoformat(ACTIVE_START) <= loc <= dtime.fromisoformat(ACTIVE_END)


def tstr(ts):
    return f'{ts.tz_convert(LOCAL_TZ):%a %d %b %H:%M} ({ts:%H:%M} UTC)'


def lot_text(risk):
    if ACCOUNT_USD <= 0:
        return f'ขนาดไม้: lot = เงินที่ยอมเสีย ÷ ({risk:.2f} × 100)'
    lot, money, real = F.lot_size(risk, ACCOUNT_USD, RISK_PCT)
    if lot < 0.01:
        return (f'⚠️ ทุน {ACCOUNT_USD:,.0f} USD เสี่ยง {RISK_PCT}% = {money:.2f} USD แต่ไม้ขั้นต่ำ 0.01 lot '
                f'เสี่ยงราว {real:.2f} USD ({real / ACCOUNT_USD * 100:.1f}% ของทุน เกินแผน)')
    return f'ขนาดไม้แนะนำ: {lot:.2f} lot (เสี่ยงจริงราว {real:.2f} USD = {real / ACCOUNT_USD * 100:.2f}% ของทุน)'


def tally(state):
    r = [x['R'] for x in state.get('history', [])]
    return f'\nผลสะสมของระบบ: {len(r)} ไม้ | ชนะ {sum(1 for x in r if x > 0)} | รวม {sum(r):+.1f}R' if r else ''


def prepare(d1, h4, h1, e, m15):
    b = F.build(d1, h4, h1, e, MINS, REQ_H1)
    ep = b['ep']
    entries = {ep.index[i] + BAR: (int(b['sig'][i]), float(ep.close.values[i]), F.SL_ATR_H4 * float(b['atr4'][i]), ep.index[i])
               for i in np.flatnonzero(b['sig'] != 0)}
    return {'ep': ep, 'm15p': S.prep(m15), 'entries': entries, 'side': b['side'], 'trend': b['trend']}


def process(state, data, now_utc, manual=False):
    msgs = []
    m15p, entries = data['m15p'], data['entries']
    idx = m15p.index
    last_seen = pd.Timestamp(state['last_m15']) if state.get('last_m15') else idx[-2]
    for i in np.flatnonzero(idx > last_seen)[-96:]:
        t = idx[i] + Q                                       # เวลาปิดของแท่ง M15 นี้
        pos = state.get('pos')
        if pos:                                              # ---- ตรวจไม้ที่เปิดอยู่ ----
            s = 1 if pos['dir'] == 'LONG' else -1
            risk = abs(pos['entry'] - pos['sl'])
            hi, lo, cl = float(m15p.high.iloc[i]), float(m15p.low.iloc[i]), float(m15p.close.iloc[i])
            hit_sl = lo <= pos['sl'] if s == 1 else hi >= pos['sl']
            hit_tp = hi >= pos['tp'] if s == 1 else lo <= pos['tp']
            held_h = (t - pd.Timestamp(pos['entry_close'])).total_seconds() / 3600
            out = None
            if hit_sl or hit_tp:
                out = ('🛑 โดน SL', -1.0) if hit_sl else ('✅ ถึง TP', F.TP_R)
            elif held_h >= F.MAX_HOLD_H:
                out = ('⏰ ครบ 48 ชม. ให้ปิดไม้ที่ราคาตลาด', s * (cl - pos['entry']) / risk)
            elif t.dayofweek == 4 and t.hour * 60 + t.minute >= 20 * 60:
                out = ('📅 ใกล้ตลาดทองปิดวันศุกร์ (20:58 UTC) ให้ปิดไม้', s * (cl - pos['entry']) / risk)
            if out:
                state.pop('pos')
                state['free_after'] = str((t - Q).floor(f'{MINS}min') + BAR)   # เหมือน Backtest: ไม่เปิดไม้ใหม่ในแท่งเดียวกัน
                state.setdefault('history', []).append({'time': pos['time'], 'dir': pos['dir'],
                                                        'result': out[0], 'R': round(out[1], 3)})
                msgs.append(f'{out[0]} {BROKER_SYMBOL} {pos["dir"]} ({out[1]:+.2f}R) เมื่อ {tstr(t)}{tally(state)}')
            continue
        if t not in entries:                                 # ---- สัญญาณเข้าเกิดเฉพาะตอนแท่งกรอบจุดเข้าปิด ----
            continue
        if state.get('free_after') and t <= pd.Timestamp(state['free_after']):
            continue
        s, entry, risk, bar_open = entries[t]
        if F.no_entry_time(SYMBOL, t) or S.gold_closed(bar_open) or not in_window(t):
            continue
        sl, tp = entry - s * risk, entry + s * F.TP_R * risk
        state['pos'] = {'time': str(bar_open), 'entry_close': str(t), 'dir': 'LONG' if s > 0 else 'SHORT',
                        'entry': entry, 'sl': sl, 'tp': tp}
        age = (now_utc - t).total_seconds() / 60
        price = float(m15p.close.iloc[-1])
        moved = s * (price - entry) / risk
        late = f'\n⏱️ แจ้งช้า {age:.0f} นาที (GitHub รันช้า)' if age > FRESH_MIN else ''
        warn = (f'\n⚠️ ตอนนี้ราคาวิ่งไปแล้ว {moved:+.2f}R เลยจุดเข้าเกินครึ่งทางไป TP แนะนำให้ข้ามไม้นี้'
                if moved > MAX_MOVED_R else '')
        msgs.append(f'{"🟢" if s > 0 else "🔴"} สัญญาณ {"ซื้อ (BUY)" if s > 0 else "ขาย (SELL)"} {BROKER_SYMBOL} (บัญชีเดโม)\n'
                    f'แท่ง {ENTRY} ปิด: {tstr(t)}\nราคาเข้า ~{core.fmt(entry)}\nSL: {core.fmt(sl)}\nTP (2R): {core.fmt(tp)}\n'
                    f'{lot_text(risk)}\n'
                    f'ถ้ายังไม่จบ ให้ปิดภายใน {tstr(t + pd.Timedelta(hours=F.MAX_HOLD_H))} หรือก่อนตลาดปิดวันศุกร์'
                    f'{late}{warn}')
    state['last_m15'] = str(idx[-1])
    if manual:
        word = lambda x: 'เหนือ' if x > 0 else ('ใต้' if x < 0 else '-')
        sd = {tf: int(data['side'][tf][-1]) for tf in ('D1', 'H4', 'H1')}
        sd[ENTRY] = int(data['ep'].side.iloc[-1])
        trend = int(data['trend'][-1])
        pos = state.get('pos')
        msgs.append(f'ℹ️ ระบบแจ้งเตือนทอง ({ENTRY}{" + H1" if REQ_H1 else ""}) ทำงานปกติ ({tstr(idx[-1] + Q)})\n'
                    + ' | '.join(f'{tf} {word(v)}เส้น 200' for tf, v in sd.items()) + '\n'
                    + (f'ทิศที่เล่นได้: {"ซื้อ" if trend > 0 else "ขาย"} รอ {ENTRY} ปิดข้ามเส้น 200' if trend
                       else 'กรอบใหญ่ยังไม่ตรงกัน ยังไม่เล่น')
                    + (f'\nไม้ที่ระบบถืออยู่: {pos["dir"]} เข้า {core.fmt(pos["entry"])} SL {core.fmt(pos["sl"])} '
                       f'TP {core.fmt(pos["tp"])}' if pos else '')
                    + f'\nสแกนช่วง {ACTIVE_START}-{ACTIVE_END} ({LOCAL_TZ.key}) ทุก 15 นาที{tally(state)}')
    return msgs


def main():
    now = pd.Timestamp.now(tz='UTC')
    manual = os.getenv('GITHUB_EVENT_NAME') == 'workflow_dispatch'
    try:
        state = json.loads(STATE.read_text())
    except Exception:
        state = {}
    if not in_window(now) and not manual:
        print(f'[SKIP] นอกช่วงเวลา {now.astimezone(LOCAL_TZ):%H:%M} {LOCAL_TZ.key}')
        STATE.write_text(json.dumps(state, indent=2, default=str))
        return
    if not core.API_KEY:
        sys.exit('Missing TWELVEDATA_API_KEY')
    if not core.DRY_RUN and not (core.TOKEN and core.USER_ID):
        sys.exit('Missing LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID (ตั้งใน GitHub Secrets)')
    h1 = core.fetch(SYMBOL, '1h', 300)
    e = h1 if ENTRY == 'H1' else core.fetch(SYMBOL, F.ENTRY_TFS[ENTRY][0], 300)
    data = prepare(core.fetch(SYMBOL, '1day', 300), core.fetch(SYMBOL, '4h', 300), h1, e,
                   core.fetch(SYMBOL, '15min', 300))
    for m in process(state, data, now, manual=manual):
        core.push(m)
        print('sent:', m.split('\n')[0])
    STATE.write_text(json.dumps(state, indent=2, default=str))
    print(f'[DONE] last M15 {data["m15p"].index[-1]} | pos {"yes" if "pos" in state else "no"}')


if __name__ == '__main__':
    main()
