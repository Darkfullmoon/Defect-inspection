import logging
import os
import secrets
import time

from flask import Flask, g, render_template
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from .config import Config
from .models import Area, Notification, User, db
from . import utils

try:  # รองรับรูป HEIC จาก iPhone ถ้าติดตั้ง pillow-heif
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:  # pragma: no cover
    pass

log = logging.getLogger(__name__)


def _wait_for_db(app, tries=30):
    for i in range(tries):
        try:
            with db.engine.connect() as c:
                c.execute(text("SELECT 1"))
            return
        except Exception as e:  # MySQL ใน Docker อาจยังไม่พร้อม
            log.warning("database not ready (%s/%s): %s", i + 1, tries, e)
            time.sleep(2)
    raise RuntimeError("cannot connect to database")


def _seed(app):
    adm_name = app.config["ADMIN_USERNAME"]
    adm = User.query.filter_by(username=adm_name).first()
    if adm is None:
        db.session.add(User(username=adm_name, full_name="ผู้ดูแลระบบ", role="admin",
                            must_change_password=False,
                            password_hash=generate_password_hash(secrets.token_urlsafe(24))))
    if Area.query.count() == 0:
        for n in ("อาคาร A", "อาคาร B", "อาคาร C", "พื้นที่ส่วนกลาง"):
            db.session.add(Area(name=n))
    db.session.commit()


def create_app(config=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    app.config["AI_ENABLED"] = bool((app.config.get("GEMINI_API_KEY") or "").strip())
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    db.init_app(app)

    from . import admin, auth, defects, main, ncr, reports
    for m in (auth, main, defects, ncr, reports, admin):
        app.register_blueprint(m.bp)

    app.add_template_filter(utils.thdate, "thdate")
    app.add_template_filter(utils.thdatetime, "thdatetime")
    app.jinja_env.globals.update(
        csrf_token=utils.csrf_token, STATUS=utils.STATUS, BUCKETS=utils.BUCKETS,
        SEVERITY=utils.SEVERITY, ROLES=utils.ROLES, LOG_ACTIONS=utils.LOG_ACTIONS,
        AI_ENABLED=app.config["AI_ENABLED"], AI_VERDICT=utils.AI_VERDICT,
    )

    @app.context_processor
    def inject_notifications():
        if getattr(g, "user", None) is None:
            return {}
        q = Notification.query.filter_by(user_id=g.user.id)
        return {
            "unread_count": q.filter_by(is_read=False).count(),
            "recent_notifications": q.order_by(Notification.created_at.desc()).limit(6).all(),
        }

    @app.route("/health")
    def health():
        return {"ok": True}

    for code, msg in ((400, "คำขอไม่ถูกต้อง"), (403, "คุณไม่มีสิทธิ์เข้าถึงหน้านี้"),
                      (404, "ไม่พบหน้าที่ต้องการ"), (413, "ไฟล์ใหญ่เกินไป (รวมไม่เกิน 80MB ต่อครั้ง)")):
        def handler(e, code=code, msg=msg):
            detail = getattr(e, "description", "") if code == 400 else ""
            return render_template("error.html", code=code, msg=msg, detail=detail), code
        app.register_error_handler(code, handler)

    with app.app_context():
        _wait_for_db(app)
        db.create_all()
        _seed(app)

    if app.config["RUN_SCHEDULER"]:
        from .jobs import start_scheduler
        start_scheduler(app)
    return app
