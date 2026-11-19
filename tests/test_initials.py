"""แสดงเฉพาะชื่อย่อ (username 3 ตัว) — ไม่ใช้ชื่อ-นามสกุลแล้ว"""
import io
import os
import re
from datetime import date, timedelta

import pytest
from PIL import Image
from werkzeug.security import generate_password_hash

from app import create_app
from app.models import User, db

PW = "Pass#1234"
FULL = "สมชาย ใจดีมาก"


@pytest.fixture()
def app(tmp_path):
    uri = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 't.db'}"
    app = create_app({"SQLALCHEMY_DATABASE_URI": uri, "UPLOAD_DIR": str(tmp_path / "up"),
                      "RUN_SCHEDULER": False, "TESTING": True, "GEMINI_API_KEY": ""})
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def login(app, name, pw):
    c = app.test_client()
    c.get("/login")
    with c.session_transaction() as s:
        tok = s["_csrf"]
    c.post("/login", data={"csrf_token": tok, "username": name, "password": pw})
    with c.session_transaction() as s:
        tok = s["_csrf"]
    return c, tok


def test_only_initials_shown(app):
    with app.app_context():
        ins = User(username="INS", full_name=FULL, role="inspector", must_change_password=False,
                   password_hash=generate_password_hash(PW))
        con = User(username="CON", full_name=FULL, role="user", must_change_password=False,
                   password_hash=generate_password_hash(PW))
        ins.team.append(con)
        db.session.add_all([ins, con])
        db.session.commit()
        assert con.display == "CON"
        con_id = con.id
    c, tok = login(app, "INS", PW)
    buf = io.BytesIO()
    Image.new("RGB", (100, 100)).save(buf, "JPEG")
    buf.seek(0)
    r = c.post("/defects/new", data={"csrf_token": tok, "area_id": "1", "location": "x", "detail": "y",
                                     "severity": "low", "assignee_id": str(con_id),
                                     "due_date": (date.today() + timedelta(days=2)).isoformat(),
                                     "photos": [(buf, "a.jpg")]}, content_type="multipart/form-data")
    did = re.search(r"/defects/(\d+)", r.headers["Location"]).group(1)
    adm, _ = login(app, "ADM", "Test*123")
    for client, pages in ((c, ["/", "/defects/", f"/defects/{did}", f"/defects/{did}/print", "/profile",
                               "/tracking", "/reports/", "/ncr/"]),
                          (adm, ["/admin/users", "/admin/pairing", f"/admin/users/{con_id}/edit"])):
        for p in pages:
            body = client.get(p).get_data(as_text=True)
            assert FULL not in body, p
    assert 'name="full_name"' not in c.get("/profile").get_data(as_text=True)
    assert 'name="full_name"' not in adm.get("/admin/users/new").get_data(as_text=True)


def test_create_user_without_full_name(app):
    adm, tok = login(app, "ADM", "Test*123")
    r = adm.post("/admin/users/new", data={"csrf_token": tok, "username": "NEW", "role": "user"})
    assert r.status_code == 302
    with app.app_context():
        assert User.query.filter_by(username="NEW").one().full_name == ""
