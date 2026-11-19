# AI Photo Check (Gemini + RAG) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ปุ่ม "ให้ AI ตรวจรูป" สำหรับผู้ตรวจ/admin ที่ให้ Gemini เทียบรูปก่อน-หลังแก้ โดยอ้างอิงเคสเก่าที่คล้ายกัน (RAG บน MySQL)

**Architecture:** โมดูลใหม่ `app/ai.py` ห่อ Gemini client (ฉีด fake ได้ผ่าน `app.extensions["gemini_client"]`) คลังเคส `ai_cases` สร้างเมื่อรับ/ตีกลับ (background thread, inline ตอน TESTING) เก็บ embedding เป็น JSON แล้ว cosine ใน Python; ผลตรวจเก็บใน `ai_checks` แสดงเป็นการ์ดในหน้ารายละเอียด

**Tech Stack:** Flask 3, SQLAlchemy 2, MySQL/SQLite, `google-genai==3.5.0`, pytest

**Spec:** `docs/superpowers/specs/2026-11-19-ai-photo-check-design.md`

## Global Constraints

- `GEMINI_API_KEY` ว่าง → `AI_ENABLED=False`: ไม่มีปุ่ม/การ์ด, ไม่สร้าง ai_cases, ไม่ import/เรียก Gemini, ทุก flow เดิมทำงานเหมือนเดิม
- ค่าเริ่มต้น: `GEMINI_MODEL=gemini-2.5-flash`, `GEMINI_EMBED_MODEL=gemini-embedding-001`, embedding 768 มิติ
- AI ไม่เปลี่ยน `Defect.status`, ไม่แตะ NCR, ไม่ส่ง notification
- ปุ่มและการ์ด AI: เฉพาะ `can_manage(d)` (ผู้ตรวจของงาน + admin) — ผู้รับผิดชอบไม่เห็น
- ปุ่มกดได้เฉพาะ `d.status == "submitted"`
- ข้อความ AI เป็นภาษาไทย; การ์ดมีหัว "ความเห็น AI (ใช้ประกอบ — ผู้ตรวจเป็นผู้ตัดสิน)"
- Error จาก Gemini ไม่ทำให้เกิด 500 — บันทึกเป็น status `error`
- เคสเก่าใน prompt ส่งเป็นข้อความเท่านั้น ไม่ส่งรูป; รูปที่ส่งต่อครั้ง: ก่อนแก้ ≤3, หลังแก้รอบล่าสุด ≤4 (ใช้ไฟล์ `path` ที่ระบบย่อไว้ 1600px แล้วย่อเหลือ ≤1024px ก่อนส่ง)
- รัน test: `.venv/Scripts/python.exe -m pytest -q`

## Review Focus

1. Defect ที่ไม่มีรูปหลังแก้ในรอบล่าสุด (ข้อมูลเก่าเสีย/ไฟล์หาย) → ai-check ต้องได้ผล status error ข้อความไทย ไม่ 500 — test ใน Task 3
2. Gemini ตอบ JSON ไม่ถูกต้อง/ไม่ครบ field → บันทึก error ไม่ 500 — test ใน Task 3
3. ยังไม่มีเคสในคลังเลย (ระบบใหม่) → ai-check ยังทำงานได้ similar_cases ว่าง — test ใน Task 2 (`retrieve` คืน `[]`)
4. รับ/ตีกลับซ้ำรอบเดิม (unique defect_id+round) → ไม่ IntegrityError — test ใน Task 4
5. embedding คนละมิติ/คนละโมเดล (เปลี่ยน GEMINI_EMBED_MODEL ภายหลัง) → retrieve ข้ามแถวที่ `embed_model` ไม่ตรง — test ใน Task 2

---

### Task 1: Config, models, dependency

**Files:**
- Modify: `app/config.py`, `app/models.py`, `app/utils.py` (LOG_ACTIONS), `app/__init__.py` (template global), `requirements.txt`, `.env`
- Test: `tests/test_ai.py`

