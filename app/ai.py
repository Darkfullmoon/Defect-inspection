"""AI ตรวจรูปหลังแก้ไขด้วย Gemini + คลังเคส (RAG) ที่เก็บใน MySQL

- คลังเคส (AICase): สร้างทุกครั้งที่ผู้ตรวจ ตรวจรับ / ตีกลับ — สรุปเป็นข้อความแล้วเก็บ embedding
- การค้น: embedding ของงานใหม่ เทียบ cosine กับคลังทั้งหมดใน Python (ข้อมูลหลักพันแถว)
- ทุกฟังก์ชันที่เรียก Gemini จับ error เอง ไม่ทำให้ระบบเดิมพัง
"""
import io
import json
import logging
import math
import os
import threading

from flask import current_app
from PIL import Image

from .models import AICase, Defect, DefectLog, db
from .utils import SEVERITY

log = logging.getLogger(__name__)

EMBED_DIM = 768
MAX_BEFORE = 3
MAX_AFTER = 4
VERDICT_TH = {"approved": "ตรวจรับ (ผ่าน)", "rejected": "ตีกลับ (ไม่ผ่าน)"}


# ---------- Gemini client ----------
def client():
    ext = current_app.extensions
    if ext.get("gemini_client") is None:
        from google import genai
        ext["gemini_client"] = genai.Client(api_key=current_app.config["GEMINI_API_KEY"])
    return ext["gemini_client"]


def _types():
    from google.genai import types
    return types


def embed(text, task):
    cfg = _types().EmbedContentConfig(task_type=task, output_dimensionality=EMBED_DIM)
    r = client().models.embed_content(model=current_app.config["GEMINI_EMBED_MODEL"],
                                      contents=text, config=cfg)
    return [float(x) for x in r.embeddings[0].values]


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def load_images(photos, limit):
    """เปิดรูปจาก UPLOAD_DIR ย่อเหลือ ≤1024px แล้วคืนเป็น Part (ข้ามไฟล์ที่หาย)"""
    types = _types()
    root = current_app.config["UPLOAD_DIR"]
    parts = []
    for p in photos:
        if len(parts) >= limit:
            break
        fp = os.path.join(root, p.path)
        try:
            with Image.open(fp) as img:
                img = img.convert("RGB")
                img.thumbnail((1024, 1024))
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=85)
        except Exception:
            log.warning("ai: cannot read image %s", fp)
            continue
        parts.append(types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"))
    return parts


def defect_text(d):
    return (f"พื้นที่: {d.area.name if d.area else '-'}\n"
            f"ตำแหน่ง: {d.location}\n"
            f"ความรุนแรง: {SEVERITY.get(d.severity, d.severity)}\n"
            f"ปัญหา: {d.detail}")


# ---------- คลังเคส ----------
def case_doc_text(case):
    d = case.defect
    return (f"{defect_text(d)}\n"
            f"สภาพหลังแก้ไข: {case.photo_summary or '-'}\n"
            f"ผลการตรวจ: {VERDICT_TH.get(case.verdict, case.verdict)}\n"
            f"เหตุผลผู้ตรวจ: {case.inspector_note or '-'}")


CASE_PROMPT = ("คุณเป็นผู้ช่วยผู้ตรวจงานก่อสร้าง/ซ่อมบำรุง ดูรูปก่อนแก้ไขและหลังแก้ไขของงานนี้ "
               "แล้วบรรยายสภาพงานหลังแก้ไขเทียบกับก่อนแก้ สั้น ๆ 2–3 ประโยค ภาษาไทย "
               "ไม่ต้องตัดสินว่าผ่านหรือไม่ผ่าน")


def process_case(case_id):
    case = db.session.get(AICase, case_id)
    if case is None:
        return None
    try:
        d = case.defect
        after = d.after_rounds.get(case.round, [])
        contents = [CASE_PROMPT, defect_text(d), "รูปก่อนแก้ไข:"]
        contents += load_images(d.before_photos, MAX_BEFORE)
        contents.append(f"รูปหลังแก้ไข (รอบที่ {case.round}):")
        contents += load_images(after, MAX_AFTER)
        r = client().models.generate_content(model=current_app.config["GEMINI_MODEL"], contents=contents)
        case.photo_summary = (r.text or "").strip()
        case.doc_text = case_doc_text(case)
        case.embedding = json.dumps(embed(case.doc_text, "RETRIEVAL_DOCUMENT"))
        case.embed_model = current_app.config["GEMINI_EMBED_MODEL"]
        case.status = "done"
        case.error = ""
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log.warning("ai: case %s failed: %s", case_id, e)
        case = db.session.get(AICase, case_id)
        case.status = "error"
        case.error = str(e)[:500]
        db.session.commit()
    return case


# ---------- การค้นเคสคล้าย ----------
def query_text(d):
    last = (DefectLog.query.filter_by(defect_id=d.id, action="submitted")
            .order_by(DefectLog.id.desc()).first())
    note = last.note if last else ""
    return f"{defect_text(d)}\nโน้ตผู้แก้ไข: {note}"


def retrieve(defect, k=4):
    model = current_app.config["GEMINI_EMBED_MODEL"]
    rows = (AICase.query.filter(AICase.status == "done", AICase.embed_model == model,
                                AICase.defect_id != defect.id).all())
    if not rows:
        return []
    q = embed(query_text(defect), "RETRIEVAL_QUERY")
    scored = []
    for c in rows:
        try:
            vec = json.loads(c.embedding or "[]")
        except ValueError:
            continue
        scored.append((c, cosine(q, vec)))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:k]


