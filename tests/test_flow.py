"""ทดสอบเส้นทางหลักทั้งหมดด้วย SQLite ชั่วคราว: python -m pytest -q"""
import io
import os
import re
from datetime import date, timedelta

import pytest
from PIL import Image

from app import create_app
from app.jobs import check_overdue
from app.models import NCR, Defect, Notification, User, db


def png():
    buf = io.BytesIO()
    Image.new("RGB", (2400, 1600), (200, 80, 60)).save(buf, "JPEG")
    buf.seek(0)
    return buf


@pytest.fixture()
def app(tmp_path):
    # ตั้ง TEST_DATABASE_URL เพื่อทดสอบกับ MySQL จริง (ฐานข้อมูลแยกสำหรับทดสอบเท่านั้น)
    uri = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 't.db'}"
    app = create_app({
        "SQLALCHEMY_DATABASE_URI": uri,
        "UPLOAD_DIR": str(tmp_path / "up"),
        "RUN_SCHEDULER": False,
        "SMTP_HOST": "",
        "TESTING": True,
    })
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


class Browser:
    def __init__(self, app):
        self.c = app.test_client()

    def token(self):
        self.c.get("/login")
        with self.c.session_transaction() as s:
            return s.get("_csrf")

    def post(self, url, data=None, **kw):
        data = dict(data or {})
        data["csrf_token"] = self.token()
        return self.c.post(url, data=data, **kw)

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def login(self, u, p):
        return self.post("/login", {"username": u, "password": p})