**Interfaces:**
- Produces: `Config.GEMINI_API_KEY/GEMINI_MODEL/GEMINI_EMBED_MODEL/AI_INLINE`; `app.config["AI_ENABLED"]` (คำนวณใน `create_app` หลัง `config.update` = `bool(GEMINI_API_KEY)`); jinja global `AI_ENABLED`
- `AICase(id, defect_id FK cascade, round:int, verdict:str(10), inspector_note:Text, photo_summary:Text, doc_text:Text, embedding:Text(LONGTEXT on MySQL via `db.Text().with_variant(mysql.LONGTEXT(), "mysql")`), embed_model:str(60), status:str(8)="pending", error:Text, created_at)`, `UniqueConstraint(defect_id, round)`, `__tablename__="ai_cases"`
- `AICheck(id, defect_id FK cascade, round, requested_by FK users, status:str(8), verdict:str(12), result_json:Text, ref_case_ids:Text="[]", model:str(60), error:Text, created_at)`, property `result -> dict` (json.loads, `{}` on fail), `__tablename__="ai_checks"`; `Defect.ai_checks` relationship order_by id, cascade delete-orphan
- `LOG_ACTIONS["ai_check"] = "ให้ AI ตรวจรูป"`
- `AI_INLINE` = `_bool("AI_INLINE","0")`; tests set True

- [ ] **Step 1: Write tests** in `tests/test_ai.py` — reuse fixture pattern from `tests/test_flow.py` (copy `png`, `Browser`; new fixture `make_app(tmp_path, key="")`):
  - `test_ai_disabled_without_key`: `app.config["AI_ENABLED"] is False`; tables `ai_cases`, `ai_checks` exist (`db.inspect(db.engine).get_table_names()`)
  - `test_ai_enabled_with_key`: with `GEMINI_API_KEY="x"` → `AI_ENABLED is True`
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/test_ai.py -q` → FAIL
- [ ] **Step 3: Implement** above; add `google-genai==3.5.0` to requirements; append to `.env`:
  ```
  # ---- AI ตรวจรูป (Gemini) — เว้นว่าง = ปิด ----
  GEMINI_API_KEY=
  GEMINI_MODEL=gemini-2.5-flash
  GEMINI_EMBED_MODEL=gemini-embedding-001
  ```
  then `.venv/Scripts/python.exe -m pip install google-genai==3.5.0`
- [ ] **Step 4: Run** full suite → PASS (test_flow still green)
- [ ] **Step 5: Commit** `feat(ai): config, models, google-genai dependency`

### Task 2: Gemini wrapper, case memory, retrieval

**Files:**
- Create: `app/ai.py`
- Test: `tests/test_ai.py`, shared fake in `tests/fake_gemini.py`

**Interfaces:**
- `client()` → `current_app.extensions.get("gemini_client")` or cached `google.genai.Client(api_key=...)` (import inside function)
- `embed(text:str, task:str) -> list[float]` — `client().models.embed_content(model=EMBED_MODEL, contents=text, config=types.EmbedContentConfig(task_type=task, output_dimensionality=768))`, return `.embeddings[0].values` as list
- `cosine(a, b) -> float` (0.0 if any norm 0 or len differs)
- `load_images(photos, limit:int) -> list[types.Part]` — open `UPLOAD_DIR/photo.path`, skip missing files, thumbnail 1024, JPEG bytes → `types.Part.from_bytes(data, mime_type="image/jpeg")`
- `case_doc_text(case:AICase) -> str` — lines: พื้นที่, ตำแหน่ง, ความรุนแรง (SEVERITY label), ปัญหา, สภาพหลังแก้ (photo_summary), ผล (`ตรวจรับ` / `ตีกลับ`), เหตุผลผู้ตรวจ
- `process_case(case_id:int) -> AICase` — generate_content with before≤3 + after(round)≤4 images + prompt "บรรยายสภาพงานหลังแก้ไขเทียบกับก่อนแก้ สั้น ๆ 2–3 ประโยค ภาษาไทย" → `photo_summary`; `doc_text = case_doc_text`; `embedding = json.dumps(embed(doc_text,"RETRIEVAL_DOCUMENT"))`; `embed_model`; status `done`; on any Exception: rollback, status `error`, `error=str(e)[:500]`, commit; never raises
- `retrieve(defect:Defect, k:int=4) -> list[tuple[AICase, float]]` — query text = area + location + detail + latest `submitted` DefectLog note; `embed(...,"RETRIEVAL_QUERY")`; candidates `AICase.status=="done"`, `embed_model==current`, `defect_id != defect.id`; sort by cosine desc; top k
- Fake (`tests/fake_gemini.py`): `FakeGemini(embed_fn=None, reply="...")` with `.models.embed_content(...)` returning `SimpleNamespace(embeddings=[SimpleNamespace(values=vec)])` where default vec is deterministic bag-of-chars-hash of text (len 768), and `.models.generate_content(...)` returning `SimpleNamespace(text=reply)`; records `calls`

- [ ] **Step 1: Write tests**
  - `test_cosine`: `cosine([1,0],[1,0])==1.0`, `cosine([1,0],[0,1])==0.0`, `cosine([0,0],[1,1])==0.0`, `cosine([1],[1,1])==0.0`
  - `test_process_case_done_and_error`: create defect with before + after round 1 (helper `make_defect(app)` in test file driving the HTTP flow like test_flow), add `AICase(round=1, verdict="rejected", inspector_note="ยังร้าว")`; with fake reply "ผนังยังมีรอยร้าว" → status `done`, `photo_summary=="ผนังยังมีรอยร้าว"`, `"ตีกลับ" in doc_text`, `len(json.loads(embedding))==768`; with fake whose generate raises → status `error`, no exception
  - `test_retrieve_orders_and_filters`: 3 done cases on other defects with hand-set embeddings `[1,0..]`, `[0.9,0.1..]`, `[0,1..]` + one on same defect + one with `embed_model="old"`; fake query vec `[1,0..]` → returned codes in order case1, case2, case3, same-defect and old-model excluded
  - `test_retrieve_empty`: no cases → `[]`
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement** `app/ai.py` per Interfaces
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat(ai): gemini wrapper, case memory, retrieval`

