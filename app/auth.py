import hmac
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .models import User, db, now
from .utils import csrf_token, password_problems

bp = Blueprint("auth", __name__)

OPEN_ENDPOINTS = {"auth.login", "static", "health"}


@bp.before_app_request
def load_user():
    g.user = None
    uid = session.get("uid")
    if uid:
        u = db.session.get(User, uid)
        if u and u.active:
            g.user = u
        else:
            session.clear()

    # CSRF สำหรับทุกฟอร์ม POST
    if request.method == "POST":
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not sent or not hmac.compare_digest(sent, session.get("_csrf", "")):
            abort(400, "CSRF token ไม่ถูกต้อง กรุณารีเฟรชหน้าแล้วลองใหม่")

    ep = request.endpoint or ""
    if ep in OPEN_ENDPOINTS:
        return None
    if g.user is None:
        return redirect(url_for("auth.login", next=request.full_path if request.method == "GET" else None))
    if g.user.must_change_password and ep not in ("auth.change_password", "auth.logout"):
        return redirect(url_for("auth.change_password"))
    return None


def roles(*allowed):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if g.user is None or g.user.role not in allowed:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    error = None
    username = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().upper()
        password = request.form.get("password") or ""
        cfg = current_app.config
        user = User.query.filter_by(username=username).first()
        ok = False
        if user and user.active:
            if user.role == "admin" and username == cfg["ADMIN_USERNAME"]:
                ok = hmac.compare_digest(password.encode(), cfg["ADMIN_PASSWORD"].encode())
            elif user.password_hash:
                ok = check_password_hash(user.password_hash, password)
        if ok:
            session.clear()
            session.permanent = True
            session["uid"] = user.id
            csrf_token()
            user.last_login_at = now()
            db.session.commit()
            if user.must_change_password:
                flash("เข้าสู่ระบบครั้งแรกด้วยรหัสกลาง กรุณาตั้งรหัสผ่านใหม่ของคุณ", "info")
                return redirect(url_for("auth.change_password"))
            nxt = request.args.get("next") or ""
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = url_for("main.dashboard")
            return redirect(nxt)
        error = "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"
    return render_template("auth/login.html", error=error, username=username, bare=True)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("ออกจากระบบแล้ว", "info")
    return redirect(url_for("auth.login"))


@bp.route("/change-password", methods=["GET", "POST"])
def change_password():
    u = g.user
    if u.role == "admin":
        flash("รหัสผ่านของ ADM กำหนดในไฟล์ .env (ADMIN_PASSWORD)", "info")
        return redirect(url_for("main.profile"))
    errors = []
    if request.method == "POST":
        current = request.form.get("current") or ""
        new = request.form.get("new") or ""
        confirm = request.form.get("confirm") or ""
        # ตั้งรหัสครั้งแรก / หลังรีเซ็ต: เพิ่งล็อกอินด้วยรหัสกลางมา ไม่ต้องกรอกซ้ำ
        # เปลี่ยนรหัสเองจากโปรไฟล์: ยังต้องกรอกรหัสปัจจุบัน กันคนอื่นมาเปลี่ยนตอนลืมล็อกเอาต์
        if not u.must_change_password and not check_password_hash(u.password_hash, current):
            errors.append("รหัสผ่านปัจจุบันไม่ถูกต้อง")
        if new != confirm:
            errors.append("รหัสผ่านใหม่ทั้งสองช่องไม่ตรงกัน")
        errors += ["รหัสผ่านใหม่ต้อง" + p for p in password_problems(new)]
        if not errors:
            u.password_hash = generate_password_hash(new)
            u.must_change_password = False
            db.session.commit()
            flash("ตั้งรหัสผ่านใหม่เรียบร้อย", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("auth/change_password.html", errors=errors, first=u.must_change_password, bare=u.must_change_password)
