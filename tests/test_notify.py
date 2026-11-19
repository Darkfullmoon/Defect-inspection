"""การแจ้งเตือนแสดงบนเว็บ (กระดิ่ง) อย่างเดียว — ไม่มีระบบอีเมล (SMTP) ในโปรแกรมแล้ว"""
import os

import pytest

from app import create_app, notify as notify_mod
from app.models import Notification, User, db


@pytest.fixture()
def app(tmp_path):
    uri = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 't.db'}"
    app = create_app({"SQLALCHEMY_DATABASE_URI": uri, "UPLOAD_DIR": str(tmp_path / "up"),
                      "RUN_SCHEDULER": False, "TESTING": True, "GEMINI_API_KEY": ""})
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def test_notify_creates_web_notification(app):
    with app.app_context():
        u = User(username="AAA", full_name="a", email="a@example.com", role="user")
        db.session.add(u)
        db.session.flush()
        notify_mod.notify(u, "งานใหม่", "รายละเอียด", "/defects/1", kind="new")
        db.session.commit()
        n = Notification.query.filter_by(user_id=u.id).one()
        assert n.title == "งานใหม่" and n.link == "/defects/1" and not n.is_read


def test_no_smtp_left(app):
    assert not hasattr(notify_mod, "send_mail") and not hasattr(notify_mod, "smtplib")
    assert not any(k.startswith("SMTP_") for k in app.config)
