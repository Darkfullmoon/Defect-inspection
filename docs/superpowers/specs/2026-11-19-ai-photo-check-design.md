# AI ตรวจรูปหลังแก้ไข (Gemini + RAG) — Design

วันที่: 2026-11-19

## เป้าหมายและเหตุผล

ให้ AI ช่วยดูรูป **หลังแก้ไข** ของ Defect เทียบกับรูปก่อนแก้ แล้วให้ความเห็นว่างานดูเรียบร้อยหรือยัง
โดยอ้างอิงเคสเก่าในระบบที่คล้ายกัน (RAG) — เคสที่ผู้ตรวจเคยรับ/ตีกลับ และเหตุผลที่เขียนไว้ — เพื่อให้ความเห็นสอดคล้องกับมาตรฐานของทีม
จุดประสงค์คือลดการตีกลับไปมา (ซึ่งทำให้เลยกำหนดจนออก NCR) และช่วยผู้ตรวจตัดสินใจเร็วขึ้น

## ขอบเขต

**ทำ**
- ปุ่ม **"ให้ AI ตรวจรูป"** ในหน้ารายละเอียด Defect (กดเมื่อต้องการ — AI เป็น optional)
- คลังเคส (case memory) ที่สร้างอัตโนมัติทุกครั้งผู้ตรวจ ตรวจรับ / ตีกลับ
- ปุ่ม Admin สร้างคลังย้อนหลังจากประวัติที่มีอยู่
- ช่อง `GEMINI_API_KEY` ใน `.env`

**ไม่ทำ (non-goals)**
- AI ไม่ตรวจรับ/ตีกลับ/ปิดงานเอง ไม่เปลี่ยนสถานะ Defect ไม่กระทบ NCR — **คนตัดสินเสมอ**
- ไม่ช่วยตอนเปิด Defect (จุดที่ 2) และไม่มีหน้าค้นหาเคสด้วยรูป (จุดที่ 3) — ไว้ทีหลัง
- ไม่ใช้ vector DB แยก ไม่เพิ่ม service ใน Docker

**ถือว่าล้มเหลว (anti-goals)**
- ระบบเดิมช้าลงหรือพังเมื่อไม่มี key / เน็ตหลุด / Gemini error
- ความเห็น AI ถูกแสดงเหมือนเป็นผลตรวจทางการ

## ข้อจำกัด

- Flask + SQLAlchemy + MySQL (Docker) เดิม ตารางใหม่สร้างด้วย `db.create_all()` ตามที่ระบบทำอยู่
- ใช้ Gemini ผ่าน package `google-genai`
- `GEMINI_API_KEY` ว่าง = ปิด AI ทั้งหมด (ซ่อนปุ่ม, ไม่สร้างคลัง) ระบบทำงานเหมือนเดิม 100%
- ข้อความที่ AI ตอบเป็นภาษาไทย
- รูปถูกส่งไป Google — ถ้าใช้ key แบบฟรี Google อาจนำข้อมูลไปใช้ปรับปรุงโมเดล; แบบเสียเงินไม่นำไปใช้ (แจ้งไว้ใน README)

## วิธีที่พิจารณา (Approaches)

| แบบ | วิธี | ข้อดี | ข้อเสีย |
|---|---|---|---|
| **A (เลือก)** | ค้นด้วยความหมาย: สรุปเคสเป็นข้อความ → embedding ด้วย Gemini → เก็บใน MySQL → cosine similarity | หาเจอแม้ใช้คำต่างกัน/คนละพื้นที่ | เรียก Gemini เพิ่มตอนปิดเคส (ถูกมาก) |
| B | ค้นจากพื้นที่ / คำซ้ำในรายละเอียด | ง่าย ไม่ต้องเก็บเพิ่ม | หาเคสคล้ายไม่เจอถ้าคำต่างกัน |
| C | vector DB แยก (Chroma ฯลฯ) | สเกลได้มาก | เกินจำเป็นกับข้อมูลหลักร้อย-พันเคส |

