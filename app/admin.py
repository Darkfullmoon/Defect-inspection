from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from .auth import roles
from .models import Area, Defect, User, db, inspector_users
from .utils import EMAIL_RE, ROLES, USERNAME_RE

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_request
@roles("admin")
def guard():
    return None


@bp.route("/users")
def users():
    role = request.args.get("role", "")
    q = User.query.filter(User.role != "admin")
    if role in ("inspector", "user"):
        q = q.filter_by(role=role)
    items = q.order_by(User.role, User.username).all()
    return render_template("admin/users.html", items=items, role=role)


def _validate(form, user=None):
    errors = []
    username = (form.get("username") or "").strip().upper()
    full_name = (form.get("full_name") or "").strip()
    email = (form.get("email") or "").strip()
    role = form.get("role") or ""
    if user is None:
        if not USERNAME_RE.match(username):
            errors.append("ชื่อผู้ใช้ต้องเป็นตัวพิมพ์ใหญ่ภาษาอังกฤษ 3 ตัว เช่น ABC")
        elif username == current_app.config["ADMIN_USERNAME"]:
            errors.append("ชื่อ ADM สงวนไว้สำหรับผู้ดูแลระบบ")
        elif User.query.filter_by(username=username).first():
            errors.append(f"ชื่อผู้ใช้ {username} มีอยู่แล้ว")
    if not full_name:
        errors.append("กรุณากรอกชื่อ-นามสกุล")
    if email and not EMAIL_RE.match(email):
        errors.append("รูปแบบอีเมลไม่ถูกต้อง")
    if role not in ("inspector", "user"):
        errors.append("กรุณาเลือกประเภทผู้ใช้")
    return {"username": username, "full_name": full_name, "email": email, "role": role}, errors


@bp.route("/users/new", methods=["GET", "POST"])
def user_new():
    errors = []
    if request.method == "POST":
        data, errors = _validate(request.form)
        if not errors:
            u = User(username=data["username"], full_name=data["full_name"], email=data["email"],
                     role=data["role"], must_change_password=True,
                     password_hash=generate_password_hash(current_app.config["DEFAULT_PASSWORD"]))
            db.session.add(u)
            db.session.commit()
            flash(f"สร้างผู้ใช้ {u.username} แล้ว — รหัสเริ่มต้น {current_app.config['DEFAULT_PASSWORD']} "
                  "ผู้ใช้จะถูกบังคับให้ตั้งรหัสใหม่เมื่อเข้าครั้งแรก", "success")
            if u.role == "user":
                flash("อย่าลืมจับคู่ผู้ใช้นี้กับผู้ตรวจที่หน้า ‘จับคู่ผู้ตรวจ’", "info")
            return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", u=None, errors=errors, form=request.form, roles=ROLES)


@bp.route("/users/<int:uid>/edit", methods=["GET", "POST"])
def user_edit(uid):
    u = db.session.get(User, uid)
    if not u or u.role == "admin":
        abort(404)
    errors = []
    if request.method == "POST":
        data, errors = _validate(request.form, u)
        if not errors and data["role"] != u.role:
            busy = Defect.query.filter(
                Defect.status != "closed",
                (Defect.assignee_id == u.id) | (Defect.inspector_id == u.id)).count()
            if busy:
                errors.append(f"เปลี่ยนประเภทไม่ได้ เพราะยังมีงานค้าง {busy} รายการ")
        if not errors:
            if data["role"] != u.role:
                u.team = []
                u.inspectors = []
            u.full_name, u.email, u.role = data["full_name"], data["email"], data["role"]
            db.session.commit()
            flash("บันทึกแล้ว", "success")
            return redirect(url_for("admin.users"))
    form = request.form if request.method == "POST" else {
        "username": u.username, "full_name": u.full_name, "email": u.email, "role": u.role}
    return render_template("admin/user_form.html", u=u, errors=errors, form=form, roles=ROLES)