# ---------- ตรวจรูปหลังแก้ (กดปุ่ม) ----------
VERDICTS = ("looks_fixed", "not_fixed", "unclear")
CONFIDENCES = ("high", "medium", "low")
CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "confidence": {"type": "string", "enum": list(CONFIDENCES)},
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "photo_issues": {"type": "array", "items": {"type": "string"}},
        "similar_cases": {"type": "array", "items": {
            "type": "object",
            "properties": {"code": {"type": "string"}, "why": {"type": "string"}},
            "required": ["code", "why"]}},
    },
    "required": ["verdict", "confidence", "summary", "findings", "photo_issues", "similar_cases"],
}
CHECK_PROMPT = """คุณเป็นผู้ช่วยผู้ตรวจงานก่อสร้าง/ซ่อมบำรุง หน้าที่คือให้ความเห็นประกอบการตรวจรับเท่านั้น (ผู้ตรวจเป็นคนตัดสิน)
เทียบรูปก่อนแก้ไขกับรูปหลังแก้ไขรอบล่าสุด แล้วประเมินว่าปัญหาที่ระบุได้รับการแก้ไขเรียบร้อยหรือไม่
- verdict: looks_fixed = ดูเรียบร้อย, not_fixed = น่าจะยังไม่เสร็จ/ยังมีปัญหา, unclear = รูปไม่ชัด ถ่ายคนละมุม หรือข้อมูลไม่พอ
- ใช้ประวัติการตีกลับของงานนี้ และเคสเก่าที่คล้ายกันเป็นมาตรฐานอ้างอิงของทีม
- similar_cases: ใส่เฉพาะเคสอ้างอิงที่เกี่ยวข้องจริง ใช้เลขที่ตามที่ให้ไว้
- ตอบเป็นภาษาไทย กระชับ"""


def _rejections(d):
    return [l.note for l in d.logs if l.action == "rejected" and l.note]


def _context_text(d, refs):
    lines = [defect_text(d), f"รอบที่ส่งงาน: {d.round}"]
    rej = _rejections(d)
    if rej:
        lines.append("ประวัติการตีกลับของงานนี้:")
        lines += [f"- รอบก่อน: {n}" for n in rej]
    if refs:
        lines.append("เคสเก่าที่คล้ายกัน (อ้างอิง):")
        for c, score in refs:
            lines.append(f"[{c.defect.code} รอบ {c.round}] (คล้าย {score:.2f})\n{c.doc_text}")
    else:
        lines.append("เคสเก่าที่คล้ายกัน: ยังไม่มีในคลัง")
    return "\n".join(lines)


def _parse_check(text):
    data = json.loads(text or "")
    if not isinstance(data, dict):
        raise ValueError("AI ตอบไม่อยู่ในรูปแบบที่กำหนด")
    if data.get("verdict") not in VERDICTS or data.get("confidence") not in CONFIDENCES:
        raise ValueError("AI ตอบผลไม่อยู่ในรูปแบบที่กำหนด")
    for key in ("findings", "photo_issues", "similar_cases"):
        if not isinstance(data.get(key), list):
            data[key] = []
    data["summary"] = str(data.get("summary") or "")
    return data


