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
โหมด Scalp (เล่นสั้น เก็บเร็ว) — scalp_scanner.py
ทิศยังตาม D1 (ความแรง ≥ 60 และ H1 ต้องไม่ขัด) แต่จุดเข้าใช้ M15 setup + M5 trigger
M15 setup: pullback เข้า EMA20 / sweep / breakout (ย้อนได้ 1 แท่ง)
M5 trigger: break / reclaim EMA20 / rejection (ไม่เข้าถ้าราคาห่าง EMA20 เกิน 1.5 ATR = ไล่ราคา)
SL แคบ (swing 8 แท่ง M5), TP1 = 1R, TP2 = 1.5R, ไม่ถึง TP1 ใน 12 แท่ง M5 ให้ออก
Cooldown 30 นาที/ทิศ, สูงสุด 6 สัญญาณ/วัน/สินทรัพย์
Workflow: .github/workflows/gold-btc-scalp-scanner.yml รันทุก 5 นาที (ข้ามช่วง 22:00–06:00 Alberta)
Credit: D1/H1 แคช 60 นาที, M15 ดึงทุกรอบ, M5 ดึงเฉพาะตอนมี M15 setup ราว 2–4 credits/รอบ — ถ้ารันทั้ง swing และ scalp พร้อมกัน ให้เฝ้าดูโควต้า free plan (800/วัน) ถ้าชนให้เปลี่ยน cron scalp เป็น */10
ทดสอบ: DRY_RUN=1 TWELVEDATA_API_KEY=... python scalp_scanner.py
