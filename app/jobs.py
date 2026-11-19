"""งานเบื้องหลัง: ออก NCR เมื่อเลยกำหนด + เตือนผู้รับผิดชอบก่อนครบกำหนด 1 วัน"""
import logging
from datetime import date, timedelta

from .models import NCR, Defect, DefectLog, db
from .notify import notify
from .utils import next_code, thdate

log = logging.getLogger(__name__)


def issue_ncr(d):
    """ออก NCR ให้ Defect ที่เลยกำหนดและยังไม่ได้ส่งงาน (ถ้ายังไม่มี)"""
    if d.ncr is not None:
        return None
    ncr = NCR(code=next_code("NCR", 4), defect=d, due_date_at_issue=d.due_date)
    db.session.add(ncr)
    late = (date.today() - d.due_date).days
    db.session.add(DefectLog(defect=d, user_id=None, action="ncr",
                             note=f"{ncr.code} — เลยกำหนด {late} วัน (กำหนด {thdate(d.due_date)})"))
    link = f"/defects/{d.id}"
    body = (f"{d.code} · {d.area.name} {d.location}\n{d.detail}\n"
            f"กำหนดแก้ไข {thdate(d.due_date)} (เลยมา {late} วัน) ผู้รับผิดชอบ: {d.assignee.display}")
    notify(d.inspector, f"ออก {ncr.code}: {d.code} เลยกำหนดแก้ไข", body, link, kind="ncr")
    notify(d.assignee, f"คุณได้รับ {ncr.code}: {d.code} เลยกำหนดแก้ไข", body, link, kind="ncr")
    return ncr


def check_overdue():
    today = date.today()
    issued = 0
    late = (Defect.query
            .filter(Defect.status.in_(["open", "rework"]), Defect.due_date < today, ~Defect.ncr.has())
            .all())
    for d in late:
        if issue_ncr(d):
            issued += 1

    tomorrow = today + timedelta(days=1)
    soon = (Defect.query
            .filter(Defect.status.in_(["open", "rework"]), Defect.due_date <= tomorrow,
                    Defect.due_date >= today, Defect.reminded_on.is_(None))
            .all())
    for d in soon:
        d.reminded_on = today
        when = "วันนี้" if d.due_date == today else "พรุ่งนี้"
        notify(d.assignee, f"{d.code} ครบกำหนดแก้ไข{when}",
               f"{d.area.name} {d.location}\n{d.detail}\nกำหนด {thdate(d.due_date)}",
               f"/defects/{d.id}", kind="due")
        db.session.add(DefectLog(defect=d, action="reminded", note=f"ครบกำหนด{when}"))
    db.session.commit()
    if issued or soon:
        log.info("overdue check: %s NCR issued, %s reminders", issued, len(soon))
    return issued, len(soon)


def start_scheduler(app):
    from apscheduler.schedulers.background import BackgroundScheduler

    def run():
        with app.app_context():
            try:
                check_overdue()
            except Exception:
                db.session.rollback()
                log.exception("overdue check failed")
            if app.config.get("AI_ENABLED"):
                try:
                    from .ai import process_pending_cases
                    process_pending_cases()
                except Exception:
                    db.session.rollback()
                    log.exception("ai case sweep failed")

    sched = BackgroundScheduler(daemon=True)
    sched.add_job(run, "interval", minutes=app.config["CHECK_INTERVAL_MINUTES"],
                  id="overdue", max_instances=1, coalesce=True)
    sched.start()
    # รันรอบแรกหลังเปิดระบบ 5 วินาที
    from datetime import datetime
    sched.add_job(run, "date", run_date=datetime.now() + timedelta(seconds=5))
    app.extensions["scheduler"] = sched
    return sched