def check(defect, user):
    from .models import AICheck
    from .utils import AI_VERDICT

    d = defect
    chk = AICheck(defect_id=d.id, round=d.round, requested_by=user.id,
                  model=current_app.config["GEMINI_MODEL"])
    try:
        after = load_images(d.after_rounds.get(d.round, []), MAX_AFTER)
        if not after:
            raise LookupError("ไม่พบรูปหลังแก้ไขของรอบล่าสุด")
        refs = retrieve(d)
        contents = [CHECK_PROMPT, _context_text(d, refs), "รูปก่อนแก้ไข:"]
        contents += load_images(d.before_photos, MAX_BEFORE)
        contents.append(f"รูปหลังแก้ไข (รอบที่ {d.round}):")
        contents += after
        cfg = _types().GenerateContentConfig(response_mime_type="application/json",
                                             response_schema=CHECK_SCHEMA)
        r = client().models.generate_content(model=current_app.config["GEMINI_MODEL"],
                                             contents=contents, config=cfg)
        data = _parse_check(r.text)
        chk.status = "done"
        chk.verdict = data["verdict"]
        chk.result_json = json.dumps(data, ensure_ascii=False)
        chk.ref_case_ids = json.dumps([c.id for c, _ in refs])
    except Exception as e:
        db.session.rollback()
        log.warning("ai: check defect %s failed: %s", d.id, e)
        chk.status = "error"
        chk.error = str(e)[:500] if isinstance(e, (LookupError, ValueError)) and str(e) else \
            f"เรียก AI ไม่สำเร็จ: {str(e)[:400]}"
    db.session.add(chk)
    db.session.add(DefectLog(defect_id=d.id, user_id=user.id, action="ai_check",
                             note=AI_VERDICT.get(chk.verdict, "ผิดพลาด")))
    db.session.commit()
    return chk


# ---------- เก็บเคสเข้าคลัง (ตอนตรวจรับ/ตีกลับ) ----------
def _run_in_ctx(app, fn, *args):
    with app.app_context():
        try:
            fn(*args)
        except Exception:
            db.session.rollback()
            log.exception("ai: background job failed")
        finally:
            db.session.remove()


def run_background(fn, *args):
    app = current_app._get_current_object()
    if app.config.get("AI_INLINE"):
        return fn(*args)
    threading.Thread(target=_run_in_ctx, args=(app, fn) + args, daemon=True).start()
    return None


def _upsert_case(defect_id, rnd, verdict, note):
    c = AICase.query.filter_by(defect_id=defect_id, round=rnd).first()
    if c is None:
        c = AICase(defect_id=defect_id, round=rnd)
        db.session.add(c)
    c.verdict = verdict
    c.inspector_note = note or ""
    c.status = "pending"
    c.error = ""
    return c


def enqueue_case(defect, rnd, verdict, note):
    if not current_app.config.get("AI_ENABLED"):
        return None
    c = _upsert_case(defect.id, rnd, verdict, note)
    db.session.commit()
    run_background(process_case, c.id)
    return c


def process_pending_cases(limit=20):
    ids = [c.id for c in AICase.query.filter(AICase.status.in_(["pending", "error"]))
           .order_by(AICase.id).limit(limit)]
    done = 0
    for cid in ids:
        c = process_case(cid)
        if c is not None and c.status == "done":
            done += 1
    return done


def backfill_cases():
    """ไล่ประวัติ: รอบ = จำนวนครั้ง 'submitted' ก่อนหน้า log ตรวจรับ/ตีกลับนั้น คืนจำนวนเคสที่เพิ่มใหม่"""
    existing = {(c.defect_id, c.round) for c in AICase.query.with_entities(AICase.defect_id, AICase.round)}
    added = 0
    for d in Defect.query.order_by(Defect.id):
        rnd = 0
        for l in d.logs:
            if l.action == "submitted":
                rnd += 1
            elif l.action in ("approved", "rejected") and rnd and (d.id, rnd) not in existing:
                if not d.after_rounds.get(rnd):
                    continue
                db.session.add(AICase(defect_id=d.id, round=rnd, verdict=l.action,
                                      inspector_note=l.note or ""))
                existing.add((d.id, rnd))
                added += 1
    db.session.commit()
    return added


def case_stats():
    rows = db.session.query(AICase.status, db.func.count(AICase.id)).group_by(AICase.status).all()
    out = {"done": 0, "pending": 0, "error": 0}
    out.update(dict(rows))
    return out