ผู้ใช้เลือก **A** และเลือกให้ AI ทำงาน **เมื่อกดปุ่มเท่านั้น** (ไม่ใช่ติ๊กตอนส่งงาน หรือสวิตช์อัตโนมัติทั้งระบบ)

## การออกแบบ

### การตั้งค่า (`.env` / `config.py`)
```
GEMINI_API_KEY=                       # ว่าง = ปิด AI
GEMINI_MODEL=gemini-2.5-flash         # โมเดลดูรูป
GEMINI_EMBED_MODEL=gemini-embedding-001
```
`config.AI_ENABLED = bool(GEMINI_API_KEY)` และส่งเข้า template เป็น global

### ตารางใหม่
- **`ai_cases`** — คลังเคส 1 แถวต่อ 1 การตัดสิน (รับ/ตีกลับ) ของ 1 รอบ
  `id, defect_id (FK cascade), round, verdict (approved/rejected), inspector_note, photo_summary (Text: Gemini บรรยายรูปหลังแก้), doc_text (Text: ข้อความที่นำไป embed), embedding (LONGTEXT JSON list[float]), embed_model, status (pending/done/error), created_at`
  unique (defect_id, round)
- **`ai_checks`** — ผลการกดตรวจแต่ละครั้ง
  `id, defect_id (FK cascade), round, requested_by (FK users), status (done/error), verdict (looks_fixed/not_fixed/unclear), result_json (Text), ref_case_ids (Text JSON), model, error (Text), created_at`

### โมดูล `app/ai.py`
- `client()` — สร้าง `genai.Client(api_key=...)` ครั้งเดียว
- `build_case(defect, round, verdict, note)` — ส่งรูปก่อนแก้ + รูปหลังแก้ของรอบนั้น (ย่อขนาดจาก thumbnail/ภาพย่อ ไม่เกิน ~1024px, สูงสุด 3+4 รูป) ให้ Gemini บรรยายสภาพหลังแก้สั้น ๆ → ประกอบ `doc_text` = พื้นที่ + ตำแหน่ง + ความรุนแรง + รายละเอียดปัญหา + บรรยายรูป + ผล (รับ/ตีกลับ) + เหตุผลผู้ตรวจ → embed (`task_type=RETRIEVAL_DOCUMENT`, 768 มิติ) → บันทึก `ai_cases`
- `retrieve(defect, k=4)` — query = พื้นที่ + ตำแหน่ง + รายละเอียดปัญหา + โน้ตผู้ส่งงานล่าสุด, embed (`RETRIEVAL_QUERY`), cosine กับ `ai_cases` status=done ทั้งหมดใน Python (ไม่มี numpy ก็พอสำหรับหลักพันแถว) ตัดเคสของ Defect เดียวกันออก แล้วคืน top-k
- `check(defect, user)` — ส่งให้ Gemini: รูปก่อนแก้ + รูปหลังแก้ของ **รอบล่าสุด** + ข้อมูล Defect + **ประวัติการตีกลับของ Defect นี้เอง** (เหตุผลจาก DefectLog) + เคสคล้ายจาก `retrieve` (ข้อความเท่านั้น ไม่ส่งรูปเคสเก่า เพื่อคุมค่าใช้จ่าย) ขอผลเป็น JSON (structured output):
  ```json
  {"verdict": "looks_fixed|not_fixed|unclear",
   "confidence": "high|medium|low",
   "summary": "สรุป 1–2 ประโยค",
   "findings": ["จุดที่พบ ..."],
   "photo_issues": ["รูปไม่ชัด / คนละมุม ..."],
   "similar_cases": [{"code": "DEF-...", "why": "..."}]}
  ```
  บันทึก `ai_checks`; error ใด ๆ บันทึกเป็น status=error พร้อมข้อความ ไม่ raise ออกไปหน้าเว็บ

