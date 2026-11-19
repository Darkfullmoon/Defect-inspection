"""ทดสอบ AI ตรวจรูป (Gemini ถูกแทนด้วยตัวจำลอง ไม่เรียก API จริง): python -m pytest -q"""
import io
import json
import os
import re
from datetime import date, timedelta

import pytest
from PIL import Image
from werkzeug.security import generate_password_hash

from app import create_app
from app.models import db

PW = "Pass#1234"


def jpg(color=(200, 80, 60)):
    buf = io.BytesIO()
    Image.new("RGB", (1200, 800), color).save(buf, "JPEG")
    buf.seek(0)
    return buf


def make_app(tmp_path, key=""):
    uri = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 't.db'}"
    return create_app({
        "SQLALCHEMY_DATABASE_URI": uri,
        "UPLOAD_DIR": str(tmp_path / "up"),
        "RUN_SCHEDULER": False,
        "SMTP_HOST": "",
        "TESTING": True,
        "GEMINI_API_KEY": key,
        "AI_INLINE": True,
    })


@pytest.fixture()
def app_off(tmp_path):
    app = make_app(tmp_path, key="")
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def app_on(tmp_path):
    app = make_app(tmp_path, key="test-key")
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def test_ai_disabled_without_key(app_off):
    assert app_off.config["AI_ENABLED"] is False
    with app_off.app_context():
        tables = db.inspect(db.engine).get_table_names()
    assert "ai_cases" in tables and "ai_checks" in tables


def test_ai_enabled_with_key(app_on):
    assert app_on.config["AI_ENABLED"] is True


# ---------- ตัวช่วยสร้างข้อมูล ----------
from fake_gemini import DIM, FakeGemini  # noqa: E402
from app.models import AICase, AICheck, Defect, DefectLog, User  # noqa: E402


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


def users(app):
    """สร้างผู้ตรวจ INS ผู้รับผิดชอบ CON (จับคู่แล้ว) คืน (browser ผู้ตรวจ, browser ผู้รับผิดชอบ, browser admin)"""
    with app.app_context():
        if not User.query.filter_by(username="INS").first():
            ins = User(username="INS", full_name="ผู้ตรวจ", role="inspector", must_change_password=False,
                       password_hash=generate_password_hash(PW))
            con = User(username="CON", full_name="ช่าง", role="user", must_change_password=False,
                       password_hash=generate_password_hash(PW))
            ins.team.append(con)
            db.session.add_all([ins, con])
            db.session.commit()
    out = []
    for name, pw in (("INS", PW), ("CON", PW), ("ADM", "Test*123")):
        b = Browser(app)
        b.post("/login", {"username": name, "password": pw})
        out.append(b)
    return out


def con_id(app):
    with app.app_context():
        return User.query.filter_by(username="CON").one().id


def new_defect(app, insb, conb, detail="ผนังร้าว", submit=True):
    form = {"area_id": "1", "location": "ชั้น 3", "detail": detail, "severity": "high",
            "due_date": (date.today() + timedelta(days=3)).isoformat(), "assignee_id": str(con_id(app)),
            "photos": [(jpg(), "a.jpg")]}
    r = insb.post("/defects/new", form, content_type="multipart/form-data")
    assert r.status_code == 302, r.get_data(as_text=True)[:500]
    did = int(re.search(r"/defects/(\d+)", r.headers["Location"]).group(1))
    if submit:
        submit_fix(conb, did)
    return did


def submit_fix(conb, did, note="ฉาบใหม่"):
    r = conb.post(f"/defects/{did}/submit", {"note": note, "photos": [(jpg((90, 90, 90)), "b.jpg")]},
                  content_type="multipart/form-data")
    assert r.status_code == 302


def unit(i):
    v = [0.0] * DIM
    v[i] = 1.0
    return v


# ---------- Task 2: wrapper, คลังเคส, การค้น ----------
from app import ai  # noqa: E402


def test_cosine():
    assert ai.cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert ai.cosine([1, 0], [0, 1]) == 0.0
    assert ai.cosine([0, 0], [1, 1]) == 0.0
    assert ai.cosine([1], [1, 1]) == 0.0


def test_process_case_done_and_error(app_on):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    fake = FakeGemini(reply="ผนังยังมีรอยร้าว")
    app_on.extensions["gemini_client"] = fake
    with app_on.app_context():
        c = AICase(defect_id=did, round=1, verdict="rejected", inspector_note="ยังร้าว")
        db.session.add(c)
        db.session.commit()
        case = ai.process_case(c.id)
        assert case.status == "done"
        assert case.photo_summary == "ผนังยังมีรอยร้าว"
        assert "ตีกลับ" in case.doc_text and "ยังร้าว" in case.doc_text
        assert len(json.loads(case.embedding)) == DIM
        assert case.embed_model == "gemini-embedding-001"
        # ส่งรูปก่อนแก้ + หลังแก้ไปด้วย
        gen = [c for c in fake.calls if c[0] == "generate"][0]
        assert sum(1 for p in gen[2] if not isinstance(p, str)) == 2

        app_on.extensions["gemini_client"] = FakeGemini(fail=True)
        case.status = "pending"
        db.session.commit()
        case = ai.process_case(case.id)
        assert case.status == "error" and "gemini down" in case.error


def _done_case(defect_id, rnd, vec, model="gemini-embedding-001"):
    return AICase(defect_id=defect_id, round=rnd, verdict="rejected", status="done",
                  embedding=json.dumps(vec), embed_model=model, doc_text=f"case {defect_id}-{rnd}")


def test_retrieve_orders_and_filters(app_on):
    insb, conb, _ = users(app_on)
    target = new_defect(app_on, insb, conb)
    others = [new_defect(app_on, insb, conb, submit=False) for _ in range(4)]
    near = [0.9, 0.1] + [0.0] * (DIM - 2)
    with app_on.app_context():
        db.session.add_all([
            _done_case(others[2], 1, unit(1)),          # ไกลสุด
            _done_case(others[0], 1, unit(0)),          # ตรงสุด
            _done_case(others[1], 1, near),             # รองลงมา
            _done_case(target, 1, unit(0)),             # Defect เดียวกัน -> ตัดออก
            _done_case(others[3], 1, unit(0), "old"),   # คนละโมเดล -> ตัดออก
            AICase(defect_id=others[3], round=2, verdict="approved", status="pending"),
        ])
        db.session.commit()
        app_on.extensions["gemini_client"] = FakeGemini(embed_fn=lambda t: unit(0))
        got = ai.retrieve(db.session.get(Defect, target), k=4)
        assert [c.defect_id for c, _ in got] == [others[0], others[1], others[2]]
        assert got[0][1] == pytest.approx(1.0)


def test_retrieve_empty(app_on):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    app_on.extensions["gemini_client"] = FakeGemini()
    with app_on.app_context():
        assert ai.retrieve(db.session.get(Defect, did)) == []