@bp.route("/users/<int:uid>/reset", methods=["POST"])
def user_reset(uid):
    u = db.session.get(User, uid)
    if not u or u.role == "admin":
        abort(404)
    u.password_hash = generate_password_hash(current_app.config["DEFAULT_PASSWORD"])
    u.must_change_password = True
    db.session.commit()
    flash(f"รีเซ็ตรหัสผ่านของ {u.username} เป็นรหัสกลางแล้ว ผู้ใช้ต้องตั้งรหัสใหม่เมื่อเข้าระบบ", "success")
    return redirect(request.referrer or url_for("admin.users"))


@bp.route("/users/<int:uid>/toggle", methods=["POST"])
def user_toggle(uid):
    u = db.session.get(User, uid)
    if not u or u.role == "admin":
        abort(404)
    u.active = not u.active
    db.session.commit()
    flash(f"{'เปิด' if u.active else 'ปิด'}การใช้งาน {u.username} แล้ว", "info")
    return redirect(request.referrer or url_for("admin.users"))


@bp.route("/pairing", methods=["GET", "POST"])
def pairing():
    inspectors = User.query.filter_by(role="inspector").order_by(User.username).all()
    users = User.query.filter_by(role="user").order_by(User.username).all()
    if request.method == "POST":
        pairs = set(request.form.getlist("pair"))  # "inspectorId-userId"
        db.session.execute(inspector_users.delete())
        valid_i = {i.id for i in inspectors}
        valid_u = {u.id for u in users}
        for p in pairs:
            try:
                i, u = map(int, p.split("-"))
            except ValueError:
                continue
            if i in valid_i and u in valid_u:
                db.session.execute(inspector_users.insert().values(inspector_id=i, user_id=u))
        db.session.commit()
        db.session.expire_all()
        flash("บันทึกการจับคู่แล้ว", "success")
        return redirect(url_for("admin.pairing"))
    current = {(i.id, u.id) for i in inspectors for u in i.team}
    return render_template("admin/pairing.html", inspectors=inspectors, users=users, current=current)


@bp.route("/ai", methods=["GET", "POST"])
def ai_cases():
    if not current_app.config["AI_ENABLED"]:
        abort(404)
    from . import ai
    if request.method == "POST" and request.form.get("action") == "backfill":
        n = ai.backfill_cases()
        ai.run_background(ai.process_pending_cases, 1000, current_app.config["AI_BACKFILL_DELAY_SEC"])
        flash(f"เพิ่มเคสเข้าคิว {n} เคส ระบบกำลังประมวลผลเบื้องหลัง", "success")
        return redirect(url_for("admin.ai_cases"))
    return render_template("admin/ai.html", stats=ai.case_stats())


@bp.route("/areas", methods=["GET", "POST"])
def areas():
    if request.method == "POST":
        action = request.form.get("action")
        name = (request.form.get("name") or "").strip()
        if action == "add":
            if not name:
                flash("กรุณากรอกชื่อพื้นที่", "error")
            elif Area.query.filter_by(name=name).first():
                flash("มีชื่อพื้นที่นี้แล้ว", "error")
            else:
                db.session.add(Area(name=name))
                db.session.commit()
                flash(f"เพิ่ม ‘{name}’ แล้ว", "success")
        elif action in ("rename", "toggle"):
            a = db.session.get(Area, request.form.get("id", type=int) or 0)
            if a:
                if action == "rename" and name:
                    if Area.query.filter(Area.name == name, Area.id != a.id).first():
                        flash("มีชื่อพื้นที่นี้แล้ว", "error")
                    else:
                        a.name = name
                elif action == "toggle":
                    a.active = not a.active
                db.session.commit()
        return redirect(url_for("admin.areas"))
    items = Area.query.order_by(Area.name).all()
    usage = dict(db.session.query(Defect.area_id, db.func.count(Defect.id)).group_by(Defect.area_id).all())
    return render_template("admin/areas.html", items=items, usage=usage)
