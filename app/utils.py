import os
import re
import secrets
import uuid
from datetime import date, datetime

from flask import abort, current_app, g, session
from PIL import Image, ImageOps

from .models import Counter, Defect, db

TH_MONTHS = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
             "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
TH_MONTHS_FULL = ["", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
                  "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]

STATUS = {
    "open": "รอแก้ไข",
    "rework": "ตีกลับแก้ไขใหม่",
    "submitted": "รอตรวจรับ",
    "closed": "แก้ไขแล้ว",
}
BUCKETS = {"progress": "กำลังดำเนินการ", "closed": "แก้ไขแล้ว", "ncr": "เกินกำหนด (NCR)"}
AI_VERDICT = {"looks_fixed": "ดูเรียบร้อย", "not_fixed": "น่าจะยังไม่เสร็จ", "unclear": "ไม่แน่ใจ / รูปไม่พอ"}
SEVERITY = {"low": "ต่ำ", "medium": "ปานกลาง", "high": "สูง"}
ROLES = {"admin": "ผู้ดูแลระบบ", "inspector": "ผู้ตรวจ", "user": "ผู้รับผิดชอบแก้ไข"}
LOG_ACTIONS = {
    "created": "เปิด Defect",
    "edited": "แก้ไขข้อมูล",
    "submitted": "ส่งงานแก้ไข",
    "approved": "ตรวจรับ / ปิดงาน",
    "rejected": "ตีกลับให้แก้ไขใหม่",
    "ncr": "ออก NCR",
    "reminded": "แจ้งเตือนใกล้ครบกำหนด",
    "ai_check": "ให้ AI ตรวจรูป",
}

USERNAME_RE = re.compile(r"^[A-Z]{3}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------- วันที่แบบไทย ----------
def thdate(d, full=False):
    if not d:
        return "-"
    if isinstance(d, datetime):
        d = d.date()
    year = d.year + 543
    if full:
        return f"{d.day} {TH_MONTHS_FULL[d.month]} {year}"
    return f"{d.day} {TH_MONTHS[d.month]} {str(year)[-2:]}"


def thdatetime(dt):
    if not dt:
        return "-"
    return f"{thdate(dt)} {dt:%H:%M}"


def parse_date(s):
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


# ---------- รหัสผ่าน ----------
def password_problems(pw):
    pw = pw or ""
    probs = []
    if len(pw) < 8:
        probs.append("ยาวอย่างน้อย 8 ตัวอักษร")
    if not re.search(r"[A-Z]", pw):
        probs.append("มีตัวพิมพ์ใหญ่ภาษาอังกฤษอย่างน้อย 1 ตัว")
    if not re.search(r"[0-9]", pw):
        probs.append("มีตัวเลขอย่างน้อย 1 ตัว")
    if not re.search(r"[^A-Za-z0-9]", pw):
        probs.append("มีอักขระพิเศษอย่างน้อย 1 ตัว เช่น ! @ # * _")
    if pw == current_app.config["DEFAULT_PASSWORD"]:
        probs.append("ต้องไม่ซ้ำกับรหัสกลาง")
    return probs


# ---------- CSRF ----------
def csrf_token():
    tok = session.get("_csrf")
    if not tok:
        tok = session["_csrf"] = secrets.token_urlsafe(32)
    return tok


# ---------- เลขที่เอกสาร ----------
def next_code(prefix, width):
    be = date.today().year + 543
    name = f"{prefix}-{be}"
    c = db.session.query(Counter).filter_by(name=name).with_for_update().first()
    if c is None:
        c = Counter(name=name, value=0)
        db.session.add(c)
    c.value += 1
    db.session.flush()
    return f"{name}-{c.value:0{width}d}"


# ---------- สิทธิ์ ----------
def defect_scope(query=None):
    q = query if query is not None else Defect.query
    u = g.user
    if u.role == "inspector":
        q = q.filter(Defect.inspector_id == u.id)
    elif u.role == "user":
        q = q.filter(Defect.assignee_id == u.id)
    return q


def can_view(d):
    u = g.user
    return u.role == "admin" or d.inspector_id == u.id or d.assignee_id == u.id


def can_manage(d):
    u = g.user
    return u.role == "admin" or d.inspector_id == u.id


def get_defect_or_404(defect_id):
    d = db.session.get(Defect, defect_id)
    if d is None or not can_view(d):
        abort(404)
    return d


# ---------- รูปภาพ ----------
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif"}


def save_image(fs):
    """ย่อรูปเหลือด้านยาว 1600px + ทำรูปย่อ 480px คืน (path, thumb_path) แบบ relative"""
    if not fs or not fs.filename:
        return None
    ext = os.path.splitext(fs.filename)[1].lower()
    if ext and ext not in ALLOWED_EXT:
        return None
    try:
        img = Image.open(fs.stream)
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
    except Exception:
        return None
    sub = datetime.now().strftime("%Y/%m")
    root = current_app.config["UPLOAD_DIR"]
    os.makedirs(os.path.join(root, sub), exist_ok=True)
    name = uuid.uuid4().hex
    rel = f"{sub}/{name}.jpg"
    rel_t = f"{sub}/{name}_t.jpg"
    big = img.copy()
    big.thumbnail((1600, 1600))
    big.save(os.path.join(root, rel), "JPEG", quality=85, optimize=True)
    small = img.copy()
    small.thumbnail((480, 480))
    small.save(os.path.join(root, rel_t), "JPEG", quality=78, optimize=True)
    return rel, rel_t


def save_images(files):
    out = []
    for fs in files:
        r = save_image(fs)
        if r:
            out.append(r)
    return out
