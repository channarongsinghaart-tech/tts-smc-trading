Gold & BTC Swing Scanner — ตาม Trend D1
โปรเจกต์แยกจากตัว Short/Long Hold เดิม ใช้ Trend Following เท่านั้น (ไม่มี counter-trend)
Logic
D1 กำหนดทิศ: EMA20>EMA50>EMA200, ราคาอยู่เหนือ/ใต้ EMA50, EMA50 ลาดไปทางเดียวกัน, ความแรง ≥ 70, และราคาไม่ยืดเกิน 3 ATR(D1) จาก EMA20 (กันไล่ราคา)
H4 รอย่อ: เทรนด์ H4 ยังไม่เสีย แล้วราคาแตะโซน EMA20 ภายใน 4 แท่งล่าสุด
H1 trigger: แท่งปิดล่าสุดต้อง break / reclaim EMA20 / rejection ตามทิศ D1
Plan: SL เหนือ/ใต้ swing 8 แท่ง H4 + 0.3 ATR, TP1 = 1.5R, TP2 = 3R (risk ต้องอยู่ 0.5–3.5 ATR H4)
ข้อความที่ส่ง LINE มี 2 แบบ: 👀 WATCH (อยู่ในโซนย่อ รอ trigger วันละครั้งต่อทิศ) และ 🚨 ENTRY READY (cooldown 12 ชม./ทิศ)
Setup
วางไฟล์ทั้งหมดใน repo ใหม่ (หรือ repo เดิมก็ได้ ไฟล์ไม่ชนกัน) โดย workflow อยู่ที่ .github/workflows/gold-btc-swing-scanner.yml
ใช้ GitHub Secrets ชุดเดิม: TWELVEDATA_API_KEY, LINE_CHANNEL_ACCESS_TOKEN, LINE_USER_ID
รันทุกชั่วโมง (นาทีที่ 5) ใช้ ~6 credits ต่อรอบ ต่ำกว่าลิมิต free plan มาก
สถานะแจ้งเตือนเก็บด้วย actions/cache เพื่อไม่ให้ส่งซ้ำ
ทดสอบในเครื่อง: DRY_RUN=1 TWELVEDATA_API_KEY=... python swing_scanner.py (print แทนส่ง LINE)
ปรับค่า
ดูค่าคงที่ด้านบนของ swing_scanner.py (MIN_D1_STRENGTH, H4_ZONE_ATR, TP1_R, COOLDOWN_HOURS ฯลฯ)
หมายเหตุ: ยังไม่ได้ backtest ค่าเหล่านี้ ควรดูสัญญาณจริงสักพักก่อนใช้เงินจริง
