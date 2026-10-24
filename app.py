"""แอปทอง SMA200 หลายกรอบ: D1 + H4 ทิศเดียวกัน แล้วเข้าเมื่อกรอบจุดเข้า (M30 หรือ H1) ปิดข้ามเส้น 200 (ทองเป็นหลัก)"""
import os
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

import m1_swing_scanner as core
import simple200 as S
import five200 as F
import swinglab as L

st.set_page_config(page_title='ทอง & BTC', page_icon='🥇', layout='centered')
PAGE = st.radio('เลือกสินทรัพย์', ['🥇 ทอง', '₿ BTC'], horizontal=True, label_visibility='collapsed', key='page')


def get_key():
    key = ''
    try:
        key = str(st.secrets.get('TWELVEDATA_API_KEY', '')).strip()
    except Exception:
        pass
    key = key or os.getenv('TWELVEDATA_API_KEY', '').strip()
    if not key:
        key = st.text_input('Twelve Data API key', type='password').strip()
    return key


KEY = get_key()
if not KEY:
    st.info('ใส่ API key (หรือตั้ง TWELVEDATA_API_KEY ใน Streamlit secrets) เพื่อเริ่มใช้งาน')
    st.stop()
core.API_KEY = KEY
core.CREDIT_BUDGET = 10 ** 9

if PAGE.startswith('₿'):
    import btc_page
    btc_page.render(KEY)
    st.stop()

st.title('🥇 ทอง SMA200 หลายกรอบ')

# ---------------- ตั้งค่า ----------------
with st.expander('⚙️ ตั้งค่า (กรอบจุดเข้า ทุน ความเสี่ยง สเปรด ค่าข้ามคืน)'):
    ENTRY = st.selectbox('กรอบจุดเข้า', list(F.ENTRY_TFS), index=list(F.ENTRY_TFS).index(F.DEFAULT_ENTRY))
    REQ_H1 = st.checkbox('ให้ H1 อยู่ฝั่งเดียวกันด้วย (ใช้กับจุดเข้า M30)', value=False) if ENTRY == 'M30' else False
    tz_name = st.selectbox('เขตเวลาของคุณ', ['America/Edmonton', 'Asia/Bangkok', 'UTC'])
    account = st.number_input('ทุนในบัญชี (USD) ใช้คำนวณขนาดไม้', value=5000.0, step=500.0)
    risk_pct = st.number_input('เสี่ยงต่อไม้ (% ของทุน)', value=0.5, step=0.1, format='%.2f')
    sp_xau = st.number_input('สเปรดทอง (หน่วยราคา)', value=0.30, step=0.05)
    sp_btc = st.number_input('สเปรด BTC (หน่วยราคา)', value=6.4, step=0.5)
    sw_xau_l = st.number_input('ค่าข้ามคืนทอง ฝั่งซื้อ (% ต่อคืน)', value=0.0124, step=0.001, format='%.4f')
    sw_xau_s = st.number_input('ค่าข้ามคืนทอง ฝั่งขาย (% ต่อคืน)', value=0.0, step=0.001, format='%.4f')
    sw_btc_l = st.number_input('ค่าข้ามคืน BTC ฝั่งซื้อ (% ต่อคืน)', value=0.0, step=0.001, format='%.4f')
    sw_btc_s = st.number_input('ค่าข้ามคืน BTC ฝั่งขาย (% ต่อคืน)', value=0.0, step=0.001, format='%.4f')
    pages = st.slider('ข้อมูลทดสอบย้อนหลัง (หน้า M30 ละ 5,000 แท่ง ≈ 5 เดือนของทอง)', 1, 4, 4)

st.caption(f'D1 + H4 อยู่ฝั่งเดียวกันของเส้น 200{" + H1 ฝั่งเดียวกัน" if REQ_H1 else ""} แล้วเข้าเมื่อแท่ง {ENTRY} '
           f'ปิดข้ามเส้น 200 | SL 1.5×ATR(H4) | TP 2R | ถือไม่เกิน 48 ชม. | เปลี่ยนกรอบจุดเข้าได้ใน ⚙️ ตั้งค่า')

asset_name = 'Gold XAU/USD'
SYM = 'XAU/USD'
TZ = ZoneInfo(tz_name)
S.SPREAD['XAU/USD'], S.SPREAD['BTC/USD'] = float(sp_xau), float(sp_btc)
S.SWAP_PCT_DAY['XAU/USD'] = {'LONG': float(sw_xau_l), 'SHORT': float(sw_xau_s)}
S.SWAP_PCT_DAY['BTC/USD'] = {'LONG': float(sw_btc_l), 'SHORT': float(sw_btc_s)}


