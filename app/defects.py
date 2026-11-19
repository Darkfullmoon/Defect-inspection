from datetime import date, timedelta

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   url_for)
from sqlalchemy import or_

from .auth import roles
from .jobs import issue_ncr
from .models import Area, Defect, DefectLog, Photo, User, db, now
from .notify import notify
from .utils import (SEVERITY, STATUS, can_manage, defect_scope, get_defect_or_404, next_code,
                    parse_date, save_images, thdate)

bp = Blueprint("defects", __name__, url_prefix="/defects")
PER_PAGE = 10


def filtered_query(args):
    q = defect_scope().join(Area)
    text = (args.get("q") or "").strip()
    if text:
        like = f"%{text}%"
        q = q.filter(or_(Defect.code.ilike(like), Defect.location.ilike(like),
                         Defect.detail.ilike(like), Area.name.ilike(like)))
    st = args.get("status") or ""
    if st == "ncr":
        q = q.filter(Defect.status != "closed", Defect.ncr.has())
    elif st == "progress":
        q = q.filter(Defect.status != "closed", ~Defect.ncr.has())
    elif st in STATUS:
        q = q.filter(Defect.status == st)
    area = args.get("area", type=int)
    if area:
        q = q.filter(Defect.area_id == area)
    sev = args.get("severity") or ""
    if sev in SEVERITY:
        q = q.filter(Defect.severity == sev)
    person = args.get("person", type=int)
    if person:
        q = q.filter(or_(Defect.assignee_id == person, Defect.inspector_id == person))
    d1, d2 = parse_date(args.get("from")), parse_date(args.get("to"))
    if d1:
        q = q.filter(Defect.created_at >= d1)
    if d2:
        q = q.filter(Defect.created_at < d2 + timedelta(days=1))
    return q


@bp.route("/")
def index():
    page = max(request.args.get("page", 1, type=int), 1)
    q = filtered_query(request.args).order_by(Defect.created_at.desc(), Defect.id.desc())
    pg = q.paginate(page=page, per_page=PER_PAGE, error_out=False)
    areas = Area.query.order_by(Area.name).all()
    return render_template("defects/index.html", pg=pg, areas=areas)


def assignable_users(inspector):
    if inspector is None:
        return []
    return [u for u in inspector.team if u.active]


def inspectors_list():
    return User.query.filter_by(role="inspector", active=True).order_by(User.username).all()


def read_form(d=None):
    """อ่านค่าจากฟอร์มเปิด/แก้ไข Defect คืน (data, errors)"""
    f = request.form
    errors = []
    data = {
        "area_id": f.get("area_id", type=int),
        "location": (f.get("location") or "").strip(),
        "detail": (f.get("detail") or "").strip(),
        "severity": f.get("severity") or "medium",
        "due_date": parse_date(f.get("due_date")),
        "assignee_id": f.get("assignee_id", type=int),
        "inspector_id": f.get("inspector_id", type=int),
    }
    if not data["area_id"] or not db.session.get(Area, data["area_id"]):
        errors.append("กรุณาเลือกพื้นที่ / หน่วยงาน")
    if not data["location"]:
        errors.append("กรุณาระบุตำแหน่ง / ชั้น")
    if not data["detail"]:
        errors.append("กรุณากรอกรายละเอียดปัญหา")
    if data["severity"] not in SEVERITY:
        errors.append("ระดับความรุนแรงไม่ถูกต้อง")
    if not data["due_date"]:
        errors.append("กรุณาระบุวันกำหนดแก้ไข")
    elif data["due_date"] < date.today() and (d is None or data["due_date"] != d.due_date):
        errors.append("วันกำหนดแก้ไขต้องไม่ย้อนหลัง")

    if g.user.role == "inspector":
        inspector = g.user
    else:
        inspector = db.session.get(User, data["inspector_id"] or 0)
        if not inspector or inspector.role != "inspector":
            errors.append("กรุณาเลือกผู้ตรวจ")
            inspector = None
    data["inspector"] = inspector
    if inspector is not None:
        allowed = {u.id for u in assignable_users(inspector)}
        if data["assignee_id"] not in allowed:
            errors.append("กรุณาเลือกผู้รับผิดชอบแก้ไข (ต้องเป็นคนที่ Admin จับคู่ไว้กับผู้ตรวจ)")
    return data, errors


def form_context(d=None):
    inspectors = inspectors_list()
    team = {i.id: [{"id": u.id, "name": u.display} for u in assignable_users(i)] for i in inspectors}
    return {
        "areas": Area.query.filter_by(active=True).order_by(Area.name).all(),
        "inspectors": inspectors,
        "team": team,
        "my_team": assignable_users(g.user) if g.user.role == "inspector" else [],
        "default_due": date.today() + timedelta(days=current_app.config["DEFAULT_DUE_DAYS"]),
        "today": date.today(),
    }


