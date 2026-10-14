"""แอป SMA200 สามกรอบ (D1 / H4 / H1): แท็บสแกนสด + แท็บ Backtest"""
import os
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

import m1_swing_scanner as core
import simple200 as S

st.set_page_config(page_title='SMA200 สามกรอบ', page_icon='📏', layout='centered')
st.title('📏 SMA200 สามกรอบ')
st.caption('ทิศ D1 + ยืนยัน H4 + จุดเข้า H1 ใช้แค่เส้น SMA200 | SL 1.5×ATR(H4)')


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

tab_live, tab_bt = st.tabs(['📡 สแกนสด', '🧪 Backtest'])

# ---------------- สแกนสด ----------------
with tab_live:
    name = st.selectbox('สินทรัพย์', list(core.ASSETS.keys()))
    if st.button('สแกนตอนนี้', use_container_width=True, key='scan'):
        try:
            with st.spinner('กำลังดึงข้อมูล D1 / H4 / H1 ...'):
                res = S.live(core.ASSETS[name])
            for tf in ('D1', 'H4', 'H1'):
                x = res['state'][tf]
                side = '🟢 เหนือเส้น 200' if x['side'] > 0 else '🔴 ใต้เส้น 200'
                st.write(f'**{tf}:** {side} | ราคา {core.fmt(x["close"])} | SMA200 {core.fmt(x["sma"])} '
                         f'| ห่าง {x["dist_atr"]:+.1f} ATR')
            trend = res['trend']
            if trend == 0:
                st.warning('D1 กับ H4 อยู่คนละฝั่งเส้น 200 → ยังไม่เล่น')
            else:
                st.write(f'**ทิศที่เล่นได้:** {"🟢 ซื้อ (LONG)" if trend > 0 else "🔴 ขาย (SHORT)"}')
                for e, label in S.ENTRY_NAMES.items():
                    if res['signal'][e]:
                        p = res['plan']
                        st.success(f'⚡ สัญญาณ ({label}) | Entry {core.fmt(p["entry"])} | SL {core.fmt(p["sl"])} | '
                                   f'TP 2R {core.fmt(p["tp"])}')
                    else:
                        st.write(f'{label}: ยังไม่มีสัญญาณบนแท่ง H1 ล่าสุด')
            if res['gold_closed']:
                st.info('ตลาดทองปิดอยู่ ข้อมูลอาจไม่เคลื่อนไหว')
            st.caption(f'แท่ง H1 ล่าสุดที่ปิดแล้ว: {res["time"]:%d %b %H:%M} UTC')
            h1 = res['h1'].tail(150)
            fig = go.Figure(go.Candlestick(x=h1.index, open=h1.open, high=h1.high, low=h1.low, close=h1.close, name='H1'))
            fig.add_trace(go.Scatter(x=h1.index, y=h1.SMA, mode='lines', name='SMA200 H1',
                                     line=dict(color='red', width=2)))
            if trend and any(res['signal'].values()):
                for lbl, y, color in (('SL', res['plan']['sl'], 'red'), ('TP 2R', res['plan']['tp'], 'lime')):
                    fig.add_hline(y=y, line_color=color, line_dash='dot', annotation_text=lbl)
            fig.update_layout(height=420, margin=dict(l=0, r=0, t=10, b=0), xaxis_rangeslider_visible=False,
                              legend=dict(orientation='h'))
            st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.error(f'ดึงข้อมูลไม่สำเร็จ: {e}')


# ---------------- Backtest ----------------
@st.cache_data(ttl=3600, show_spinner=False)
def cached_history(symbol, key, pages, ver='simple1'):
    core.API_KEY = key
    return S.load_history(symbol, int(pages))