def tstr(ts):
    return f'{ts.tz_convert(TZ):%a %d %b %H:%M} ({ts:%H:%M} UTC)'


def chart(df, n, title, levels=None):
    d = df.tail(n)
    fig = go.Figure(go.Candlestick(x=d.index, open=d.open, high=d.high, low=d.low, close=d.close, name=title,
                                   increasing_line_color='#26a69a', decreasing_line_color='#ef5350'))
    fig.add_trace(go.Scatter(x=d.index, y=d.SMA, mode='lines', name='SMA200', line=dict(color='orange', width=2)))
    for name, y, color in (levels or []):
        fig.add_hline(y=y, line_color=color, line_dash='dot', annotation_text=name, annotation_position='right')
    fig.update_layout(height=380, margin=dict(l=0, r=0, t=30, b=0), title=dict(text=title, x=0, font=dict(size=14)),
                      xaxis_rangeslider_visible=False, showlegend=False)
    return fig


@st.cache_data(ttl=120, show_spinner=False)
def live_cached(symbol, key, entry, req_h1, ver='m30v1'):
    core.API_KEY = key
    return F.live(symbol, entry, req_h1)


@st.cache_data(ttl=3600, show_spinner=False)
def m30_history(symbol, key, pages, ver='m30v1'):
    core.API_KEY = key
    return F.load_history(symbol, 'M30', int(pages))


@st.cache_data(ttl=3600, show_spinner=False)
def h1_history(symbol, key, pages, ver='h1v1'):
    core.API_KEY = key
    return S.load_history(symbol, int(pages))


tab_now, tab_bt, tab_tools = st.tabs(['📡 ตอนนี้', '🧪 ทดสอบย้อนหลัง', '🔬 เครื่องมือวิจัย'])

