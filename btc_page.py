"""หน้า BTC: Market Structure + แนวรับแนวต้าน + Liquidity & Stop Run + Multi-Timeframe พร้อมจุดเข้าและ Backtest"""
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

import m1_swing_scanner as core
import simple200 as S
import btc_smc as B

WORD = {1: '🟢 ขาขึ้น (HH+HL)', -1: '🔴 ขาลง (LH+LL)', 0: '⚪ ไซด์เวย์'}


@st.cache_data(ttl=120, show_spinner=False)
def live_cached(key, ver='btc1'):
    core.API_KEY = key
    return B.live()


@st.cache_data(ttl=3600, show_spinner=False)
def history_cached(key, pages, ver='btc1'):
    core.API_KEY = key
    return S.load_history(B.SYMBOL, int(pages))


def chart(df, n, title, zones=(), pools=(), labels=(), events=(), sweeps=(), levels=None):
    d = df.tail(n)
    t0 = d.index[0]
    fig = go.Figure(go.Candlestick(x=d.index, open=d.open, high=d.high, low=d.low, close=d.close, name=title,
                                   increasing_line_color='#26a69a', decreasing_line_color='#ef5350'))
    fig.add_trace(go.Scatter(x=d.index, y=d.SMA, mode='lines', name='SMA200', line=dict(color='orange', width=1.5)))
    lo, hi = float(d.low.min()), float(d.high.max())
    pad = (hi - lo) * 0.15
    for z0, z1, cnt, color in zones:
        if z1 >= lo - pad and z0 <= hi + pad:
            h = max(z1 - z0, (hi - lo) * 0.004)
            fig.add_shape(type='rect', x0=t0, x1=d.index[-1], y0=z0 - h / 2 if z0 == z1 else z0,
                          y1=z1 + h / 2 if z0 == z1 else z1, fillcolor=color, opacity=0.18, line_width=0)
    for kind, level, _, _ in pools:
        if lo - pad <= level <= hi + pad:
            fig.add_hline(y=level, line_color='#ab47bc', line_dash='dash', line_width=1,
                          annotation_text='Liquidity ' + ('บน' if kind == 'high' else 'ล่าง'), annotation_position='left')
    lab = [x for x in labels if x[0] >= t0]
    if lab:
        fig.add_trace(go.Scatter(x=[x[0] for x in lab], y=[x[1] for x in lab], mode='text', text=[x[2] for x in lab],
                                 textposition=['top center' if x[3] == 'high' else 'bottom center' for x in lab],
                                 textfont=dict(size=10, color='#bbbbbb'), name='สวิง'))
    ev = [x for x in events if x[0] >= t0]
    if ev:
        fig.add_trace(go.Scatter(x=[x[0] for x in ev], y=[x[1] for x in ev], mode='markers+text', text=[x[2] for x in ev],
                                 textposition='middle right', marker=dict(symbol='line-ew', size=14, line_width=2,
                                 color='#42a5f5'), textfont=dict(size=10, color='#42a5f5'), name='BOS/CHoCH'))
    sw = [x for x in sweeps if x[0] >= t0]
    if sw:
        fig.add_trace(go.Scatter(x=[x[0] for x in sw], y=[x[1] for x in sw], mode='markers',
                                 marker=dict(symbol=['triangle-up' if x[3] == 1 else 'triangle-down' for x in sw], size=11,
                                 color=['#26a69a' if x[3] == 1 else '#ef5350' for x in sw]), name='กวาดสต็อป'))
    for name, y, color in (levels or []):
        fig.add_hline(y=y, line_color=color, line_dash='dot', annotation_text=name, annotation_position='right')
    fig.update_layout(height=420, margin=dict(l=0, r=0, t=30, b=0), title=dict(text=title, x=0, font=dict(size=14)),
                      xaxis_rangeslider_visible=False, showlegend=False, yaxis=dict(range=[lo - pad, hi + pad]))
    return fig


