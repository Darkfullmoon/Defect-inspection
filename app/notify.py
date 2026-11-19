"""การแจ้งเตือนบนเว็บ (กระดิ่ง) — ระบบไม่มีการส่งอีเมล"""
from .models import Notification, db


def notify(user, title, body="", link="", kind="info"):
    """สร้างการแจ้งเตือนในกระดิ่ง (ต้องอยู่ใน app context)"""
    if user is None:
        return
    db.session.add(Notification(user_id=user.id, kind=kind, title=title, body=body, link=link))
