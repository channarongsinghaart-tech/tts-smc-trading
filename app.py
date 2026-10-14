"""แอป MA200 + Market Profile (Streamlit): แท็บสแกนสด + แท็บ Backtest"""
import os
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

import m1_swing_scanner as core
import ma200_strategy as strat
import backtest_ma200 as bt

st.set_page_config(page_title='MA200 + Market Profile', page_icon='📈', layout='centered')
st.title('📈 MA200 + Market Profile')
st.caption('คำนวณจาก D1 + H4 + H1 (SMA200 และ Market Profile) | M15 ใช้เป็นจุดเข้าเท่านั้น')


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


# ---------------- สแกนสด ----------------
def scan_live(symbol):
    d1 = strat.prep(core.fetch(symbol, '1day', 300))
    h4 = strat.prep(core.fetch(symbol, '4h', 300))
    h1 = strat.prep(core.fetch(symbol, '1h', 400))
    m15 = strat.prep(core.fetch(symbol, '15min', 120))
    res = strat.decide(d1, h4, h1, m15, fast=False)
    if not res['levels']:
        res['levels'] = strat.profile_levels(h1)
    res['sma_h1'] = float(h1.SMA.iloc[-1])
    res['atr_h1'] = float(h1.ATR.iloc[-1])
    return res, h1, m15


def live_chart(m15, res):
    d = m15.tail(120)
    fig = go.Figure(go.Candlestick(x=d.index, open=d.open, high=d.high, low=d.low, close=d.close, name='M15'))
    fig.add_hline(y=res['sma_h1'], line_color='red', line_width=2, annotation_text='SMA200 H1')
    lo, hi = float(d.low.min()), float(d.high.max())
    colors = {'POC': 'gold', 'VAH': 'deepskyblue', 'VAL': 'violet'}
    for lv in res['levels']:
        if lo <= lv['price'] <= hi:
            fig.add_hline(y=lv['price'], line_color=colors[lv['kind']], line_dash='dot', line_width=1)
    sig = res['setup']
    if sig:
        for name, y, color in (('Entry', sig['entry'], 'white'), ('SL', sig['sl'], 'red'),
                               ('TP1', sig['tp1'], 'lime'), ('TP2', sig['tp2'], 'green')):
            fig.add_hline(y=y, line_color=color, line_width=2, annotation_text=name)
    fig.update_layout(height=420, margin=dict(l=0, r=0, t=10, b=0), xaxis_rangeslider_visible=False,
                      legend=dict(orientation='h'))
    return fig


tab_live, tab_bt = st.tabs(['📡 สแกนสด', '🧪 Backtest'])

with tab_live:
    name = st.selectbox('สินทรัพย์', list(core.ASSETS.keys()))
    if st.button('สแกนตอนนี้', use_container_width=True, key='scan'):
        try:
            with st.spinner('กำลังดึงข้อมูล D1 / H4 / H1 / M15 ...'):
                res, h1, m15 = scan_live(core.ASSETS[name])
            info = res['info']
            st.write(f'**D1:** {info["d1"]} ({info["d1_note"]})')
            st.write(f'**H4:** {info["h4"]} ({info["h4_note"]})')
            x = m15.iloc[-1]
            st.write(f'**H1:** SMA200 {core.fmt(res["sma_h1"])} | ATR {core.fmt(res["atr_h1"])}')
            st.write(f'**M15 แท่งล่าสุด (จุดเข้า):** {m15.index[-1]:%d %b %H:%M} UTC | ราคา {core.fmt(float(x.close))}')
            if res['direction']:
                st.write(f'**ทิศที่เล่นได้:** {"🟢 LONG" if res["direction"] == "LONG" else "🔴 SHORT"}')
            else:
                st.write('**ทิศที่เล่นได้:** ยังไม่มี (D1 กับ H4 ไม่ตรงกัน)')
            sig = res['setup']
            if sig:
                st.success(f'⚡ สัญญาณ {res["direction"]} ที่ช่อง {sig["names"]}\n\n'
                           f'Entry {core.fmt(sig["entry"])} | SL {core.fmt(sig["sl"])} | '
                           f'TP1 {core.fmt(sig["tp1"])} | TP2 {core.fmt(sig["tp2"])}')
            else:
                st.warning(f'ยังไม่มีสัญญาณ: {res["why"]}')
            st.plotly_chart(live_chart(m15, res), use_container_width=True)
            if res['levels']:
                lv = pd.DataFrame(res['levels']).sort_values('price', ascending=False)
                st.write('**ระดับ Market Profile จาก H1 (ล่าสุด)**')
                st.dataframe(lv[['name', 'price']], use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f'ดึงข้อมูลไม่สำเร็จ: {e}')


