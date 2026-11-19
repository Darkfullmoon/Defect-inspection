"""เอกสาร PDF (Defect / NCR / รายงาน) ใช้ฟอนต์ TH SarabunPSK ที่แนบมากับโปรแกรม"""
import io
import os
import re
from datetime import date, timedelta

import pytest
from PIL import Image
from werkzeug.security import generate_password_hash

from app import create_app
from app.jobs import issue_ncr
from app.models import Defect, User, db

PW = "Pass#1234"


@pytest.fixture()
def app(tmp_path):
    uri = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 't.db'}"
    app = create_app({"SQLALCHEMY_DATABASE_URI": uri, "UPLOAD_DIR": str(tmp_path / "up"),
                      "RUN_SCHEDULER": False, "TESTING": True, "GEMINI_API_KEY": ""})
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def test_fonts_served(app):
    c = app.test_client()
    for name in ("THSarabunPSK.ttf", "THSarabunPSK-Bold.ttf"):
        r = c.get(f"/static/fonts/{name}")
        assert r.status_code == 200 and len(r.data) > 50_000, name
        assert r.data[:4] == b"\x00\x01\x00\x00"  # TrueType


def test_print_pages_use_th_sarabun(app):
    with app.app_context():
        ins = User(username="INS", role="inspector", must_change_password=False,
                   password_hash=generate_password_hash(PW))
        con = User(username="CON", role="user", must_change_password=False,
                   password_hash=generate_password_hash(PW))
        ins.team.append(con)
        db.session.add_all([ins, con])
        db.session.commit()
        con_id = con.id
    c = app.test_client()
    c.get("/login")
    with c.session_transaction() as s:
        tok = s["_csrf"]
    c.post("/login", data={"csrf_token": tok, "username": "INS", "password": PW})
    with c.session_transaction() as s:
        tok = s["_csrf"]
    buf = io.BytesIO()
    Image.new("RGB", (100, 100)).save(buf, "JPEG")
    buf.seek(0)
    r = c.post("/defects/new", data={"csrf_token": tok, "area_id": "1", "location": "ชั้น ๓", "detail": "ผนังร้าว",
                                     "severity": "low", "assignee_id": str(con_id),
                                     "due_date": (date.today() + timedelta(days=2)).isoformat(),
                                     "photos": [(buf, "a.jpg")]}, content_type="multipart/form-data")
    did = int(re.search(r"/defects/(\d+)", r.headers["Location"]).group(1))
    with app.app_context():
        d = db.session.get(Defect, did)
        d.due_date = date.today() - timedelta(days=1)
        ncr = issue_ncr(d)
        db.session.commit()
        ncr_id = ncr.id
    for p in (f"/defects/{did}/print", f"/ncr/{ncr_id}", "/reports/"):
        body = c.get(p).get_data(as_text=True)
        assert "/static/fonts/THSarabunPSK.ttf" in body, p
        assert re.search(r'font-family:\s*"THSarabunPSK"', body), p
        assert "fonts.googleapis.com/css2?family=Sarabun" not in body, p
        # สัญลักษณ์ที่ TH SarabunPSK ไม่มี จะไปใช้ฟอนต์อื่นของเครื่อง (หน้าตาต่างกันแต่ละเครื่อง)
        assert "☐" not in body, p