@bp.route("/new", methods=["GET", "POST"])
@roles("inspector", "admin")
def new():
    errors = []
    if request.method == "POST":
        data, errors = read_form()
        files = request.files.getlist("photos")
        saved = []
        if not errors:
            saved = save_images(files)
            if not saved:
                errors.append("กรุณาแนบรูปภาพปัญหาอย่างน้อย 1 รูป (ไฟล์รูปภาพเท่านั้น)")
        if not errors:
            d = Defect(code=next_code("DEF", 5), area_id=data["area_id"], location=data["location"],
                       detail=data["detail"], severity=data["severity"], due_date=data["due_date"],
                       assignee_id=data["assignee_id"], inspector_id=data["inspector"].id)
            db.session.add(d)
            for path, thumb in saved:
                d.photos.append(Photo(kind="before", round=0, path=path, thumb_path=thumb,
                                      uploaded_by=g.user.id))
            db.session.add(DefectLog(defect=d, user_id=g.user.id, action="created",
                                     note=f"กำหนดแก้ไข {thdate(d.due_date)}"))
            db.session.flush()
            notify(d.assignee, f"งานใหม่ {d.code}: กำหนดแก้ไข {thdate(d.due_date)}",
                   f"{d.area.name} {d.location}\n{d.detail}\nผู้ตรวจ: {d.inspector.display}",
                   f"/defects/{d.id}", kind="new")
            db.session.commit()
            flash(f"บันทึก {d.code} แล้ว และแจ้งผู้รับผิดชอบเรียบร้อย", "success")
            return redirect(url_for("defects.detail", defect_id=d.id))
    return render_template("defects/form.html", d=None, errors=errors, form=request.form, **form_context())


@bp.route("/<int:defect_id>")
def detail(defect_id):
    d = get_defect_or_404(defect_id)
    return render_template("defects/detail.html", d=d, manage=can_manage(d))


@bp.route("/<int:defect_id>/edit", methods=["GET", "POST"])
@roles("inspector", "admin")
def edit(defect_id):
    d = get_defect_or_404(defect_id)
    if not can_manage(d):
        abort(403)
    if d.status == "closed":
        flash("งานนี้ปิดแล้ว แก้ไขไม่ได้", "error")
        return redirect(url_for("defects.detail", defect_id=d.id))
    errors = []
    if request.method == "POST":
        data, errors = read_form(d)
        if not errors:
            changes = []
            if d.due_date != data["due_date"]:
                changes.append(f"กำหนดแก้ไข {thdate(d.due_date)} → {thdate(data['due_date'])}")
                d.reminded_on = None
            if d.assignee_id != data["assignee_id"]:
                old = d.assignee.username
                new_user = db.session.get(User, data["assignee_id"])
                changes.append(f"ผู้รับผิดชอบ {old} → {new_user.username}")
            for k in ("area_id", "location", "detail", "severity", "due_date", "assignee_id"):
                setattr(d, k, data[k])
            d.inspector_id = data["inspector"].id
            saved = save_images(request.files.getlist("photos"))
            for path, thumb in saved:
                d.photos.append(Photo(kind="before", round=0, path=path, thumb_path=thumb,
                                      uploaded_by=g.user.id))
            if saved:
                changes.append(f"เพิ่มรูปปัญหา {len(saved)} รูป")
            db.session.add(DefectLog(defect=d, user_id=g.user.id, action="edited",
                                     note=", ".join(changes) or "แก้ไขรายละเอียด"))
            db.session.flush()
            db.session.refresh(d)
            if changes:
                notify(d.assignee, f"{d.code} มีการแก้ไขข้อมูล", "\n".join(changes), f"/defects/{d.id}")
            db.session.commit()
            flash("บันทึกการแก้ไขแล้ว", "success")
            return redirect(url_for("defects.detail", defect_id=d.id))
    form = request.form if request.method == "POST" else {
        "area_id": str(d.area_id), "location": d.location, "detail": d.detail, "severity": d.severity,
        "due_date": d.due_date.isoformat(), "assignee_id": str(d.assignee_id),
        "inspector_id": str(d.inspector_id)}
    return render_template("defects/form.html", d=d, errors=errors, form=form, **form_context(d))


