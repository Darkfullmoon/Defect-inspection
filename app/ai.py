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