# ================= ตอนนี้ =================
with tab_now:
    if st.button('🔄 อัปเดตข้อมูลล่าสุด', use_container_width=True, key='refresh'):
        live_cached.clear()
    try:
        with st.spinner('กำลังดึงข้อมูล D1 / H4 / H1 / M30 ...'):
            res = live_cached(SYM, KEY, ENTRY, REQ_H1)
        stt, trend, sig = res['state'], res['trend'], res['signal']
        st.caption(f'แท่ง {ENTRY} ล่าสุดที่ปิดแล้ว: {tstr(res["entry_close"])} | ราคาล่าสุด {core.fmt(res["price"])} '
                   f'| ข้อมูลเก็บไว้ 2 นาที กดอัปเดตเพื่อดึงใหม่')

        # ---- กล่องสถานะหลัก ----
        side_txt = {1: 'ซื้อ', -1: 'ขาย'}
        if res['gold_closed']:
            st.info('🌙 ตลาดทองปิดอยู่ (เปิดอาทิตย์ 22:00 UTC) ราคาไม่เคลื่อนไหว')
        if sig is not None and sig['moved_R'] <= 0.5:
            box = st.success if sig['dir'] > 0 else st.error
            box(f'### {"🟢 สัญญาณซื้อ (BUY)" if sig["dir"] > 0 else "🔴 สัญญาณขาย (SELL)"}\n'
                f'แท่ง {ENTRY} ปิดเมื่อ {tstr(sig["time"])} | ราคาไปแล้ว {sig["moved_R"]:+.2f}R จากจุดเข้า')
        elif sig is not None:
            st.warning(f'### ⚠️ สัญญาณ{side_txt[sig["dir"]]}เกิดแล้ว แต่ราคาวิ่งไป {sig["moved_R"]:+.2f}R\n'
                       f'เลยจุดเข้ามากเกินครึ่งทางไป TP แนะนำให้ข้ามไม้นี้')
        elif trend:
            e_side = stt[ENTRY]['side']
            if e_side == trend:
                st.info(f'### ⏳ ทิศ{side_txt[trend]}: รอรอบถัดไป\n{ENTRY} อยู่ฝั่ง{side_txt[trend]}อยู่แล้ว '
                        f'รอให้ย่อไปอีกฝั่งของเส้น 200 แล้วปิดข้ามกลับมา')
            else:
                st.info(f'### ⏳ ทิศ{side_txt[trend]}: รอ {ENTRY} ปิดข้ามเส้น 200 กลับมา\n'
                        f'เมื่อแท่ง {ENTRY} ปิด{"เหนือ" if trend > 0 else "ใต้"}เส้น 200 จะเป็นสัญญาณ{side_txt[trend]}')
        else:
            why = 'D1 กับ H4 อยู่คนละฝั่งเส้น 200' if res['d1h4'] == 0 else 'H1 ยังอยู่คนละฝั่งกับ D1/H4'
            st.warning(f'### ⛔ ยังไม่เล่น\n{why}')

        # ---- ตารางสถานะทุกกรอบ ----
        rows = ['| กรอบ | สถานะ | ราคา | SMA200 | ห่าง (ATR) |', '|---|---|---|---|---|']
        for tf in ('D1', 'H4', 'H1', 'M30'):
            x = stt[tf]
            mark = '🟢 เหนือ' if x['side'] > 0 else ('🔴 ใต้' if x['side'] < 0 else '⚪ ไม่มีข้อมูล')
            sma = core.fmt(x['sma']) if x['sma'] == x['sma'] else '-'
            dist = f'{x["dist_atr"]:+.1f}' if x['dist_atr'] == x['dist_atr'] else '-'
            rows.append(f'| {tf} | {mark} | {core.fmt(x["close"])} | {sma} | {dist} |')
        st.markdown('\n'.join(rows))

        # ---- แผนเทรด ----
        levels = None
        if trend:
            s = sig['dir'] if sig is not None else trend
            entry = sig['entry'] if sig is not None else res['price']
            risk = sig['risk'] if sig is not None else res['risk']
            sl, tp = entry - s * risk, entry + s * F.TP_R * risk
            lot, money, real = F.lot_size(risk, account, risk_pct) if SYM == 'XAU/USD' else (None, None, None)
            title = 'แผนเทรดของสัญญาณนี้' if sig is not None else f'ถ้าเกิดสัญญาณ{side_txt[s]}ที่ราคาตอนนี้'
            plan = [f'**{title}**', f'- ราคาเข้า: **{core.fmt(entry)}**', f'- SL: **{core.fmt(sl)}** (ห่าง {core.fmt(risk)})',
                    f'- TP 2R: **{core.fmt(tp)}**']
            if sig is not None:
                plan.append(f'- ถ้ายังไม่ถึง TP/SL ให้ปิดภายใน: {tstr(sig["close_by"])} หรือก่อนตลาดปิดวันศุกร์')
            if lot is not None:
                if lot >= 0.01:
                    plan.append(f'- ขนาดไม้แนะนำ: **{lot:.2f} lot** เสี่ยงจริงราว {real:,.2f} USD '
                                f'({real / account * 100:.2f}% ของทุน, งบ {risk_pct}% = {money:,.2f} USD)')
                else:
                    plan.append(f'- ⚠️ ทุน {account:,.0f} USD เสี่ยง {risk_pct}% = {money:,.2f} USD แต่ไม้ขั้นต่ำ 0.01 lot '
                                f'เสี่ยงราว {real:,.2f} USD ({real / account * 100:.1f}% ของทุน เกินแผน)')
            st.markdown('\n'.join(plan))
            levels = [('Entry', entry, 'white'), ('SL', sl, '#ef5350'), ('TP', tp, '#26a69a')]

        # ---- กราฟ ----
        other = 'H1' if ENTRY == 'M30' else 'M30'
        c1, c2, c3, c4 = st.tabs([f'{ENTRY} (จุดเข้า)', other, 'H4', 'D1'])
        fr = res['frames']
        with c1:
            st.plotly_chart(chart(fr[ENTRY], 160, f'{asset_name} {ENTRY}', levels), use_container_width=True)
        with c2:
            st.plotly_chart(chart(fr[other], 160, f'{asset_name} {other}', levels), use_container_width=True)
        with c3:
            st.plotly_chart(chart(fr['H4'], 150, f'{asset_name} H4'), use_container_width=True)
        with c4:
            st.plotly_chart(chart(fr['D1'], 200, f'{asset_name} D1'), use_container_width=True)
        st.caption('เส้นสีส้ม = SMA200 ของแต่ละกรอบ | ผลย้อนหลังไม่รับประกันอนาคต ใช้บัญชีเดโมก่อนเสมอ')
    except Exception as e:
        st.error(f'ดึงข้อมูลไม่สำเร็จ: {e}')

