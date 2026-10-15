"""แอป SMA200 สามกรอบ (D1 / H4 / H1): แท็บสแกนสด + แท็บ Backtest"""
import os
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

import m1_swing_scanner as core
import simple200 as S
import swinglab as L

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
    tp_sel = st.selectbox('ขนาด TP (ใช้กับแบบ TP)', list(S.TP_CHOICES.keys()), index=2)
    hold_sel = st.selectbox('ถือนานสุด', list(S.HOLD_CHOICES.keys()), index=4)
    intra = st.checkbox('ไม่ถือข้ามคืน (ปิดก่อน 21:00 UTC และไม่เปิดไม้ใหม่หลัง 18:00 UTC)', value=False)
    sp_btc = st.number_input('สเปรด BTC (หน่วยราคา)', value=6.4, step=0.5)
    sp_xau = st.number_input('สเปรดทอง (หน่วยราคา)', value=0.30, step=0.05)
    st.caption('ค่าข้ามคืน (% ของราคาต่อคืน, คิดเฉพาะคืนที่ถือผ่านเวลาตัดรอบ ทองพุธ 3 เท่า) ค่าบวก = เสียเงิน | ทองตั้งไว้ตามบัญชี Exness ของคุณ (ต.ค. 2026)')
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
            parts = [S.run(sym, *frames, entry=e_key, exit_mode=x_key, tp_r=S.TP_CHOICES[tp_sel],
                           max_hold=S.HOLD_CHOICES[hold_sel], intraday=intra) for sym, frames in data.items()]
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

    st.divider()
    st.write('**⏱️ เทียบแบบถือสั้น (ทองเท่านั้น)**')
    st.caption('จุดเข้า H1 ข้ามเส้น 200 | TP 1R / 1.5R / 2R × ถือนานสุด 12 ชม. / 1 วัน / 2 วัน / 3 วัน และแบบไม่ข้ามคืน '
               '| ใช้ข้อมูลและค่าสเปรด/ค่าข้ามคืนชุดเดียวกับด้านบน')
    if st.button('รันเทียบแบบถือสั้น', use_container_width=True, key='short'):
        try:
            S.SPREAD['XAU/USD'] = float(sp_xau)
            S.SWAP_PCT_DAY['XAU/USD'] = {'LONG': float(sw_xau_l), 'SHORT': float(sw_xau_s)}
            with st.spinner('กำลังคำนวณ 15 แบบ ...'):
                frames = cached_history('XAU/USD', KEY, pages)
                cmp_short = S.compare_short('XAU/USD', frames)
            st.dataframe(cmp_short, use_container_width=True, hide_index=True)
            st.caption('ดู "R/เทรด สุทธิ" และ "ผล" เป็นหลัก ต้องบวกทั้งช่วงพัฒนาและทดสอบ | ลอง 15 แบบพร้อมกัน '
                       'ย่อมมีบางแบบดูดีโดยบังเอิญ ให้เลือกแบบที่แบบข้างเคียงก็ดีด้วย')
            st.download_button('ดาวน์โหลดผลเทียบถือสั้น CSV', cmp_short.to_csv(index=False), 'short_hold_compare.csv')
        except Exception as e:
            st.error(f'เทียบแบบถือสั้นไม่สำเร็จ: {e}')

    st.divider()
    st.write('**₿ ปรับระบบสำหรับ BTC: ทดสอบตัวกรอง 3 ข้อ**')
    st.caption('จุดเข้า H1 ข้ามเส้น 200 + TP 2R + ถือไม่เกิน 48 ชม. | ตัวกรองตั้งจากเหตุผลไว้ก่อน ไม่ได้ไล่หาตัวเลข: '
               'ไม่เข้าเสาร์-อาทิตย์ / ราคา D1 ห่างเส้น 200 ≥ 1 ATR / เส้น 200 ของ H4 ชันตามทิศ')
    flt_asset = st.selectbox('สินทรัพย์', ['Bitcoin BTC/USD', 'Gold XAU/USD'], key='flt_asset')
    if st.button('รันทดสอบตัวกรอง', use_container_width=True, key='flt'):
        try:
            S.SPREAD['BTC/USD'], S.SPREAD['XAU/USD'] = float(sp_btc), float(sp_xau)
            S.SWAP_PCT_DAY['XAU/USD'] = {'LONG': float(sw_xau_l), 'SHORT': float(sw_xau_s)}
            S.SWAP_PCT_DAY['BTC/USD'] = {'LONG': float(sw_btc_l), 'SHORT': float(sw_btc_s)}
            sym = core.ASSETS[flt_asset]
            with st.spinner('กำลังทดสอบ 5 แบบ ...'):
                frames = cached_history(sym, KEY, pages)
                st.dataframe(S.compare_filters(sym, frames), use_container_width=True, hide_index=True)
                base = S.run(sym, *frames, entry='cross', exit_mode='tp', tp_r=2.0, max_hold=48)
            st.write('**แยกเทรดตามสภาพตลาด ณ จุดเข้า (แบบไม่กรอง)**')
            st.caption('ดูว่าเทรดแบบไหนแพ้บ่อย | เชื่อได้เฉพาะ ✓/✗ ที่ชัดทั้งสองช่วง | dist_d1 = ราคาห่างเส้น 200 (D1) กี่ ATR '
                       '| slope_h4 = ความชันเส้น 200 (H4) ตามทิศเทรด | adx_h4 = ความแรงเทรนด์ H4')
            st.dataframe(S.diagnose(base), use_container_width=True, hide_index=True)
            if len(base):
                st.download_button('ดาวน์โหลดเทรดพร้อมตัวชี้วัด CSV', base.to_csv(index=False), 'filter_trades.csv')
            if sym == 'BTC/USD' and float(sw_btc_l) == 0 and float(sw_btc_s) == 0:
                st.info('ยังไม่ได้ใส่ค่าข้ามคืนของ BTC ผลจริงอาจแย่กว่านี้ ดูค่าได้ใน MT5 → BTCUSDm → ข้อมูลจำเพาะ')
        except Exception as e:
            st.error(f'ทดสอบตัวกรองไม่สำเร็จ: {e}')

    st.divider()
    st.write('**🔬 ห้องทดลองสวิง: 4 สูตรใหม่ ถือไม่เกิน 2 วัน เทรดได้ทั้งขึ้นและลง**')
    st.caption('M1 ย่อในเทรนด์ (RSI2) | M2 สวิงในกรอบ (Bollinger, ตลาดไม่มีเทรนด์) | M3 เบรกกรอบเอเชีย (ปิดในวัน) | '
               'M4 กวาด High/Low เมื่อวาน แบบ SMC (ปิดในวัน) | แต่ละสูตรทดสอบ TP 1R และ 1.5R | '
               'ใช้ข้อมูล สเปรด และค่าข้ามคืน ชุดเดียวกับด้านบน')
    lab_assets = st.selectbox('สินทรัพย์ที่จะทดลอง', ['ทองอย่างเดียว', 'BTC อย่างเดียว', 'ทั้งสองตัว'])
    if st.button('รันห้องทดลองสวิง', use_container_width=True, key='lab'):
        try:
            S.SPREAD['BTC/USD'], S.SPREAD['XAU/USD'] = float(sp_btc), float(sp_xau)
            S.SWAP_PCT_DAY['XAU/USD'] = {'LONG': float(sw_xau_l), 'SHORT': float(sw_xau_s)}
            S.SWAP_PCT_DAY['BTC/USD'] = {'LONG': float(sw_btc_l), 'SHORT': float(sw_btc_s)}
            syms = {'ทองอย่างเดียว': ['XAU/USD'], 'BTC อย่างเดียว': ['BTC/USD'],
                    'ทั้งสองตัว': ['XAU/USD', 'BTC/USD']}[lab_assets]
            with st.spinner('กำลังทดลอง 4 สูตร × 2 TP ...'):
                lab_data = {sym: cached_history(sym, KEY, pages) for sym in syms}
                table, lab_trades = L.lab(lab_data)
            st.dataframe(table, use_container_width=True, hide_index=True)
            st.caption('อ่านผล: ดู "R/เทรด สุทธิ" ต้องบวกทั้งช่วงพัฒนาและทดสอบ และช่อง "ผล" ต้องเป็น ✓ ถึงจะเชื่อได้ | '
                       'ลอง 8 แบบต่อสินทรัพย์พร้อมกัน แบบที่ดูดีที่สุดอาจดีโดยบังเอิญ | ชนะ % สูงไม่ได้แปลว่ากำไร')
            if len(lab_trades):
                st.download_button('ดาวน์โหลดรายการเทรดห้องทดลอง CSV', lab_trades.to_csv(index=False), 'swinglab_trades.csv')
        except Exception as e:
            st.error(f'ห้องทดลองไม่สำเร็จ: {e}')
