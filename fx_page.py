"""หน้า EUR/USD: ระบบสวิงในกรอบ (Bollinger + ADX) หรือ SMA200 แบบทอง + แผงโครงสร้างตลาด (SMC) + Backtest"""
import math
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

import m1_swing_scanner as core
import simple200 as S
import five200 as F
import btc_smc as B
import swinglab as L
from btc_page import chart, WORD

PAIRS = {'EUR/USD': {'broker': 'EURUSD', 'quote_usd': None, 'spread_pips': 1.2, 'swl': 0.0, 'sws': 0.0}}
PIP, POINT = 0.0001, 0.00001
fmt = lambda v: f'{v:.5f}'
RANGE, TREND = 'สวิงในกรอบ (Bollinger + ADX)', 'SMA200 (แบบเดียวกับทอง)'


def range_state(fr, pair, tp_r):
    """สถานะระบบสวิงในกรอบ (สูตร M2) ณ แท่ง H1 ล่าสุดที่ปิดแล้ว"""
    d1, h4, h1 = (fr[k][['open', 'high', 'low', 'close']] for k in ('D1', 'H4', 'H1'))
    f, h1p = L.features(d1, h4, h1)
    sig, risk = L.model_signals(f, 'M2')
    x = f.iloc[-1]
    out = {'f': f, 'adx4': float(x.adx4), 'range_on': bool(x.adx4 < L.ADX_RANGE), 'up': float(x.bb_up),
           'lo': float(x.bb_lo), 'close': float(x.close), 'signal': None}
    out['mid'] = (out['up'] + out['lo']) / 2
    n = len(f)
    for i in range(n - 1, n - 3, -1):
        t_close = f.index[i] + pd.Timedelta(hours=1)
        if sig[i] != 0 and not S.no_entry_time(pair, t_close):
            s, rk, e = int(sig[i]), float(risk[i]), float(f.close.values[i])
            out['signal'] = {'dir': s, 'time': t_close, 'entry': e, 'risk': rk, 'sl': e - s * rk, 'tp': e + s * tp_r * rk,
                             'close_by': t_close + pd.Timedelta(hours=L.HOLD_H)}
            break
    return out


@st.cache_data(ttl=120, show_spinner=False)
def live_cached(key, pair, entry, ver='fx1'):
    core.API_KEY = key
    res = F.live(pair, entry, False)
    fr = res['frames']
    smc = B.live(pair, frames=(fr['D1'], fr['H4'], fr['H1']), price=res['price'])
    q = PAIRS[pair]['quote_usd']
    rate = float(core.fetch(q, '1h', 5).close.iloc[-1]) if q else 1.0
    return res, smc, rate


@st.cache_data(ttl=3600, show_spinner=False)
def history_cached(key, pair, pages, ver='fx1'):
    core.API_KEY = key
    return S.load_history(pair, int(pages))


def lot_calc(risk_price, contract, quote_usd, balance, ccy, risk_pct):
    """คืน (lot, งบ USD, ความเสี่ยงจริง USD, ต่อ lot USD)"""
    bal_usd = balance / 100 if ccy == 'USC' else balance
    per_lot = risk_price * contract * quote_usd
    money = bal_usd * risk_pct / 100
    if per_lot <= 0 or bal_usd <= 0:
        return None, None, None, None
    lot = math.floor(money / per_lot * 100) / 100
    return lot, money, max(lot, 0.01) * per_lot, per_lot


def money_txt(usd, ccy):
    return f'{usd * 100:,.0f} USC (≈ {usd:,.2f} USD)' if ccy == 'USC' else f'{usd:,.2f} USD'