### Task 3: AI check + route + detail card

**Files:**
- Modify: `app/ai.py`, `app/defects.py`, `app/templates/defects/detail.html`
- Test: `tests/test_ai.py`

**Interfaces:**
- `check(defect:Defect, user:User) -> AICheck` in `app/ai.py`; uses `retrieve(defect)` and prior `rejected` DefectLog notes of this defect; `generate_content` with `config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=<schema dict>)`; expected JSON keys exactly: `verdict` ∈ {`looks_fixed`,`not_fixed`,`unclear`}, `confidence` ∈ {`high`,`medium`,`low`}, `summary`, `findings[]`, `photo_issues[]`, `similar_cases[{code,why}]`; validate verdict/confidence, else error. If latest round has no loadable after images → status error `"ไม่พบรูปหลังแก้ไขของรอบล่าสุด"` without calling Gemini. Always adds `DefectLog(action="ai_check", note=<verdict label or "ผิดพลาด">)`, commits, never raises
- Verdict labels (utils): `AI_VERDICT = {"looks_fixed": "ดูเรียบร้อย", "not_fixed": "น่าจะยังไม่เสร็จ", "unclear": "ไม่แน่ใจ / รูปไม่พอ"}`, jinja global
- Route `POST /defects/<int:defect_id>/ai-check` (`defects.ai_check`): `get_defect_or_404`; 404 if not AI_ENABLED; 403 if `not can_manage(d)` or `d.status != "submitted"`; calls `ai.check`; redirect to detail `#ai`
- Template: card `id="ai"` placed after after-photos section; shown only `{% if AI_ENABLED and manage %}`; shows latest `AICheck` for `d.round` (fallback: latest any round, read-only); button form (csrf) shown when submitted; button disables + text "AI กำลังตรวจ… (ประมาณ 5–20 วินาที)" on submit via inline JS; badge colors green/orange/gray by verdict; error shows `check.error`