def test_full_flow(app):
    adm = Browser(app)
    # ไม่มี CSRF -> 400
    assert adm.c.post("/login", data={"username": "ADM", "password": "x"}).status_code == 400
    assert adm.login("ADM", "wrong").status_code == 200
    r = adm.login("adm", "Test*123")  # ตัวเล็กก็ได้ ระบบแปลงเป็นตัวใหญ่
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert adm.get("/").status_code == 200

    # สร้างผู้ใช้: ชื่อผิดรูปแบบ / ชื่อสงวน
    r = adm.post("/admin/users/new", {"username": "AB1", "full_name": "x", "role": "user"})
    assert "ตัวพิมพ์ใหญ่ภาษาอังกฤษ 3 ตัว" in r.get_data(as_text=True)
    r = adm.post("/admin/users/new", {"username": "ADM", "full_name": "x", "role": "user"})
    assert "สงวน" in r.get_data(as_text=True)
    assert adm.post("/admin/users/new", {"username": "INS", "full_name": "ผู้ตรวจ หนึ่ง", "email": "ins@x.com", "role": "inspector"}).status_code == 302
    assert adm.post("/admin/users/new", {"username": "con", "full_name": "ช่าง สอง", "email": "con@x.com", "role": "user"}).status_code == 302
    with app.app_context():
        ins = User.query.filter_by(username="INS").one()
        con = User.query.filter_by(username="CON").one()
        ins_id, con_id = ins.id, con.id
    assert adm.post("/admin/pairing", {"pair": f"{ins_id}-{con_id}"}).status_code == 302

    # ผู้ตรวจเข้าครั้งแรก -> ถูกบังคับเปลี่ยนรหัส
    insb = Browser(app)
    r = insb.login("INS", "Test*123")
    assert r.headers["Location"].endswith("/change-password")
    assert insb.get("/").headers["Location"].endswith("/change-password")
    bad = insb.post("/change-password", {"current": "Test*123", "new": "abcdefgh", "confirm": "abcdefgh"})
    body = bad.get_data(as_text=True)
    assert "ตัวพิมพ์ใหญ่" in body and "ตัวเลข" in body and "อักขระพิเศษ" in body
    assert insb.post("/change-password", {"current": "Test*123", "new": "Insp#2026", "confirm": "Insp#2026"}).status_code == 302
    assert insb.get("/").status_code == 200

    conb = Browser(app)
    conb.login("CON", "Test*123")
    conb.post("/change-password", {"current": "Test*123", "new": "Fix!1234", "confirm": "Fix!1234"})

    # ผู้รับผิดชอบเปิด Defect ไม่ได้
    assert conb.get("/defects/new").status_code == 403

    # ผู้ตรวจเปิด Defect (ไม่มีรูป -> error)
    form = {"area_id": "1", "location": "ชั้น 3", "detail": "ผนังร้าว", "severity": "high",
            "due_date": (date.today() + timedelta(days=3)).isoformat(), "assignee_id": str(con_id)}
    r = insb.post("/defects/new", form, content_type="multipart/form-data")
    assert "อย่างน้อย 1 รูป" in r.get_data(as_text=True)
    r = insb.post("/defects/new", {**form, "photos": [(png(), "a.jpg"), (png(), "b.jpg")]},
                  content_type="multipart/form-data")
    assert r.status_code == 302
    did = int(re.search(r"/defects/(\d+)", r.headers["Location"]).group(1))
    with app.app_context():
        d = db.session.get(Defect, did)
        assert d.code.startswith(f"DEF-{date.today().year + 543}-")
        assert len(d.before_photos) == 2
        thumb = d.before_photos[0].thumb_path
        assert Notification.query.filter_by(user_id=con_id).count() == 1

    # สิทธิ์ดูรูป: ผู้เกี่ยวข้องเห็น, คนนอกไม่เห็น
    assert conb.get(f"/media/{thumb}").status_code == 200
    adm.post("/admin/users/new", {"username": "OUT", "full_name": "คนนอก", "role": "user"})
    outb = Browser(app)
    outb.login("OUT", "Test*123")
    outb.post("/change-password", {"current": "Test*123", "new": "Out@1234", "confirm": "Out@1234"})
    assert outb.get(f"/media/{thumb}").status_code == 404
    assert outb.get(f"/defects/{did}").status_code == 404

    # ทุกหน้าเปิดได้ทุกบทบาท
    pages = ["/", "/defects/", f"/defects/{did}", f"/defects/{did}/print", "/tracking", "/ncr/",
             "/reports/", "/stats", "/notifications", "/profile", "/?month=" + date.today().strftime("%Y-%m"),
             "/defects/?status=ncr&q=ผนัง", "/reports/?status=progress&from=2020-01-01&to=2099-01-01"]
    for b in (adm, insb, conb):
        for p in pages:
            assert b.get(p).status_code == 200, p
    for p in ["/admin/users", "/admin/pairing", "/admin/areas", "/defects/new", f"/defects/{did}/edit"]:
        assert adm.get(p).status_code == 200, p
    assert conb.get("/admin/users").status_code == 403

    # ส่งงาน (ไม่มีรูป -> ไม่ผ่าน) แล้วส่งจริง
    conb.post(f"/defects/{did}/submit", {"note": "x"}, content_type="multipart/form-data")
    with app.app_context():
        assert db.session.get(Defect, did).status == "open"
    conb.post(f"/defects/{did}/submit", {"note": "ฉาบใหม่", "photos": [(png(), "c.jpg")]},
              content_type="multipart/form-data")
    with app.app_context():
        assert db.session.get(Defect, did).status == "submitted"

    # ผู้ตรวจตีกลับ (ต้องมีเหตุผล) -> rework
    insb.post(f"/defects/{did}/review", {"decision": "reject"})
    with app.app_context():
        assert db.session.get(Defect, did).status == "submitted"
    insb.post(f"/defects/{did}/review", {"decision": "reject", "note": "สียังไม่เรียบ"})
    with app.app_context():
        d = db.session.get(Defect, did)
        assert d.status == "rework"
        # ทำให้เลยกำหนด แล้วรันตัวตรวจ -> ออก NCR
        d.due_date = date.today() - timedelta(days=2)
        db.session.commit()
        issued, _ = check_overdue()
        assert issued == 1
        d = db.session.get(Defect, did)
        assert d.ncr and d.ncr.code.startswith("NCR-") and d.bucket == "ncr"
        ncr_id = d.ncr.id
        assert Notification.query.filter_by(user_id=ins_id, kind="ncr").count() == 1
        assert check_overdue()[0] == 0  # ไม่ออกซ้ำ
    assert insb.get(f"/ncr/{ncr_id}").status_code == 200
    assert "NCR-" in insb.get("/ncr/").get_data(as_text=True)
    assert "เกินกำหนด (NCR)" in insb.get("/").get_data(as_text=True)

    # ส่งใหม่ -> ตรวจรับ -> ปิดงานและปิด NCR
    conb.post(f"/defects/{did}/submit", {"photos": [(png(), "d.jpg")]}, content_type="multipart/form-data")
    insb.post(f"/defects/{did}/review", {"decision": "approve", "note": "ผ่าน"})
    with app.app_context():
        d = db.session.get(Defect, did)
        assert d.status == "closed" and d.ncr.status == "closed" and d.round == 2
        assert len(d.after_rounds) == 2

    # Admin รีเซ็ตรหัส -> ผู้ใช้ต้องตั้งใหม่ รหัสเก่าใช้ไม่ได้
    adm.post(f"/admin/users/{con_id}/reset")
    c2 = Browser(app)
    assert c2.login("CON", "Fix!1234").status_code == 200
    assert c2.login("CON", "Test*123").headers["Location"].endswith("/change-password")

    # ปิดใช้งานแล้วล็อกอินไม่ได้
    adm.post(f"/admin/users/{con_id}/toggle")
    assert Browser(app).login("CON", "Test*123").status_code == 200

    # ส่งอีเมลรายงานเมื่อยังไม่ตั้ง SMTP -> แจ้งเตือน ไม่พัง
    r = insb.post("/reports/email", {"to": "boss@x.com"}, follow_redirects=True)
    assert "SMTP" in r.get_data(as_text=True)


def test_reminder(app):
    with app.app_context():
        ins = User(username="AAA", full_name="a", role="inspector", must_change_password=False)
        u = User(username="BBB", full_name="b", role="user", must_change_password=False)
        db.session.add_all([ins, u])
        db.session.flush()
        d = Defect(code="DEF-T-1", area_id=1, location="x", detail="y", due_date=date.today() + timedelta(days=1),
                   assignee_id=u.id, inspector_id=ins.id)
        db.session.add(d)
        db.session.commit()
        assert check_overdue() == (0, 1)
        assert check_overdue() == (0, 0)
        assert NCR.query.count() == 0