with tab_bt:
    st.caption('ใช้ข้อมูลจริงจาก Twelve Data | credits ราว 2×(หน้า H1 + 3) ครั้ง | ครั้งแรกรอ 2-4 นาที')
    pages = st.slider('ข้อมูล H1 ย้อนหลัง (หน้าละ 5,000 แท่ง ≈ 7 เดือนของ BTC / 10 เดือนของทอง)', 1, 4, 4)
    entry = st.selectbox('จุดเข้า', list(S.ENTRY_NAMES.values()))
    exit_ = st.selectbox('การออก', list(S.EXIT_NAMES.values()))
    sp_btc = st.number_input('สเปรด BTC (หน่วยราคา)', value=6.4, step=0.5)
    sp_xau = st.number_input('สเปรดทอง (หน่วยราคา)', value=0.30, step=0.05)
    st.caption('ค่าข้ามคืน (% ของราคาต่อวัน) ค่าบวก = เสียเงิน | ทองตั้งไว้ตามบัญชี Exness ของคุณ (ต.ค. 2026)')
    sw_xau_l = st.number_input('ค่าข้ามคืนทอง ฝั่งซื้อ', value=0.0124, step=0.001, format='%.4f')
    sw_xau_s = st.number_input('ค่าข้ามคืนทอง ฝั่งขาย', value=0.0, step=0.001, format='%.4f')
    sw_btc_l = st.number_input('ค่าข้ามคืน BTC ฝั่งซื้อ', value=0.0, step=0.001, format='%.4f')
    sw_btc_s = st.number_input('ค่าข้ามคืน BTC ฝั่งขาย', value=0.0, step=0.001, format='%.4f')
    if st.button('รัน Backtest', use_container_width=True, key='bt'):
        try:
            S.SPREAD['BTC/USD'], S.SPREAD['XAU/USD'] = float(sp_btc), float(sp_xau)
            S.SWAP_PCT_DAY['XAU/USD'] = {'LONG': float(sw_xau_l), 'SHORT': float(sw_xau_s)}
            S.SWAP_PCT_DAY['BTC/USD'] = {'LONG': float(sw_btc_l), 'SHORT': float(sw_btc_s)}
            e_key = {v: k for k, v in S.ENTRY_NAMES.items()}[entry]
            x_key = {v: k for k, v in S.EXIT_NAMES.items()}[exit_]
            data = {}
            bar = st.progress(0.0, text='เริ่ม...')
            for k, (nm, sym) in enumerate(core.ASSETS.items()):
                bar.progress(k / len(core.ASSETS), text=f'ดึงข้อมูล {sym} ...')
                data[sym] = cached_history(sym, KEY, pages)
            bar.progress(1.0, text='คำนวณ...')
            for sym, (d1, h4, h1) in data.items():
                st.caption(f'{sym}: H1 {len(h1):,} แท่ง ({h1.index[0]:%d %b %Y} → {h1.index[-1]:%d %b %Y})')
            parts = [S.run(sym, *frames, entry=e_key, exit_mode=x_key) for sym, frames in data.items()]
            t = pd.concat([p for p in parts if len(p)], ignore_index=True) if any(len(p) for p in parts) else pd.DataFrame()
            if len(t) == 0:
                st.warning('ไม่มีเทรดในช่วงข้อมูลนี้')
            else:
                rep = S.report(t)
                st.write(f'**ผลแบบที่เลือก ({entry} / {exit_}):** {rep.attrs["verdict"]}')
                st.dataframe(rep, use_container_width=True, hide_index=True)
                st.write('**กราฟกำไรสะสม (หน่วย R หักต้นทุน)**')
                st.line_chart(t.sort_values('time').R_net.cumsum().reset_index(drop=True))
                st.write('**เทียบทั้ง 4 แบบ (ข้อมูลชุดเดียวกัน)**')
                st.caption('เชื่อได้เฉพาะ ✓/✗ ที่ชัดทั้งช่วงพัฒนา (70% แรก) และช่วงทดสอบ (30% หลัง) | '
                           'ระบบตามเทรนด์ชนะไม่บ่อย กำไรมาจากไม้ที่วิ่งยาว ดูที่ R สุทธิ ไม่ใช่ชนะ %')
                st.dataframe(S.compare(data), use_container_width=True, hide_index=True)
                st.dataframe(t, use_container_width=True)
                st.download_button('ดาวน์โหลดรายการเทรด CSV', t.to_csv(index=False), 'simple200_trades.csv')
        except Exception as e:
            st.error(f'Backtest ไม่สำเร็จ: {e}')