- [ ] **Step 1: Write tests**
  - `test_ai_check_inspector`: submitted defect, fake reply valid JSON verdict `not_fixed` → POST 302; AICheck status done, verdict not_fixed; detail page contains "น่าจะยังไม่เสร็จ" and "ผู้ตรวจเป็นผู้ตัดสิน"; `Defect.status` still `submitted`; DefectLog has `ai_check`
  - `test_ai_check_permissions`: assignee POST → 403 and detail page lacks "ความเห็น AI"; inspector POST on `open` defect → 403
  - `test_ai_check_disabled`: no key → POST 404; detail lacks "ให้ AI ตรวจรูป"
  - `test_ai_check_bad_json`: fake reply `"not json"` → 302, AICheck status error
  - `test_ai_check_missing_after_photo`: delete after-photo files from disk → AICheck status error, message "ไม่พบรูปหลังแก้ไขของรอบล่าสุด", fake `generate_content` not called
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** full suite → PASS
- [ ] **Step 5: Commit** `feat(ai): on-demand AI photo check for inspectors`

### Task 4: Case capture on review, scheduler sweep, admin backfill

**Files:**
- Modify: `app/ai.py`, `app/defects.py` (review), `app/jobs.py`, `app/admin.py`, create `app/templates/admin/ai.html`, nav link in `app/templates/base.html` (admin menu, only if AI_ENABLED)
- Test: `tests/test_ai.py`

**Interfaces:**
- `enqueue_case(defect, round:int, verdict:str, note:str) -> AICase|None` — no-op None if not AI_ENABLED; get-or-create by (defect_id, round) (update verdict/note, reset status pending if existing); commit; then `AI_INLINE` → `process_case(id)` directly, else `threading.Thread(target=_run_in_ctx, args=(app, id), daemon=True).start()`
- `process_pending_cases(limit:int=20) -> int` — status in (pending, error), oldest first
- `backfill_cases() -> int` — for each defect, walk logs by id; `round` counter increments at each `submitted`; each `approved`/`rejected` log → `enqueue`-style get-or-create pending (no thread per row; returns count created); then caller triggers processing
- review: after `db.session.commit()` call `ai.enqueue_case(d, d.round, "approved"|"rejected", note)` wrapped in try/except logging (never breaks review)
- jobs: in `start_scheduler.run()` after check_overdue, if AI_ENABLED call `process_pending_cases()` (own try/except)
- Admin `GET/POST /admin/ai` (`admin.ai`): GET shows counts done/pending/error; POST `action=backfill` → `backfill_cases()` then background `process_pending_cases(limit=1000)` thread (inline when AI_INLINE), flash "เพิ่มเคสเข้าคิว N เคส"; 404 if not AI_ENABLED

- [ ] **Step 1: Write tests**
  - `test_review_creates_case`: reject round 1 → AICase(round=1, verdict rejected, status done); resubmit + approve round 2 → second AICase verdict approved
  - `test_review_without_key_no_case`: AICase count 0
  - `test_enqueue_same_round_twice`: call `enqueue_case` twice for same round → 1 row, no IntegrityError
  - `test_review_survives_ai_failure`: fake raising on everything → review still 302 and Defect closed
  - `test_backfill_round_mapping`: build history created→submitted→rejected→submitted→approved with key disabled, then enable (`app.config["AI_ENABLED"]=True`, inject fake) → `backfill_cases()==2`, rounds {1: rejected, 2: approved}; calling again returns 0
  - `test_admin_ai_page`: admin GET 200 shows counts; inspector GET 403
- [ ] **Step 2: Run** → FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** full suite → PASS
- [ ] **Step 5: Commit** `feat(ai): case memory capture, sweep, admin backfill`

### Task 5: Docs and live check

**Files:**
- Modify: `README.md`

- [ ] **Step 1:** README section "AI ตรวจรูป (Gemini)": ขอ key ที่ aistudio.google.com, ใส่ `GEMINI_API_KEY` ใน .env แล้วเปิดโปรแกรมใหม่, ใครกดได้, กด "สร้างคลังเคสย้อนหลัง" ครั้งแรก, หมายเหตุ key ฟรีอาจถูกใช้ข้อมูลฝึกโมเดล, ปล่อยว่าง = ปิด
- [ ] **Step 2:** restart app with empty key, open a defect page → no AI UI, no errors in log
- [ ] **Step 3: Commit** `docs: AI photo check setup`
