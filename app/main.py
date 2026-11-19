import os
from collections import Counter as Tally
from collections import defaultdict
from datetime import date, timedelta

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   send_from_directory, url_for)

from .models import NCR, Defect, Notification, Photo, User, db
from .utils import (BUCKETS, EMAIL_RE, SEVERITY, TH_MONTHS, TH_MONTHS_FULL, can_view,
                    defect_scope)

bp = Blueprint("main", __name__)


def month_shift(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def month_label(d):
    return f"{TH_MONTHS[d.month]} {str(d.year + 543)[-2:]}"


@bp.route("/")
def dashboard():
    month = request.args.get("month", "")  # YYYY-MM หรือว่าง = ทั้งหมด
    q = defect_scope()
    month_title = "ทั้งหมด"
    if month:
        try:
            y, m = map(int, month.split("-"))
            start = date(y, m, 1)
            q = q.filter(Defect.created_at >= start, Defect.created_at < month_shift(start, 1))
            month_title = f"{TH_MONTHS_FULL[m]} {y + 543}"
        except ValueError:
            month = ""
    defects = q.all()
    counts = Tally(d.bucket for d in defects)
    total = len(defects)

    today = date.today()
    all_scoped = defect_scope().all()
    active = [d for d in all_scoped if d.active]
    alerts = {
        "overdue": sum(1 for d in active if d.has_open_ncr),
        "due_today": sum(1 for d in active if d.due_date == today and d.status != "submitted"),
        "due_soon": sum(1 for d in active if 0 < d.days_left <= 3 and d.status != "submitted"),
        "waiting": sum(1 for d in active if d.status == "submitted"),
        "rework": sum(1 for d in active if d.status == "rework"),
    }

    # กราฟรายเดือน 6 เดือนล่าสุด
    first = month_shift(today.replace(day=1), -5)
    months = [month_shift(first, i) for i in range(6)]
    created = Tally((d.created_at.year, d.created_at.month) for d in all_scoped)
    closed = Tally((d.closed_at.year, d.closed_at.month) for d in all_scoped if d.closed_at)
    monthly = {
        "labels": [month_label(m) for m in months],
        "created": [created[(m.year, m.month)] for m in months],
        "closed": [closed[(m.year, m.month)] for m in months],
    }

    latest = defect_scope().order_by(Defect.created_at.desc()).limit(6).all()
    month_options = [month_shift(today.replace(day=1), -i) for i in range(12)]
    return render_template(
        "main/dashboard.html", total=total, counts=counts, alerts=alerts, monthly=monthly,
        latest=latest, month=month, month_title=month_title,
        month_options=[(f"{m:%Y-%m}", f"{TH_MONTHS_FULL[m.month]} {m.year + 543}") for m in month_options],
        donut={"labels": [BUCKETS["closed"], BUCKETS["progress"], BUCKETS["ncr"]],
               "series": [counts["closed"], counts["progress"], counts["ncr"]]},
    )


@bp.route("/tracking")
def tracking():
    today = date.today()
    active = (defect_scope().filter(Defect.status != "closed")
              .order_by(Defect.due_date, Defect.id).all())
    groups = [
        ("ncr", "เกินกำหนด / ออก NCR แล้ว", [d for d in active if d.has_open_ncr and d.status != "submitted"]),
        ("today", "ครบกำหนดวันนี้", [d for d in active if not d.has_open_ncr and d.status != "submitted" and d.due_date <= today]),
        ("soon", "ครบกำหนดภายใน 3 วัน", [d for d in active if d.status != "submitted" and 0 < d.days_left <= 3 and not d.has_open_ncr]),
        ("later", "ยังมีเวลา", [d for d in active if d.status != "submitted" and d.days_left > 3 and not d.has_open_ncr]),
        ("waiting", "ส่งงานแล้ว รอผู้ตรวจตรวจรับ", [d for d in active if d.status == "submitted"]),
    ]
    return render_template("main/tracking.html", groups=groups)


@bp.route("/stats")
def stats():
    defects = defect_scope().all()
    today = date.today()
    by_area = defaultdict(lambda: [0, 0])  # [ทั้งหมด, ปิดแล้ว]
    by_person = defaultdict(lambda: {"closed": 0, "progress": 0, "ncr": 0})
    sev = Tally()
    fix_days = []
    on_time = late = 0
    for d in defects:
        by_area[d.area.name][0] += 1
        if d.status == "closed":
            by_area[d.area.name][1] += 1
            fix_days.append((d.closed_at.date() - d.created_at.date()).days)
            if d.ncr is None:
                on_time += 1
            else:
                late += 1
        by_person[d.assignee.username][d.bucket] += 1
        sev[d.severity] += 1
    ncr_total = sum(1 for d in defects if d.ncr is not None)

    first = month_shift(today.replace(day=1), -11)
    months = [month_shift(first, i) for i in range(12)]
    created = Tally((d.created_at.year, d.created_at.month) for d in defects)
    closed = Tally((d.closed_at.year, d.closed_at.month) for d in defects if d.closed_at)
    ncrs = Tally((d.ncr.issued_at.year, d.ncr.issued_at.month) for d in defects if d.ncr)

    areas_sorted = sorted(by_area.items(), key=lambda x: -x[1][0])
    people = sorted(by_person.items(), key=lambda x: -sum(x[1].values()))
    data = {
        "trend": {"labels": [month_label(m) for m in months],
                  "created": [created[(m.year, m.month)] for m in months],
                  "closed": [closed[(m.year, m.month)] for m in months],
                  "ncr": [ncrs[(m.year, m.month)] for m in months]},
        "area": {"labels": [a for a, _ in areas_sorted],
                 "total": [v[0] for _, v in areas_sorted],
                 "closed": [v[1] for _, v in areas_sorted]},
        "severity": {"labels": [SEVERITY[k] for k in ("low", "medium", "high")],
                     "series": [sev[k] for k in ("low", "medium", "high")]},
        "people": {"labels": [p for p, _ in people],
                   "closed": [v["closed"] for _, v in people],
                   "progress": [v["progress"] for _, v in people],
                   "ncr": [v["ncr"] for _, v in people]},
    }
    kpi = {
        "total": len(defects),
        "closed": sum(1 for d in defects if d.status == "closed"),
        "avg_days": round(sum(fix_days) / len(fix_days), 1) if fix_days else 0,
        "on_time_rate": round(on_time * 100 / (on_time + late)) if (on_time + late) else 0,
        "ncr_total": ncr_total,
        "ncr_rate": round(ncr_total * 100 / len(defects)) if defects else 0,
    }
    return render_template("main/stats.html", data=data, kpi=kpi)


@bp.route("/notifications")
def notifications():
    items = (Notification.query.filter_by(user_id=g.user.id)
             .order_by(Notification.created_at.desc()).limit(100).all())
    return render_template("main/notifications.html", items=items)


@bp.route("/notifications/<int:nid>/open")
def notification_open(nid):
    n = db.session.get(Notification, nid)
    if not n or n.user_id != g.user.id:
        abort(404)
    n.is_read = True
    db.session.commit()
    return redirect(n.link or url_for("main.notifications"))


@bp.route("/notifications/read-all", methods=["POST"])
def notifications_read_all():
    Notification.query.filter_by(user_id=g.user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return redirect(request.referrer or url_for("main.notifications"))


@bp.route("/profile", methods=["GET", "POST"])
def profile():
    u = g.user
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        if email and not EMAIL_RE.match(email):
            flash("รูปแบบอีเมลไม่ถูกต้อง", "error")
        else:
            u.email = email
            db.session.commit()
            flash("บันทึกข้อมูลแล้ว", "success")
        return redirect(url_for("main.profile"))
    return render_template("main/profile.html")


@bp.route("/media/<path:rel>")
def media(rel):
    p = Photo.query.filter((Photo.path == rel) | (Photo.thumb_path == rel)).first()
    if p is None or not can_view(p.defect):
        abort(404)
    resp = send_from_directory(os.path.abspath(current_app.config["UPLOAD_DIR"]), rel, max_age=86400 * 7)
    resp.headers["Cache-Control"] = "private, max-age=604800"
    return resp