def render(KEY):
    st.title('₿ BTC โครงสร้างตลาด (SMC)')
    st.caption('Market Structure + แนวรับแนวต้าน + Liquidity & Stop Run + Multi-Timeframe | จุดเข้า: H4 ตามทิศ → '
               'H1 กวาดสต็อป → CHoCH ภายใน 12 ชม. | SL ใต้ไส้ที่กวาด | TP 2R | ถือไม่เกิน 48 ชม.')
    with st.expander('⚙️ ตั้งค่า BTC (ทุน ความเสี่ยง ขนาดสัญญา สเปรด ค่าข้ามคืน)'):
        tz = ZoneInfo(st.selectbox('เขตเวลาของคุณ', ['America/Edmonton', 'Asia/Bangkok', 'UTC'], key='btc_tz'))
        account = st.number_input('ทุนในบัญชี (USD)', value=5000.0, step=500.0, key='btc_acc')
        risk_pct = st.number_input('เสี่ยงต่อไม้ (% ของทุน)', value=0.5, step=0.1, format='%.2f', key='btc_risk')
        contract = st.number_input('ขนาดสัญญา (BTC ต่อ 1 lot) ดูใน MT5 → BTCUSDm → ข้อมูลจำเพาะ', value=1.0, step=0.1,
                                   key='btc_ct')
        spread = st.number_input('สเปรด BTC (หน่วยราคา)', value=6.4, step=0.5, key='btc_sp')
        swl = st.number_input('ค่าข้ามคืน ฝั่งซื้อ (% ต่อคืน)', value=0.0, step=0.001, format='%.4f', key='btc_swl')
        sws = st.number_input('ค่าข้ามคืน ฝั่งขาย (% ต่อคืน)', value=0.0, step=0.001, format='%.4f', key='btc_sws')
        use_sr = st.checkbox('ใช้แบบมีแนวรับ/แนวต้าน (S/R) เป็นแบบหลัก', value=False, key='btc_use_sr')
        pages = st.slider('ข้อมูลทดสอบย้อนหลัง (หน้า H1 ละ 5,000 แท่ง ≈ 7 เดือน)', 1, 4, 4, key='btc_pages')
    S.SPREAD['BTC/USD'] = float(spread)
    S.SWAP_PCT_DAY['BTC/USD'] = {'LONG': float(swl), 'SHORT': float(sws)}
    tstr = lambda ts: f'{ts.tz_convert(tz):%a %d %b %H:%M} ({ts:%H:%M} UTC)'
    fmt = core.fmt

    tab_now, tab_bt = st.tabs(['📡 ตอนนี้', '🧪 ทดสอบย้อนหลัง'])
    with tab_now:
        if st.button('🔄 อัปเดตข้อมูลล่าสุด', use_container_width=True, key='btc_refresh'):
            live_cached.clear()
        try:
            with st.spinner('กำลังดึงข้อมูล BTC D1 / H4 / H1 ...'):
                r = live_cached(KEY)
            st.caption(f'แท่ง H1 ล่าสุดที่ปิดแล้ว: {tstr(r["time"])} | ราคาล่าสุด {fmt(r["price"])} | ข้อมูลเก็บไว้ 2 นาที')
            sig, pend, bias = r['signal'], r['pending'], r['bias']
            a4 = r['atr']['H4']
            if sig is not None and (not use_sr or sig['sr']) and sig['moved_R'] <= 0.5:
                box = st.success if sig['dir'] > 0 else st.error
                box(f'### {"🟢 สัญญาณซื้อ (BUY)" if sig["dir"] > 0 else "🔴 สัญญาณขาย (SELL)"}'
                    f'{" ✓ ผ่าน S/R" if sig["sr"] else ""}\nกวาดสต็อปที่ {fmt(sig["info"]["swept_level"])} แล้ว CHoCH '
                    f'ทะลุ {fmt(sig["info"]["choch_level"])} | แท่ง H1 ปิด {tstr(sig["time"])} | ราคาไปแล้ว {sig["moved_R"]:+.2f}R')
            elif sig is not None and (not use_sr or sig['sr']):
                st.warning(f'### ⚠️ สัญญาณ{"ซื้อ" if sig["dir"] > 0 else "ขาย"}เกิดแล้ว แต่ราคาวิ่งไป {sig["moved_R"]:+.2f}R\n'
                           'เลยจุดเข้าเกินครึ่งทางไป TP แนะนำให้ข้ามไม้นี้')
            elif pend is not None:
                st.info(f'### ⏳ กวาดสต็อปแล้ว รอ CHoCH {"↑" if pend["dir"] > 0 else "↓"}\nกวาดที่ {fmt(pend["level"])} '
                        f'เมื่อ {tstr(pend["sweep_time"] + pd.Timedelta(hours=1))} | รอแท่ง H1 ปิด'
                        f'{"เหนือ" if pend["dir"] > 0 else "ใต้"} **{fmt(pend["trigger"])}** ภายใน {pend["bars_left"]} ชม.')
            elif bias:
                sh, sl = r['last_sw']['H1']
                lvl = sl if bias == 'ซื้อ' else sh
                st.info(f'### ⏳ มุมมอง{bias}: รอ H1 กวาดสต็อป\nรอราคาแทง{"ลงใต้" if bias == "ซื้อ" else "ขึ้นเหนือ"} '
                        f'สวิง{"ต่ำ" if bias == "ซื้อ" else "สูง"}ล่าสุดของ H1 ที่ **{fmt(lvl)}** แล้วปิดกลับ จากนั้นรอ CHoCH')
            else:
                st.warning('### ⛔ ยังไม่เล่น\nโครงสร้าง H4 เป็นไซด์เวย์ หรือ D1 สวนทิศกับ H4')

            sc = r['score']
            st.write(f'**คะแนนโครงสร้าง D1+H4+H1: {sc:+d}** '
                     f'({"เอียงขึ้นชัด" if sc >= 2 else "เอียงขึ้น" if sc == 1 else "เอียงลงชัด" if sc <= -2 else "เอียงลง" if sc == -1 else "กลางๆ"})')
            rows = ['| กรอบ | โครงสร้าง | เส้น 200 | สวิงสูงล่าสุด | สวิงต่ำล่าสุด |', '|---|---|---|---|---|']
            for tf in ('D1', 'H4', 'H1'):
                sh, sl = r['last_sw'][tf]
                rows.append(f'| {tf} | {WORD[r["struct"][tf]]} | {"เหนือ" if r["sma"][tf] > 0 else "ใต้"} | '
                            f'{fmt(sh) if sh == sh else "-"} | {fmt(sl) if sl == sl else "-"} |')
            st.markdown('\n'.join(rows))

            lv = ['**ระดับราคาสำคัญ** (ห่างจากราคาตอนนี้ เป็น ATR ของ H4)']
            for z in reversed(r['resistance']):
                lv.append(f'- 🔺 แนวต้าน {fmt(z[0])}{" – " + fmt(z[1]) if z[1] > z[0] else ""} (ชน {z[2]} ครั้ง, '
                          f'+{(z[0] - r["price"]) / a4:.1f} ATR)')
            lv.append(f'- ▶️ ราคาตอนนี้ **{fmt(r["price"])}**')
            for z in r['support']:
                lv.append(f'- 🔻 แนวรับ {fmt(z[0])}{" – " + fmt(z[1]) if z[1] > z[0] else ""} (ชน {z[2]} ครั้ง, '
                          f'{(z[1] - r["price"]) / a4:.1f} ATR)')
            lv.append('- ' + ' | '.join(f'{k} {fmt(v)}' for k, v in r['refs'].items()))
            st.markdown('\n'.join(lv))
            liq = ['**Liquidity & Stop Run**']
            if r['pools']:
                for kind, level, _, _ in r['pools']:
                    liq.append(f'- 💧 จุดรวมสต็อป{"ด้านบน (High เท่ากัน)" if kind == "high" else "ด้านล่าง (Low เท่ากัน)"} '
                               f'{fmt(level)} ยังไม่ถูกกวาด')
            else:
                liq.append('- ไม่พบ High/Low เท่ากันที่ยังไม่ถูกกวาดใน 120 ชม. ล่าสุด')
            for t, level, txt, _ in r['sweeps'][-3:]:
                liq.append(f'- 🧹 {txt} ที่ {fmt(level)} เมื่อ {tstr(t + pd.Timedelta(hours=1))}')
            st.markdown('\n'.join(liq))

            levels = None
            if sig is not None:
                lot, money, real = B.lot_size(sig['risk'], account, risk_pct, contract)
                plan = ['**แผนเทรดของสัญญาณนี้**', f'- ราคาเข้า: **{fmt(sig["entry"])}**',
                        f'- SL: **{fmt(sig["sl"])}** (ห่าง {fmt(sig["risk"])})', f'- TP 2R: **{fmt(sig["tp"])}**',
                        f'- ถ้ายังไม่ถึง TP/SL ให้ปิดภายใน: {tstr(sig["close_by"])}']
                if lot is not None:
                    plan.append(f'- ขนาดไม้แนะนำ: **{max(lot, 0.01):.2f} lot** เสี่ยงจริงราว {real:,.2f} USD '
                                f'({real / account * 100:.2f}% ของทุน)' + (' ⚠️ เกินแผน' if lot < 0.01 else ''))
                st.markdown('\n'.join(plan))
                levels = [('Entry', sig['entry'], 'white'), ('SL', sig['sl'], '#ef5350'), ('TP', sig['tp'], '#26a69a')]
            zones = [(z[0], z[1], z[2], '#ef5350') for z in r['resistance']] + [(z[0], z[1], z[2], '#26a69a') for z in r['support']]
            c1, c2, c3 = st.tabs(['H1 (จุดเข้า)', 'H4', 'D1'])
            with c1:
                st.plotly_chart(chart(r['h1p'], 150, 'BTC/USD H1', zones, r['pools'], r['labels_h1'], r['events_h1'],
                                      r['sweeps'], levels), use_container_width=True)
            with c2:
                st.plotly_chart(chart(r['h4p'], 150, 'BTC/USD H4', zones, (), r['labels_h4'], r['events_h4']),
                                use_container_width=True)
            with c3:
                st.plotly_chart(chart(r['d1p'], 200, 'BTC/USD D1', zones), use_container_width=True)
            st.caption('แถบเขียว/แดง = โซนแนวรับ/แนวต้าน | เส้นม่วง = จุดรวมสต็อป | ▲▼ = กวาดสต็อป | เส้นฟ้า = BOS/CHoCH | '
                       'HH/HL/LH/LL = จุดสวิง | เส้นส้ม = SMA200 | ใช้บัญชีเดโมก่อนเสมอ')
        except Exception as e:
            st.error(f'ดึงข้อมูลไม่สำเร็จ: {e}')

    with tab_bt:
        st.caption('ทดสอบกฎจุดเข้า BTC บนข้อมูล H1 ย้อนหลัง ~2 ปี หักสเปรดและค่าข้ามคืนแล้ว | ครั้งแรกรอ 2-4 นาที')
        if st.button('▶️ ทดสอบกฎ BTC', use_container_width=True, key='btc_bt'):
            try:
                with st.spinner('กำลังดึงข้อมูลและจำลองเทรด ...'):
                    frames = history_cached(KEY, pages)
                    table, sets = B.compare(frames)
                h1 = frames[2]
                st.caption(f'ช่วงข้อมูล H1: {h1.index[0]:%d %b %Y} → {h1.index[-1]:%d %b %Y} (70% แรก = พัฒนา, 30% หลัง = ทดสอบ)')
                main = sets[B.NAMES[use_sr]]
                if len(main):
                    sm = S.stats(main)
                    st.write(f'**แบบหลักที่ตั้งไว้: {B.NAMES[use_sr]}**')
                    a1, b1, c1 = st.columns(3)
                    a1.metric('เทรด', sm['เทรด'])
                    b1.metric('ชนะ', f'{sm["ชนะ %"]}%')
                    c1.metric('R ต่อเทรด (สุทธิ)', f'{sm["R/เทรด สุทธิ"]:+.2f}')
                    a1, b1, c1 = st.columns(3)
                    a1.metric('R รวม', f'{sm["R รวม"]:+.1f}')
                    b1.metric('PF', sm['PF'])
                    c1.metric('ขาดทุนสะสมสูงสุด', f'{sm["ขาดทุนสะสมสูงสุด R"]}R')
                    st.write(f'**ผลทางสถิติ:** {S._verdict(main[main.period == S.DEV], main[main.period == S.TEST])} '
                             f'| ช่วงเชื่อมั่น 95%: {sm["ช่วงเชื่อมั่น 95%"]} R ต่อเทรด')
                fig = go.Figure()
                for label, t in sets.items():
                    if len(t):
                        tt = t.sort_values('time')
                        fig.add_trace(go.Scatter(x=tt.exit_time, y=tt.R_net.cumsum(), mode='lines', name=label))
                fig.update_layout(height=320, margin=dict(l=0, r=0, t=30, b=0), legend=dict(orientation='h', y=-0.25),
                                  title=dict(text='กำไรสะสม (R หักต้นทุน)', x=0))
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(table, use_container_width=True, hide_index=True)
                st.caption('เชื่อได้เมื่อ R สุทธิบวกทั้งช่วงพัฒนาและทดสอบ และช่อง "ผล" เป็น ✓ | '
                           'BTC เคยทดสอบหลายสูตรมาแล้วยังไม่มีแบบไหนได้เปรียบ จึงต้องดูผลอย่างเข้มงวด')
                if float(swl) == 0 and float(sws) == 0:
                    st.info('ยังไม่ได้ใส่ค่าข้ามคืน BTC ผลจริงอาจแย่กว่านี้ ดูค่าได้ใน MT5 → BTCUSDm → ข้อมูลจำเพาะ')
                with st.expander('รายการเทรดทั้งหมด'):
                    for k, (label, t) in enumerate(sets.items()):
                        st.write(f'**{label}**')
                        st.dataframe(t, use_container_width=True)
                        if len(t):
                            st.download_button(f'ดาวน์โหลด CSV: {label}', t.to_csv(index=False), f'btc_trades_{k + 1}.csv',
                                               key=f'btc_dl_{k}')
            except Exception as e:
                st.error(f'ทดสอบไม่สำเร็จ: {e}')