# ---------------- Backtest ----------------
@st.cache_data(ttl=3600, show_spinner=False)
def cached_history(symbol, key, pages, ver=3):
    core.API_KEY = key
    bt.M15_PAGES = int(pages)
    return bt.load_history(symbol)


with tab_bt:
    st.caption('ใช้ข้อมูลจริงจาก Twelve Data | credits ≈ 2×(หน้า M15 + 3) ครั้ง')
    pages = st.slider('ข้อมูล M15 ย้อนหลัง (หน้าละ 5,000 แท่ง ≈ 52 วันของ BTC)', 1, 4, 2)
    full = st.checkbox('ทดสอบต่อเนื่องทั้งช่วงข้อมูล (ได้เทรดมากที่สุด)', value=True)
    n_win = st.slider('จำนวนช่วงสุ่มต่อสินทรัพย์ (ใช้เมื่อไม่ติ๊กข้างบน)', 10, 150, 60, step=10)
    hours = st.slider('ความยาวแต่ละช่วงสุ่ม (ชั่วโมง)', 6, 72, 24, step=6)
    seed = st.number_input('seed', value=42, step=1)
    sp_btc = st.number_input('สเปรด BTC (หน่วยราคา)', value=6.4, step=0.5)
    sp_xau = st.number_input('สเปรดทอง (หน่วยราคา)', value=0.30, step=0.05)
    if st.button('รัน Backtest', use_container_width=True, key='bt'):
        try:
            bt.WINDOWS = 0 if full else int(n_win)
            bt.WINDOW_HOURS, bt.SEED = int(hours), int(seed)
            bt.SPREAD['BTC/USD'], bt.SPREAD['XAU/USD'] = float(sp_btc), float(sp_xau)
            rng = np.random.default_rng(int(seed))
            parts, dirs, total = [], [], 0
            bar = st.progress(0.0, text='เริ่ม...')
            for k, (nm, sym) in enumerate(core.ASSETS.items()):
                bar.progress(k / len(core.ASSETS), text=f'ดึงข้อมูล {sym} ...')
                d1, h4, h1, m15 = cached_history(sym, KEY, pages)
                bar.progress((k + 0.5) / len(core.ASSETS), text=f'จำลองเทรด {sym} ...')
                t, nw = bt.run_asset(sym, d1, h4, h1, m15, rng)
                parts.append(t)
                dirs.append(bt.direction_study(sym, d1, h4, m15))
                total += nw
            bar.progress(1.0, text='เสร็จแล้ว')
            all_t = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
            st.markdown(bt.report(all_t, total))
            if len(all_t):
                with st.expander('🔬 วิเคราะห์เชิงลึก (ช่วงพัฒนา 70% แรก / ช่วงทดสอบ 30% หลัง)'):
                    st.write('**1) ทิศจาก D1+H4 ถูกแค่ไหน (วัดทุกแท่ง H4, ดู 24 ชม. ถัดไป)**')
                    st.caption('ทิศถูก % ต้องสูงกว่าฐานสุ่ม % ทั้งสองช่วง ถึงจะถือว่าทิศมีประโยชน์')
                    st.dataframe(bt.direction_report(pd.concat(dirs, ignore_index=True)),
                                 use_container_width=True, hide_index=True)
                    st.write('**2) หลังเข้าเทรด ราคาไปทางเรา (MFE) / สวนเรา (MAE) แค่ไหนใน 4 ชม.**')
                    st.caption('หน่วย R = ระยะ SL ของกลยุทธ์ ใช้ดูว่า SL/TP วางห่างพอดีไหม')
                    st.dataframe(bt.excursion_report(all_t), use_container_width=True, hide_index=True)
                    st.write('**3) ลองเปลี่ยน SL/TP กับจุดเข้าเดิม**')
                    st.caption('ชนะ % สูงไม่ได้แปลว่ากำไร ดูที่ R สุทธิ ต้องเป็นบวกทั้งสองช่วง '
                               'และจำไว้ว่าลองหลายแบบ ย่อมมีบางแบบดูดีโดยบังเอิญ')
                    st.dataframe(bt.grid_report(all_t), use_container_width=True, hide_index=True)
                    st.write('**4) ตัวชี้วัดสภาพตลาด ณ จุดเข้า (แบ่ง 3 กลุ่มตามช่วงพัฒนา)**')
                    st.caption('เชื่อได้เฉพาะแถว ✓/✗ ที่ชัดทั้งสองช่วง แถว "ไม่ชัด" หรือ "ข้อมูลน้อย" ห้ามเอาไปตั้งกฎ')
                    st.dataframe(bt.feature_report(all_t), use_container_width=True, hide_index=True)
                st.write('**กราฟกำไรสะสม (หน่วย R หักต้นทุน)**')
                st.line_chart(all_t.sort_values('time').R_net.cumsum().reset_index(drop=True))
                st.dataframe(all_t, use_container_width=True)
                st.download_button('ดาวน์โหลดรายการเทรด CSV', all_t.to_csv(index=False), 'backtest_trades.csv')
        except Exception as e:
            st.error(f'Backtest ไม่สำเร็จ: {e}')

    st.divider()
    st.write('**🧩 ทดสอบแยกส่วน**')
    st.caption('เทียบ A ทิศอย่างเดียว / B +SMA200 H1 / C +Market Profile / D ระบบเต็ม ที่ระยะถือ 4 ชม. / 1 วัน / 3 วัน '
               'ใช้ข้อมูลชุดเดียวกับ Backtest (ไม่เสีย credits เพิ่มถ้ารัน Backtest แล้ว) ใช้เวลาราว 2-5 นาที')
    if st.button('รันทดสอบแยกส่วน', use_container_width=True, key='abl'):
        try:
            bt.SPREAD['BTC/USD'], bt.SPREAD['XAU/USD'] = float(sp_btc), float(sp_xau)
            parts = []
            bar = st.progress(0.0, text='เริ่ม...')
            for k, (nm, sym) in enumerate(core.ASSETS.items()):
                bar.progress(k / len(core.ASSETS), text=f'ทดสอบแยกส่วน {sym} ...')
                d1, h4, h1, m15 = cached_history(sym, KEY, pages)
                parts.append(bt.ablation_study(sym, d1, h4, h1, m15))
            bar.progress(1.0, text='เสร็จแล้ว')
            abl = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
            for sym in core.ASSETS.values():
                st.write(f'**{sym}**')
                st.dataframe(bt.ablation_report(abl[abl.symbol == sym]) if len(abl) else pd.DataFrame(),
                             use_container_width=True, hide_index=True)
            st.write('**รวมทองและ BTC**')
            st.dataframe(bt.ablation_report(abl), use_container_width=True, hide_index=True)
            st.caption('อ่านผล: "เฉลี่ย (ATR H1)" คือราคาไปตามทิศกี่ ATR หลังหักสเปรด ต้องเป็นบวกทั้งช่วงพัฒนาและทดสอบ | '
                       'z มากกว่า 2 = ไม่น่าใช่ความบังเอิญ | A ต้องดีกว่า "เทียบ: ซื้อทุกแท่ง H4" ถึงจะบอกได้ว่าทิศมีประโยชน์ | '
                       'B, C, D ต้องดีกว่า A ถึงจะบอกได้ว่าจุดเข้าเพิ่มคุณค่า')
            if len(abl):
                st.download_button('ดาวน์โหลดผลทดสอบแยกส่วน CSV', abl.to_csv(index=False), 'ablation.csv')
        except Exception as e:
            st.error(f'ทดสอบแยกส่วนไม่สำเร็จ: {e}')
