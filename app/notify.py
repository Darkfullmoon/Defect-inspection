"""การแจ้งเตือนบนเว็บ (กระดิ่ง) + ส่งอีเมลรายงาน (ใช้เฉพาะเมนูส่งรายงาน ตาม SMTP ใน .env)"""
import logging
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr

from flask import current_app

from .models import Notification, db

log = logging.getLogger(__name__)


def _smtp_settings():
    c = current_app.config
    return {k: c[k] for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TLS")}


def _send(cfg, to, subject, html):
    if not cfg["SMTP_HOST"]:
        log.info("SMTP not configured; skip email to %s: %s", to, subject)
        return False
    msg = MIMEMultipart("alternative")
    name, addr = parseaddr(cfg["SMTP_FROM"])
    msg["From"] = formataddr((name, addr))
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=20) as s:
            if cfg["SMTP_TLS"]:
                s.starttls()
            if cfg["SMTP_USER"]:
                s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            s.sendmail(addr, [to], msg.as_string())
        return True
    except Exception:
        log.exception("send mail failed to %s", to)
        return False


def send_mail(to, subject, html, wait=False):
    if not to:
        return False
    cfg = _smtp_settings()
    if wait:
        return _send(cfg, to, subject, html)
    threading.Thread(target=_send, args=(cfg, to, subject, html), daemon=True).start()
    return True


def notify(user, title, body="", link="", kind="info"):
    """สร้างการแจ้งเตือนบนเว็บ (กระดิ่ง) — ไม่ส่งอีเมล (ต้องอยู่ใน app context)"""
    if user is None:
        return
    db.session.add(Notification(user_id=user.id, kind=kind, title=title, body=body, link=link))
