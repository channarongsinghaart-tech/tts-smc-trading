"""แอป M1 Swing (Streamlit): แท็บสแกนสด + แท็บ Backtest"""
import os
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

import m1_swing_scanner as s
import backtest_m1 as bt

st.set_page_config(page_title='M1 Swing', page_icon='⚡', layout='centered')
st.title('⚡ M1 Swing')
st.caption('D1 แท่งก่อนหน้า = ตัวกรองทิศ | H1 = ตัวหลัก (โซน) | M1 = จุดเข้า')

s.CREDIT_BUDGET = 10 ** 9      # ในแอปคุมโดยกดเอง ไม่ใช้งบรายวันของสแกนเนอร์


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
s.API_KEY = KEY


# ---------------- สแกนสด ----------------
def scan_live(symbol):
    d1 = s.d1_prev_bias(s.fetch(symbol, '1day', 3))
    h1 = s.ind(s.fetch(symbol, '1h', 120))
    ctx, why = s.h1_context(h1, d1)
    m1 = s.ind(s.fetch(symbol, '1min', 300, kind='m1'))
    sig, sig_why = (None, 'ยังไม่อยู่ในโซน H1')
    if ctx and ctx['armed']:
        sig, sig_why = s.m1_signal(m1, ctx)
    elif not ctx:
        sig_why = why
    return d1, ctx, why, m1, sig, sig_why


def live_chart(m1, ctx, sig):
    d = m1.tail(120)
    fig = go.Figure(go.Candlestick(x=d.index, open=d.open, high=d.high, low=d.low, close=d.close))
    if ctx:
        fig.add_hrect(y0=ctx['lo'], y1=ctx['hi'], fillcolor='orange', opacity=0.18, line_width=0)
    if sig:
        for name, y, color in (('Entry', sig['entry'], 'white'), ('SL', sig['sl'], 'red'),
                               ('TP1', sig['tp1'], 'lime'), ('TP2', sig['tp2'], 'green')):
            fig.add_hline(y=y, line_color=color, line_dash='dot', annotation_text=name)
    fig.update_layout(height=380, margin=dict(l=0, r=0, t=10, b=0), xaxis_rangeslider_visible=False)
    return fig


tab_live, tab_bt = st.tabs(['📡 สแกนสด', '🧪 Backtest'])

with tab_live:
    name = st.selectbox('สินทรัพย์', list(s.ASSETS.keys()))
    if st.button('สแกนตอนนี้', use_container_width=True, key='scan'):
        try:
            with st.spinner('กำลังดึงข้อมูล D1 / H1 / M1 ...'):
                d1, ctx, why, m1, sig, sig_why = scan_live(s.ASSETS[name])
            st.write(f'**D1 แท่งก่อนหน้า:** {d1["bias"]}  (PDH {s.fmt(d1["pdh"])} / PDL {s.fmt(d1["pdl"])})')
            if ctx:
                st.write(f'**H1:** {ctx["mode"]} → ทิศ **{ctx["direction"]}** | โซน {s.fmt(ctx["lo"])} - {s.fmt(ctx["hi"])} '
                         f'| {"✅ ราคาใกล้โซน" if ctx["armed"] else "⏳ ราคายังไกลโซน"}')
            else:
                st.write(f'**H1:** ไม่เล่น — {why}')
            st.write(f'**M1 แท่งล่าสุด:** {m1.index[-1]:%H:%M} UTC | ราคา {s.fmt(float(m1.close.iloc[-1]))}')
            if sig:
                st.success(f'⚡ สัญญาณ {ctx["direction"]}  Entry {s.fmt(sig["entry"])} | SL {s.fmt(sig["sl"])} | '
                           f'TP1 {s.fmt(sig["tp1"])} | TP2 {s.fmt(sig["tp2"])}')
            else:
                st.warning(f'ยังไม่มีสัญญาณ: {sig_why}')
            st.plotly_chart(live_chart(m1, ctx, sig), use_container_width=True)
        except Exception as e:
            st.error(f'ดึงข้อมูลไม่สำเร็จ: {e}')


# ---------------- Backtest ----------------
@st.cache_data(ttl=3600, show_spinner=False)
def cached_history(symbol, key):
    s.API_KEY = key
    return bt.load_history(symbol)


with tab_bt:
    st.caption('สุ่มช่วงย้อนหลังช่วงละ 2 ชม. ใช้ข้อมูลจริงจาก Twelve Data (~8 credits, ครั้งแรกอาจรอ 1-2 นาที)')
    n_win = st.slider('จำนวนช่วงต่อสินทรัพย์', 10, 80, 30, step=5)
    seed = st.number_input('seed (เปลี่ยนเพื่อสุ่มชุดใหม่)', value=42, step=1)
    sp_btc = st.number_input('สเปรด BTC (หน่วยราคา)', value=10.0, step=1.0)
    sp_xau = st.number_input('สเปรดทอง (หน่วยราคา)', value=0.30, step=0.05)
    if st.button('รัน Backtest', use_container_width=True, key='bt'):
        try:
            bt.WINDOWS, bt.SEED = int(n_win), int(seed)
            bt.SPREAD['BTC/USD'], bt.SPREAD['XAU/USD'] = float(sp_btc), float(sp_xau)
            rng = np.random.default_rng(int(seed))
            parts, total = [], 0
            bar = st.progress(0.0, text='เริ่ม...')
            for k, (nm, sym) in enumerate(s.ASSETS.items()):
                bar.progress(k / len(s.ASSETS), text=f'ดึงข้อมูล {sym} ...')
                d1, h1, m1 = cached_history(sym, KEY)
                bar.progress((k + 0.5) / len(s.ASSETS), text=f'จำลองเทรด {sym} ...')
                t, nw = bt.run_asset(sym, d1, h1, m1, rng)
                parts.append(t)
                total += nw
            bar.progress(1.0, text='เสร็จแล้ว')
            all_t = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
            st.markdown(bt.report(all_t, total))
            if len(all_t):
                st.write('**กราฟกำไรสะสม (หน่วย R หักต้นทุน)**')
                st.line_chart(all_t.sort_values('time').R_net.cumsum().reset_index(drop=True))
                st.dataframe(all_t, use_container_width=True)
                st.download_button('ดาวน์โหลดรายการเทรด CSV', all_t.to_csv(index=False), 'backtest_trades.csv')
        except Exception as e:
            st.error(f'Backtest ไม่สำเร็จ: {e}')