### จุดเชื่อมกับระบบเดิม
- **`defects.review`** — หลัง commit การรับ/ตีกลับ ถ้า AI_ENABLED ให้สร้าง `ai_cases` แถว pending แล้วประมวลผลใน background thread (ภายใต้ app context) เพื่อไม่ให้ผู้ตรวจรอ; ล้มเหลว = status error รอทำซ้ำ
- **Scheduler เดิม (`jobs.py`)** — เพิ่มงานเก็บกวาด: ประมวลผล `ai_cases` ที่ pending/error ค้าง (จำกัดจำนวนต่อรอบ)
- **route ใหม่ `POST /defects/<id>/ai-check`** — สิทธิ์: ผู้ตรวจของงาน, admin, ผู้รับผิดชอบของงาน; เฉพาะ status `submitted`; ทำแบบ synchronous (ใช้เวลา ~5–20 วินาที) หน้าเว็บกดผ่าน fetch แสดง spinner แล้ว reload ส่วนผล; กดซ้ำได้ แสดงผลล่าสุดของรอบนั้น
- **DefectLog** — เพิ่ม action `ai_check` ("ให้ AI ตรวจรูป") ใน `LOG_ACTIONS`
- **Admin** — ปุ่ม "สร้างคลังเคส AI ย้อนหลัง": ไล่ DefectLog `approved`/`rejected` ทั้งหมด จับคู่กับรอบ (นับจำนวน `submitted` ก่อนหน้า log นั้น = เลขรอบ) สร้าง `ai_cases` pending แล้วให้ background/scheduler ประมวลผล แสดงจำนวน done/pending/error

### หน้าจอ (`defects/detail.html`)
การ์ดใต้ส่วนรูปหลังแก้ แสดงเฉพาะเมื่อ AI_ENABLED:
```
┌ ความเห็น AI (ใช้ประกอบ — ผู้ตรวจเป็นผู้ตัดสิน) ─────────┐
│ ⚠ น่าจะยังไม่เสร็จ  · ความมั่นใจ: กลาง                │
│ ยังเห็นรอยร้าวบริเวณมุมซ้ายล่าง                        │
│ • ...                                               │
│ เคสคล้าย: DEF-2569-00031 (เคยถูกตีกลับเรื่องรอยต่อ)    │
│ ตรวจเมื่อ 19 พ.ย. 69 14:02 โดย ABC · รอบที่ 2          │
│                         [ให้ AI ตรวจรูปอีกครั้ง]       │
└─────────────────────────────────────────────────────┘
```
สถานะ `submitted` และยังไม่เคยตรวจรอบนี้ → แสดงปุ่ม "ให้ AI ตรวจรูป"; สถานะอื่นแสดงผลเก่าอ่านอย่างเดียว (ถ้ามี) ป้ายผล: เขียว = ดูเรียบร้อย, ส้ม = น่าจะยังไม่เสร็จ, เทา = ไม่แน่ใจ/รูปไม่พอ

### Dependencies
เพิ่ม `google-genai` ใน `requirements.txt` (ระบุเวอร์ชันที่ติดตั้งได้จริงตอนสร้าง)

### ทดสอบ
- mock Gemini client (ไม่เรียกจริงใน test)
- ไม่มี key → ไม่เห็นปุ่ม, review ทำงานเหมือนเดิม, ไม่สร้าง ai_cases
- มี key → review สร้าง ai_cases; ai-check บันทึกผล; Gemini error → status error ไม่ 500
- สิทธิ์: ผู้ใช้ที่ไม่เกี่ยวข้องกด ai-check ได้ 403; สถานะไม่ใช่ submitted ได้ 403
- retrieve ตัดเคสของ Defect เดียวกันออก และเรียงตามความคล้าย
- การจับคู่รอบในการสร้างคลังย้อนหลัง

## สิ่งที่ผู้สร้างตัดสินเองได้
- ถ้อยคำ prompt, ขนาด/จำนวนรูปที่ส่งจริง, ค่า k
- รายละเอียดสไตล์การ์ดให้เข้ากับ template เดิม (glass, reveal)
- จำนวนงานเก็บกวาดต่อรอบ scheduler
- ชื่อโมเดล default ให้ตรวจว่ายังใช้ได้ตอนสร้าง (ตั้งค่าได้ใน .env อยู่แล้ว)