def render(KEY):
    st.title('💶 EUR/USD SMA200 + โครงสร้างตลาด')
    pair = 'EUR/USD'
    P = PAIRS[pair]
    with st.expander(f'⚙️ ตั้งค่า {pair} (บัญชี ขนาดสัญญา สเปรด ค่าข้ามคืน)'):
        system = st.selectbox('ระบบสัญญาณ', [RANGE, TREND], key='fx_sys')
        tp_r = {'1R': 1.0, '1.5R': 1.5}[st.selectbox('TP ของสวิงในกรอบ', ['1R', '1.5R'], key='fx_tp')]
        entry = st.selectbox('กรอบจุดเข้า (ระบบ SMA200)', list(F.ENTRY_TFS), index=list(F.ENTRY_TFS).index('H1'), key='fx_entry')
        tz = ZoneInfo(st.selectbox('เขตเวลาของคุณ', ['America/Edmonton', 'Asia/Bangkok', 'UTC'], key='fx_tz'))
        ccy = 'USC' if st.radio('ประเภทบัญชี', ['USC (บัญชีเซ็นต์)', 'USD (บัญชีปกติ)'], horizontal=True,
                                key='fx_ccy').startswith('USC') else 'USD'
        balance = st.number_input(f'ยอดเงินในบัญชี ({ccy})', value=30000.0 if ccy == 'USC' else 300.0, step=100.0,
                                  key=f'fx_bal_{ccy}')
        risk_pct = st.number_input('เสี่ยงต่อไม้ (% ของทุน)', value=0.5, step=0.1, format='%.2f', key='fx_risk')
        contract = st.number_input('ขนาดสัญญา (หน่วยต่อ 1 lot) บัญชีเซ็นต์ = 1,000 | ปกติ = 100,000',
                                   value=1000.0 if ccy == 'USC' else 100000.0, step=1000.0, key=f'fx_ct_{ccy}')
        sp = st.number_input('สเปรด (pip)', value=P['spread_pips'], step=0.1, key=f'fx_sp_{pair}')
        swl = st.number_input('Swap long (จุด ตามใน MT5)', value=P['swl'], step=0.5, key=f'fx_swl_{pair}')
        sws = st.number_input('Swap short (จุด ตามใน MT5)', value=P['sws'], step=0.5, key=f'fx_sws_{pair}')
        pages = st.slider('ข้อมูลทดสอบย้อนหลัง (หน้า H1 ละ 5,000 แท่ง ≈ 8 เดือน)', 1, 4, 4, key='fx_pages')
    if system == RANGE:
        st.caption(f'สวิงในกรอบ: ใช้เมื่อ ADX(H4) < 20 (ไม่มีเทรนด์) | H1 หลุดกรอบ Bollinger(20,2) แล้วปิดกลับเข้ามา → เข้าสวนไปหาค่ากลาง '
                   f'| SL 1×ATR(H4) | TP {tp_r:g}R | ถือไม่เกิน 48 ชม. | ระบบทดลอง ดูผลในแท็บทดสอบก่อนใช้')
    else:
        st.caption(f'ระบบ SMA200 เดียวกับทอง: D1 + H4 ฝั่งเดียวกันของเส้น 200 แล้วเข้าเมื่อ {entry} ปิดข้ามเส้น 200 | '
                   'SL 1.5×ATR(H4) | TP 2R | ถือไม่เกิน 48 ชม. | ไม่เข้าช่วงตัดรอบ 20:45-22:00 UTC และชั่วโมงสุดท้ายวันศุกร์')
    S.SPREAD[pair] = float(sp) * PIP
    tstr = lambda ts: f'{ts.tz_convert(tz):%a %d %b %H:%M} ({ts:%H:%M} UTC)'
    side_txt = {1: 'ซื้อ', -1: 'ขาย'}

    def set_swap(price):
        S.SWAP_PCT_DAY[pair] = {'LONG': -float(swl) * POINT / price * 100, 'SHORT': -float(sws) * POINT / price * 100}

    tab_now, tab_bt = st.tabs(['📡 ตอนนี้', '🧪 ทดสอบย้อนหลัง'])
    with tab_now:
        if st.button('🔄 อัปเดตข้อมูลล่าสุด', use_container_width=True, key='fx_refresh'):
            live_cached.clear()
        try:
            with st.spinner(f'กำลังดึงข้อมูล {pair} ...'):
                res, smc, q2usd = live_cached(KEY, pair, entry)
            stt, trend, sig = res['state'], res['trend'], res['signal']
            set_swap(res['price'])
            st.caption(f'แท่ง {entry} ล่าสุดที่ปิดแล้ว: {tstr(res["entry_close"])} | ราคาล่าสุด {fmt(res["price"])} '
                       f'| ข้อมูลเก็บไว้ 2 นาที')
            if S.fx_closed(res['time'] - pd.Timedelta(minutes=1)):
                st.info('🌙 ตลาด Forex ปิดอยู่ (เปิดอาทิตย์ 21:05 UTC)')
            rs = range_state(res['frames'], pair, tp_r)
            if system == RANGE:
                rsig = rs['signal']
                if rsig is not None:
                    moved = rsig['dir'] * (res['price'] - rsig['entry']) / rsig['risk']
                    if moved <= 0.5:
                        box = st.success if rsig['dir'] > 0 else st.error
                        box(f'### {"🟢 สัญญาณซื้อ (BUY)" if rsig["dir"] > 0 else "🔴 สัญญาณขาย (SELL)"} {P["broker"]} — สวิงในกรอบ\n'
                            f'ปิดกลับเข้ากรอบ Bollinger เมื่อ {tstr(rsig["time"])} | ราคาไปแล้ว {moved:+.2f}R')
                    else:
                        st.warning(f'### ⚠️ สัญญาณ{side_txt[rsig["dir"]]}เกิดแล้ว แต่ราคาวิ่งไป {moved:+.2f}R\nแนะนำให้ข้ามไม้นี้')
                elif not rs['range_on']:
                    st.warning(f'### ⛔ ยังไม่เล่น: ตลาดมีเทรนด์\nADX(H4) = {rs["adx4"]:.1f} (ต้องต่ำกว่า 20 ถึงจะเทรดสวิงในกรอบ)')
                elif rs['close'] > rs['up']:
                    st.info(f'### ⏳ หลุดกรอบบนแล้ว รอปิดกลับเข้ามา → ขาย\nรอแท่ง H1 ปิดเป็นแท่งแดงใต้ {fmt(rs["up"])} | ADX(H4) {rs["adx4"]:.1f}')
                elif rs['close'] < rs['lo']:
                    st.info(f'### ⏳ หลุดกรอบล่างแล้ว รอปิดกลับเข้ามา → ซื้อ\nรอแท่ง H1 ปิดเป็นแท่งเขียวเหนือ {fmt(rs["lo"])} | ADX(H4) {rs["adx4"]:.1f}')
                else:
                    st.info(f'### ⏳ ตลาดไม่มีเทรนด์ รอราคาหลุดกรอบ\nกรอบบน {fmt(rs["up"])} | กลาง {fmt(rs["mid"])} | ล่าง {fmt(rs["lo"])} '
                            f'| ADX(H4) {rs["adx4"]:.1f}')
            elif sig is not None and sig['moved_R'] <= 0.5:
                box = st.success if sig['dir'] > 0 else st.error
                box(f'### {"🟢 สัญญาณซื้อ (BUY)" if sig["dir"] > 0 else "🔴 สัญญาณขาย (SELL)"} {P["broker"]}\n'
                    f'แท่ง {entry} ปิดเมื่อ {tstr(sig["time"])} | ราคาไปแล้ว {sig["moved_R"]:+.2f}R จากจุดเข้า')
            elif sig is not None:
                st.warning(f'### ⚠️ สัญญาณ{side_txt[sig["dir"]]}เกิดแล้ว แต่ราคาวิ่งไป {sig["moved_R"]:+.2f}R\nแนะนำให้ข้ามไม้นี้')
            elif trend:
                if stt[entry]['side'] == trend:
                    st.info(f'### ⏳ ทิศ{side_txt[trend]}: รอรอบถัดไป\n{entry} อยู่ฝั่ง{side_txt[trend]}แล้ว '
                            f'รอให้ย่อไปอีกฝั่งของเส้น 200 แล้วปิดข้ามกลับมา')
                else:
                    st.info(f'### ⏳ ทิศ{side_txt[trend]}: รอ {entry} ปิดข้ามเส้น 200 กลับมา')
            else:
                st.warning('### ⛔ ยังไม่เล่น (ระบบ SMA200)\nD1 กับ H4 อยู่คนละฝั่งเส้น 200')

            rows = ['| กรอบ | เส้น 200 | ราคา | SMA200 | ห่าง (ATR) | โครงสร้าง |', '|---|---|---|---|---|---|']
            for tf in ('D1', 'H4', 'H1', 'M30'):
                x = stt[tf]
                mark = '🟢 เหนือ' if x['side'] > 0 else ('🔴 ใต้' if x['side'] < 0 else '⚪ -')
                stc = WORD[smc['struct'][tf]].split(' (')[0] if tf in smc['struct'] else '-'
                rows.append(f'| {tf} | {mark} | {fmt(x["close"])} | {fmt(x["sma"]) if x["sma"] == x["sma"] else "-"} | '
                            f'{x["dist_atr"]:+.1f} | {stc} |')
            st.markdown('\n'.join(rows))

            levels = None
            if system == RANGE and rs['signal'] is not None:
                rsig = rs['signal']
                lot, money, real, per_lot = lot_calc(rsig['risk'], contract, q2usd, balance, ccy, risk_pct)
                plan = ['**แผนเทรดของสัญญาณนี้ (สวิงในกรอบ)**', f'- ราคาเข้า: **{fmt(rsig["entry"])}**',
                        f'- SL: **{fmt(rsig["sl"])}** ({rsig["risk"] / PIP:.1f} pip)',
                        f'- TP {tp_r:g}R: **{fmt(rsig["tp"])}** ({tp_r * rsig["risk"] / PIP:.1f} pip) | ค่ากลางกรอบ {fmt(rs["mid"])}',
                        f'- ถ้ายังไม่ถึง TP/SL ให้ปิดภายใน: {tstr(rsig["close_by"])} หรือก่อนตลาดปิดวันศุกร์']
                if lot is not None:
                    plan.append(f'- ขนาดไม้แนะนำ: **{max(lot, 0.01):.2f} lot** เสี่ยงจริงราว {money_txt(real, ccy)}'
                                + (' ⚠️ เกินแผน' if lot < 0.01 else ''))
                st.markdown('\n'.join(plan))
                levels = [('Entry', rsig['entry'], 'white'), ('SL', rsig['sl'], '#ef5350'), ('TP', rsig['tp'], '#26a69a')]
            elif system == TREND and (trend or sig is not None):
                s = sig['dir'] if sig is not None else trend
                e = sig['entry'] if sig is not None else res['price']
                rk = sig['risk'] if sig is not None else res['risk']
                sl, tp = e - s * rk, e + s * F.TP_R * rk
                lot, money, real, per_lot = lot_calc(rk, contract, q2usd, balance, ccy, risk_pct)
                plan = [f'**{"แผนเทรดของสัญญาณนี้" if sig is not None else f"ถ้าเกิดสัญญาณ{side_txt[s]}ที่ราคาตอนนี้"}**',
                        f'- ราคาเข้า: **{fmt(e)}**', f'- SL: **{fmt(sl)}** ({rk / PIP:.1f} pip)',
                        f'- TP 2R: **{fmt(tp)}** ({2 * rk / PIP:.1f} pip)']
                if sig is not None:
                    plan.append(f'- ถ้ายังไม่ถึง TP/SL ให้ปิดภายใน: {tstr(sig["close_by"])} หรือก่อนตลาดปิดวันศุกร์')
                if lot is not None:
                    plan.append(f'- ขนาดไม้แนะนำ: **{max(lot, 0.01):.2f} lot** เสี่ยงจริงราว {money_txt(real, ccy)} '
                                f'({real / (balance / 100 if ccy == "USC" else balance) * 100:.2f}% ของทุน)'
                                + (' ⚠️ เกินแผน' if lot < 0.01 else ''))
                st.markdown('\n'.join(plan))
                levels = [('Entry', e, 'white'), ('SL', sl, '#ef5350'), ('TP', tp, '#26a69a')]

            st.write(f'**แผงโครงสร้างตลาด (SMC) — คะแนน D1+H4+H1: {smc["score"]:+d}**')
            a4 = smc['atr']['H4']
            lv = []
            for z in reversed(smc['resistance']):
                lv.append(f'- 🔺 แนวต้าน {fmt(z[0])}{" – " + fmt(z[1]) if z[1] > z[0] else ""} '
                          f'(+{(z[0] - smc["price"]) / PIP:.0f} pip)')
            lv.append(f'- ▶️ ราคาตอนนี้ **{fmt(smc["price"])}**')
            for z in smc['support']:
                lv.append(f'- 🔻 แนวรับ {fmt(z[0])}{" – " + fmt(z[1]) if z[1] > z[0] else ""} '
                          f'({(z[1] - smc["price"]) / PIP:.0f} pip)')
            lv.append('- ' + ' | '.join(f'{k} {fmt(v)}' for k, v in smc['refs'].items()))
            for kind, level, _, _ in smc['pools']:
                lv.append(f'- 💧 จุดรวมสต็อป{"ด้านบน" if kind == "high" else "ด้านล่าง"} {fmt(level)}')
            for t, level, txt, _ in smc['sweeps'][-2:]:
                lv.append(f'- 🧹 {txt} ที่ {fmt(level)} เมื่อ {tstr(t + pd.Timedelta(hours=1))}')
            if smc['signal'] is not None:
                ss = smc['signal']
                lv.append(f'- 🧪 สัญญาณ SMC (ทดลอง ยังไม่ผ่านการทดสอบ): {"ซื้อ" if ss["dir"] > 0 else "ขาย"} '
                          f'เข้า {fmt(ss["entry"])} SL {fmt(ss["sl"])} TP {fmt(ss["tp"])}')
            st.markdown('\n'.join(lv))

            zones = [(z[0], z[1], z[2], '#ef5350') for z in smc['resistance']] + \
                    [(z[0], z[1], z[2], '#26a69a') for z in smc['support']]
            fr = res['frames']
            etf = 'H1' if system == RANGE else entry
            other = 'M30' if etf == 'H1' else 'H1'
            c1, c2, c3, c4 = st.tabs([f'{etf} (จุดเข้า)', other, 'H4', 'D1'])
            with c1:
                if system == RANGE:
                    fig = chart(fr['H1'], 150, f'{pair} H1 + Bollinger(20,2)', zones, levels=levels)
                    ff = rs['f'].tail(150)
                    for col, dash in (('bb_up', 'dash'), ('bb_lo', 'dash')):
                        fig.add_trace(go.Scatter(x=ff.index, y=ff[col], mode='lines', line=dict(color='#9e9e9e', width=1, dash=dash)))
                    fig.add_trace(go.Scatter(x=ff.index, y=(ff.bb_up + ff.bb_lo) / 2, mode='lines',
                                             line=dict(color='#9e9e9e', width=1, dash='dot')))
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.plotly_chart(chart(fr[entry], 150, f'{pair} {entry}', zones,
                                          smc['pools'] if entry == 'H1' else (), smc['labels_h1'] if entry == 'H1' else (),
                                          smc['events_h1'] if entry == 'H1' else (), smc['sweeps'] if entry == 'H1' else (),
                                          levels), use_container_width=True)
            with c2:
                st.plotly_chart(chart(fr[other], 150, f'{pair} {other}', zones, levels=levels), use_container_width=True)
            with c3:
                st.plotly_chart(chart(fr['H4'], 150, f'{pair} H4', zones, (), smc['labels_h4'], smc['events_h4']),
                                use_container_width=True)
            with c4:
                st.plotly_chart(chart(fr['D1'], 200, f'{pair} D1', zones), use_container_width=True)
            st.caption('เส้นส้ม = SMA200 | แถบเขียว/แดง = แนวรับ/แนวต้าน | เส้นม่วง = จุดรวมสต็อป | ▲▼ = กวาดสต็อป | '
                       'ใช้บัญชีเดโมก่อนเสมอ')
        except Exception as e:
            st.error(f'ดึงข้อมูลไม่สำเร็จ: {e}')

    with tab_bt:
        st.write('**🔄 สวิงในกรอบ (Bollinger + ADX)**')
        st.caption('สูตร M2 จากห้องทดลอง ตัวเลขเดิมไม่ปรับ | TP 1R และ 1.5R | ข้อมูล H1 ~2.5 ปี')
        if st.button('▶️ ทดสอบสวิงในกรอบ', use_container_width=True, key='fx_bt_range'):
            try:
                with st.spinner('กำลังจำลองเทรด ...'):
                    d1, h4, h1 = history_cached(KEY, pair, pages)
                    set_swap(float(h1.close.iloc[-1]))
                    cache = L.features(d1, h4, h1)
                    rsets = {f'สวิงในกรอบ TP {tp:g}R': L.run_model(pair, d1, h4, h1, 'M2', tp, cache=cache) for tp in (1.0, 1.5)}
                    rtab = pd.DataFrame([F._row(k, v) for k, v in rsets.items()])
                st.caption(f'ช่วงข้อมูล H1: {h1.index[0]:%d %b %Y} → {h1.index[-1]:%d %b %Y} (70% แรก = พัฒนา, 30% หลัง = ทดสอบ)')
                st.dataframe(rtab, use_container_width=True, hide_index=True)
                fig = go.Figure()
                for label, t in rsets.items():
                    if len(t):
                        tt = t.sort_values('time')
                        fig.add_trace(go.Scatter(x=tt.exit_time, y=tt.R_net.cumsum(), mode='lines', name=label))
                fig.update_layout(height=300, margin=dict(l=0, r=0, t=30, b=0), legend=dict(orientation='h', y=-0.25),
                                  title=dict(text='กำไรสะสม (R หักต้นทุน)', x=0))
                st.plotly_chart(fig, use_container_width=True)
                st.caption('เชื่อได้เมื่อ R สุทธิบวกทั้งช่วงพัฒนาและทดสอบ | ถ้าไม่ผ่านทั้งสองแบบ ไม่ควรเทรด EUR/USD ด้วยสูตรนี้')
                for k, (label, t) in enumerate(rsets.items()):
                    if len(t):
                        st.download_button(f'ดาวน์โหลด CSV: {label}', t.to_csv(index=False), f'EURUSD_range_{k + 1}.csv',
                                           key=f'fx_rdl_{k}')
            except Exception as e:
                st.error(f'ทดสอบไม่สำเร็จ: {e}')
        st.divider()
        st.write('**📏 SMA200 และ SMC (ผลเดิม: ขาดทุนทั้ง 3 แบบ)**')
        st.caption(f'ทดสอบ {pair} ด้วยกฎเดิมทุกตัวเลข (ไม่ปรับให้เข้ากับคู่เงิน) = ทดสอบข้ามตลาดของระบบทองและ BTC '
                   '| ข้อมูล H1 ~2.5 ปี | ครั้งแรกรอ 2-4 นาที')
        if st.button(f'▶️ ทดสอบ {pair}', use_container_width=True, key='fx_bt'):
            try:
                with st.spinner('กำลังดึงข้อมูลและจำลองเทรด ...'):
                    d1, h4, h1 = history_cached(KEY, pair, pages)
                    set_swap(float(h1.close.iloc[-1]))
                    base = F.run_entry(pair, d1, h4, h1, h1, 'H1')
                    tab_smc, sets_smc = B.compare((d1, h4, h1), symbol=pair)
                    sets = {'SMA200 เข้าที่ H1 (ระบบเดียวกับทอง)': base, **sets_smc}
                    table = pd.concat([pd.DataFrame([F._row('SMA200 เข้าที่ H1 (ระบบเดียวกับทอง)', base)]), tab_smc],
                                      ignore_index=True)
                st.caption(f'ช่วงข้อมูล H1: {h1.index[0]:%d %b %Y} → {h1.index[-1]:%d %b %Y} (70% แรก = พัฒนา, 30% หลัง = ทดสอบ)')
                st.dataframe(table, use_container_width=True, hide_index=True)
                fig = go.Figure()
                for label, t in sets.items():
                    if len(t):
                        tt = t.sort_values('time')
                        fig.add_trace(go.Scatter(x=tt.exit_time, y=tt.R_net.cumsum(), mode='lines', name=label))
                fig.update_layout(height=320, margin=dict(l=0, r=0, t=30, b=0), legend=dict(orientation='h', y=-0.25),
                                  title=dict(text='กำไรสะสม (R หักต้นทุน)', x=0))
                st.plotly_chart(fig, use_container_width=True)
                st.caption('เชื่อได้เมื่อ R สุทธิบวกทั้งช่วงพัฒนาและทดสอบ | ถ้าระบบ SMA200 บวกกับคู่เงินนี้ด้วย หลักฐานของระบบทองจะแข็งขึ้น')
                with st.expander('รายการเทรดทั้งหมด'):
                    for k, (label, t) in enumerate(sets.items()):
                        st.write(f'**{label}**')
                        st.dataframe(t, use_container_width=True)
                        if len(t):
                            st.download_button(f'ดาวน์โหลด CSV: {label}', t.to_csv(index=False),
                                               f'{pair.replace("/", "")}_trades_{k + 1}.csv', key=f'fx_dl_{k}')
            except Exception as e:
                st.error(f'ทดสอบไม่สำเร็จ: {e}')
