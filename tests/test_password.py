"""ตั้งรหัสผ่านครั้งแรก / หลังรีเซ็ต ไม่ต้องกรอกรหัสปัจจุบัน — แต่เปลี่ยนรหัสเองจากโปรไฟล์ยังต้องกรอก"""
import os

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from app import create_app
from app.models import User, db


@pytest.fixture()
def app(tmp_path):
    uri = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 't.db'}"
    app = create_app({"SQLALCHEMY_DATABASE_URI": uri, "UPLOAD_DIR": str(tmp_path / "up"),
                      "RUN_SCHEDULER": False, "TESTING": True, "GEMINI_API_KEY": ""})
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def client_for(app, must_change, pw):
    with app.app_context():
        db.session.add(User(username="AAA", full_name="a", role="user", must_change_password=must_change,
                            password_hash=generate_password_hash(pw)))
        db.session.commit()
    c = app.test_client()
    c.get("/login")
    with c.session_transaction() as s:
        tok = s["_csrf"]
    c.post("/login", data={"csrf_token": tok, "username": "AAA", "password": pw})
    with c.session_transaction() as s:  # ล็อกอินแล้วได้ CSRF token ใหม่
        tok = s["_csrf"]
    return c, tok


def pw_ok(app, pw):
    with app.app_context():
        return check_password_hash(User.query.filter_by(username="AAA").one().password_hash, pw)


def test_first_login_no_current_password(app):
    c, tok = client_for(app, True, "Test*123")
    page = c.get("/change-password").get_data(as_text=True)
    assert 'name="current"' not in page and "ตั้งรหัสผ่านใหม่ของคุณ" in page
    r = c.post("/change-password", data={"csrf_token": tok, "new": "Newpw#2026", "confirm": "Newpw#2026"})
    assert r.status_code == 302 and pw_ok(app, "Newpw#2026")
    with app.app_context():
        assert User.query.filter_by(username="AAA").one().must_change_password is False


def test_first_login_still_checks_rules(app):
    c, tok = client_for(app, True, "Test*123")
    r = c.post("/change-password", data={"csrf_token": tok, "new": "abc", "confirm": "abc"})
    assert r.status_code == 200 and "ตัวพิมพ์ใหญ่" in r.get_data(as_text=True)
    assert pw_ok(app, "Test*123")


def test_voluntary_change_still_needs_current(app):
    c, tok = client_for(app, False, "Mine#2026")
    assert 'name="current"' in c.get("/change-password").get_data(as_text=True)
    r = c.post("/change-password", data={"csrf_token": tok, "new": "Other#2026", "confirm": "Other#2026"})
    assert "รหัสผ่านปัจจุบันไม่ถูกต้อง" in r.get_data(as_text=True) and pw_ok(app, "Mine#2026")
    c.post("/change-password", data={"csrf_token": tok, "current": "Mine#2026", "new": "Other#2026",
                                     "confirm": "Other#2026"})
    assert pw_ok(app, "Other#2026")