# ================= ทดสอบย้อนหลัง =================
with tab_bt:
    st.caption('เทียบจุดเข้า H1 (ระบบเดิม) กับ M30 ในช่วงเวลาเดียวกัน (ช่วงที่มีข้อมูล M30) หักสเปรดและค่าข้ามคืนแล้ว '
               '| ครั้งแรกรอ 2-4 นาที')
    if st.button('▶️ ทดสอบระบบ', use_container_width=True, key='bt'):
        try:
            with st.spinner('กำลังดึงข้อมูลและจำลองเทรด ...'):
                frames = m30_history(SYM, KEY, pages)
                table, sets = F.compare(SYM, frames)
            m30 = frames[3]
            st.caption(f'ช่วงข้อมูล M30: {m30.index[0]:%d %b %Y} → {m30.index[-1]:%d %b %Y} '
                       f'(70% แรก = ช่วงพัฒนา, 30% หลัง = ช่วงทดสอบ)')
            chosen = next((k for k in sets if k.startswith(F.name_of(ENTRY, REQ_H1))), None)
            main = sets.get(chosen, pd.DataFrame())
            if len(main):
                sm = S.stats(main)
                st.write(f'**แบบที่ตั้งไว้ตอนนี้: {chosen}**')
                a1, b1, c1 = st.columns(3)
                a1.metric('เทรด', sm['เทรด'])
                b1.metric('ชนะ', f'{sm["ชนะ %"]}%')
                c1.metric('R ต่อเทรด (สุทธิ)', f'{sm["R/เทรด สุทธิ"]:+.2f}')
                a1, b1, c1 = st.columns(3)
                a1.metric('R รวม', f'{sm["R รวม"]:+.1f}')
                b1.metric('PF', sm['PF'])
                c1.metric('ขาดทุนสะสมสูงสุด', f'{sm["ขาดทุนสะสมสูงสุด R"]}R')
                verdict = S._verdict(main[main.period == S.DEV], main[main.period == S.TEST])
                st.write(f'**ผลทางสถิติ:** {verdict} | ช่วงเชื่อมั่น 95%: {sm["ช่วงเชื่อมั่น 95%"]} R ต่อเทรด')
            fig = go.Figure()
            for label, t in sets.items():
                if len(t):
                    tt = t.sort_values('time')
                    fig.add_trace(go.Scatter(x=tt.exit_time, y=tt.R_net.cumsum(), mode='lines', name=label))
            fig.update_layout(height=340, margin=dict(l=0, r=0, t=30, b=0), title=dict(text='กำไรสะสม (R หักต้นทุน)', x=0),
                              legend=dict(orientation='h', y=-0.25))
            st.plotly_chart(fig, use_container_width=True)
            st.write('**เทียบจุดเข้า H1 กับ M30**')
            st.dataframe(table, use_container_width=True, hide_index=True)
            st.caption('เชื่อได้เมื่อ R สุทธิบวกทั้งช่วงพัฒนาและทดสอบ และช่อง "ผล" เป็น ✓ | '
                       'ถ้า M30 แย่กว่า H1 ให้เปลี่ยนกรอบจุดเข้าเป็น H1 ใน ⚙️ ตั้งค่า และใน workflow ของ LINE')
            with st.expander('รายการเทรดทั้งหมด'):
                for k, (label, t) in enumerate(sets.items()):
                    st.write(f'**{label}**')
                    st.dataframe(t, use_container_width=True)
                    if len(t):
                        st.download_button(f'ดาวน์โหลด CSV: {label}', t.to_csv(index=False), f'trades_{k + 1}.csv',
                                           key=f'dl_{k}')
        except Exception as e:
            st.error(f'ทดสอบไม่สำเร็จ: {e}')

# ================= เครื่องมือวิจัย =================
with tab_tools:
    st.caption('เครื่องมือเดิมจากการวิจัย (ใช้ข้อมูล H1 ย้อนหลัง ~2.8 ปี) เปิดเฉพาะตอนต้องการ')
    with st.expander('⏱️ เทียบแบบถือสั้น (ระบบ H1 ทอง)'):
        if st.button('รัน', use_container_width=True, key='short'):
            try:
                with st.spinner('กำลังคำนวณ ...'):
                    t = S.compare_short('XAU/USD', h1_history('XAU/USD', KEY, 4))
                st.dataframe(t, use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f'ไม่สำเร็จ: {e}')
    with st.expander('🧰 ทดสอบตัวกรอง (ระบบ H1)'):
        if st.button('รัน', use_container_width=True, key='flt'):
            try:
                with st.spinner('กำลังคำนวณ ...'):
                    fr_h1 = h1_history(SYM, KEY, 4)
                    st.dataframe(S.compare_filters(SYM, fr_h1), use_container_width=True, hide_index=True)
                    st.dataframe(S.diagnose(S.run(SYM, *fr_h1, entry='cross', exit_mode='tp', tp_r=2.0, max_hold=48)),
                                 use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f'ไม่สำเร็จ: {e}')
    with st.expander('🔬 ห้องทดลองสวิง 4 สูตร'):
        if st.button('รัน', use_container_width=True, key='lab'):
            try:
                with st.spinner('กำลังทดลอง ...'):
                    table, trades = L.lab({SYM: h1_history(SYM, KEY, 4)})
                st.dataframe(table, use_container_width=True, hide_index=True)
            except Exception as e:
                st.error(f'ไม่สำเร็จ: {e}')
