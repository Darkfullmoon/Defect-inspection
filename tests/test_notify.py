"""การแจ้งเตือนแสดงบนเว็บ (กระดิ่ง) อย่างเดียว ไม่ส่งอีเมล แม้ตั้ง SMTP และผู้ใช้มีอีเมล"""
import os

import pytest

from app import create_app, notify as notify_mod
from app.models import Notification, User, db


@pytest.fixture()
def app(tmp_path):
    uri = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 't.db'}"
    app = create_app({"SQLALCHEMY_DATABASE_URI": uri, "UPLOAD_DIR": str(tmp_path / "up"),
                      "RUN_SCHEDULER": False, "TESTING": True, "GEMINI_API_KEY": "",
                      "SMTP_HOST": "smtp.example.com"})
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def test_notify_web_only_no_email(app, monkeypatch):
    sent = []
    monkeypatch.setattr(notify_mod, "send_mail", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(notify_mod.smtplib, "SMTP", lambda *a, **k: sent.append("smtp"))
    with app.app_context():
        u = User(username="AAA", full_name="a", email="a@example.com", role="user")
        db.session.add(u)
        db.session.flush()
        notify_mod.notify(u, "งานใหม่", "รายละเอียด", "/defects/1", kind="new")
        db.session.commit()
        n = Notification.query.filter_by(user_id=u.id).one()
        assert n.title == "งานใหม่" and n.link == "/defects/1" and not n.is_read
    assert sent == []
