from collections import Counter as Tally

from flask import (Blueprint, current_app, flash, g, redirect, render_template, request,
                   url_for)

from .defects import filtered_query
from .models import Area, Defect, User, db, now
from .notify import send_mail
from .utils import EMAIL_RE, thdatetime

bp = Blueprint("reports", __name__, url_prefix="/reports")

FILTER_KEYS = ("q", "status", "area", "severity", "person", "from", "to")


def build(args):
    items = filtered_query(args).order_by(Defect.created_at.desc()).all()
    counts = Tally(d.bucket for d in items)
    return items, counts


def filter_summary(args):
    from .utils import BUCKETS, SEVERITY, STATUS, thdate, parse_date
    parts = []
    if args.get("from") or args.get("to"):
        parts.append(f"วันที่บันทึก {thdate(parse_date(args.get('from')))} ถึง {thdate(parse_date(args.get('to')))}")
    st = args.get("status")
    if st:
        parts.append("สถานะ: " + (STATUS.get(st) or BUCKETS.get(st) or st))
    if args.get("area", type=int):
        a = db.session.get(Area, args.get("area", type=int))
        if a:
            parts.append("พื้นที่: " + a.name)
    if args.get("severity") in SEVERITY:
        parts.append("ความรุนแรง: " + SEVERITY[args["severity"]])
    if args.get("person", type=int):
        u = db.session.get(User, args.get("person", type=int))
        if u:
            parts.append("ผู้เกี่ยวข้อง: " + u.display)
    if args.get("q"):
        parts.append(f"ค้นหา: “{args['q']}”")
    return " · ".join(parts) or "ทั้งหมด"


@bp.route("/")
def index():
    items, counts = build(request.args)
    people = User.query.filter(User.role != "admin").order_by(User.username).all()
    return render_template("reports/index.html", items=items, counts=counts,
                           areas=Area.query.order_by(Area.name).all(), people=people,
                           summary=filter_summary(request.args),
                           print_mode=request.args.get("print") == "1",
                           default_email=g.user.email, now_text=thdatetime(now()))


@bp.route("/email", methods=["POST"])
def email():
    to = (request.form.get("to") or "").strip()
    args = request.form
    back = url_for("reports.index", **{k: args.get(k) for k in FILTER_KEYS if args.get(k)})
    recipients = [t.strip() for t in to.replace(";", ",").split(",") if t.strip()]
    if not recipients or not all(EMAIL_RE.match(r) for r in recipients):
        flash("กรุณากรอกอีเมลผู้รับให้ถูกต้อง (หลายคนคั่นด้วย ,)", "error")
        return redirect(back)
    items, counts = build(args)
    html = render_template("email/report.html", items=items, counts=counts, summary=filter_summary(args),
                           base=current_app.config["APP_BASE_URL"], sender=g.user)
    if not current_app.config["SMTP_HOST"]:
        flash("ยังไม่ได้ตั้งค่า SMTP ใน .env จึงส่งอีเมลไม่ได้", "error")
        return redirect(back)
    for r in recipients:
        send_mail(r, f"[Defect] รายงาน Defect {len(items)} รายการ", html)
    flash(f"ส่งรายงานไปที่ {', '.join(recipients)} แล้ว", "success")
    return redirect(back)