@bp.route("/<int:defect_id>/submit", methods=["POST"])
def submit_fix(defect_id):
    d = get_defect_or_404(defect_id)
    if d.assignee_id != g.user.id or d.status not in ("open", "rework"):
        abort(403)
    note = (request.form.get("note") or "").strip()
    saved = save_images(request.files.getlist("photos"))
    if not saved:
        flash("กรุณาแนบรูปภาพหลังแก้ไขอย่างน้อย 1 รูป", "error")
        return redirect(url_for("defects.detail", defect_id=d.id) + "#submit")
    d.round += 1
    for path, thumb in saved:
        d.photos.append(Photo(kind="after", round=d.round, path=path, thumb_path=thumb,
                              uploaded_by=g.user.id))
    d.status = "submitted"
    d.submitted_at = now()
    late = (date.today() - d.due_date).days
    db.session.add(DefectLog(defect=d, user_id=g.user.id, action="submitted",
                             note=(note or "แนบรูปหลังแก้ไขแล้ว") + (f" (ช้ากว่ากำหนด {late} วัน)" if late > 0 else "")))
    notify(d.inspector, f"{d.code} ส่งงานแก้ไขแล้ว รอตรวจรับ",
           f"{d.assignee.display} ส่งรูปหลังแก้ไข {len(saved)} รูป\n{note}", f"/defects/{d.id}", kind="submitted")
    db.session.commit()
    flash("ส่งงานแก้ไขแล้ว ระบบแจ้งผู้ตรวจเรียบร้อย", "success")
    return redirect(url_for("defects.detail", defect_id=d.id))


@bp.route("/<int:defect_id>/review", methods=["POST"])
@roles("inspector", "admin")
def review(defect_id):
    d = get_defect_or_404(defect_id)
    if not can_manage(d) or d.status != "submitted":
        abort(403)
    decision = request.form.get("decision")
    note = (request.form.get("note") or "").strip()
    if decision == "approve":
        d.status = "closed"
        d.closed_at = now()
        if d.ncr and d.ncr.status == "open":
            d.ncr.status = "closed"
            d.ncr.closed_at = now()
        db.session.add(DefectLog(defect=d, user_id=g.user.id, action="approved", note=note))
        notify(d.assignee, f"{d.code} ผ่านการตรวจรับแล้ว", note or "ปิดงานเรียบร้อย", f"/defects/{d.id}", kind="approved")
        flash(f"ตรวจรับ {d.code} และปิดงานแล้ว", "success")
    elif decision == "reject":
        if not note:
            flash("กรุณาระบุเหตุผลที่ตีกลับ", "error")
            return redirect(url_for("defects.detail", defect_id=d.id) + "#review")
        d.status = "rework"
        d.reminded_on = None
        db.session.add(DefectLog(defect=d, user_id=g.user.id, action="rejected", note=note))
        notify(d.assignee, f"{d.code} ถูกตีกลับให้แก้ไขใหม่",
               f"เหตุผล: {note}\nกำหนดแก้ไข {thdate(d.due_date)}", f"/defects/{d.id}", kind="rejected")
        if d.due_date < date.today():
            db.session.flush()
            issue_ncr(d)
        flash(f"ตีกลับ {d.code} แล้ว", "info")
    else:
        abort(400)
    db.session.commit()
    if current_app.config["AI_ENABLED"]:
        try:  # เก็บเคสเข้าคลัง AI — ล้มเหลวก็ไม่กระทบการตรวจรับ
            from . import ai
            ai.enqueue_case(d, d.round, "approved" if decision == "approve" else "rejected", note)
        except Exception:
            db.session.rollback()
            current_app.logger.exception("ai: enqueue case failed")
    return redirect(url_for("defects.detail", defect_id=d.id))


@bp.route("/<int:defect_id>/ai-check", methods=["POST"])
def ai_check(defect_id):
    """ให้ AI ตรวจรูปหลังแก้ (เฉพาะผู้ตรวจของงาน / admin) — เป็นความเห็นประกอบ ไม่เปลี่ยนสถานะงาน"""
    d = get_defect_or_404(defect_id)
    if not current_app.config["AI_ENABLED"]:
        abort(404)
    if not can_manage(d) or d.status != "submitted":
        abort(403)
    from . import ai
    chk = ai.check(d, g.user)
    if chk.status == "error":
        flash(f"AI ตรวจไม่สำเร็จ: {chk.error}", "error")
    return redirect(url_for("defects.detail", defect_id=d.id) + "#ai")


@bp.route("/<int:defect_id>/delete", methods=["POST"])
@roles("admin")
def delete(defect_id):
    d = get_defect_or_404(defect_id)
    code = d.code
    db.session.delete(d)
    db.session.commit()
    flash(f"ลบ {code} แล้ว", "info")
    return redirect(url_for("defects.index"))


@bp.route("/<int:defect_id>/print")
def print_view(defect_id):
    d = get_defect_or_404(defect_id)
    return render_template("defects/print.html", d=d)
