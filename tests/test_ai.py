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


# ---------- Task 3: ปุ่มตรวจ + การ์ด ----------
GOOD = json.dumps({"verdict": "not_fixed", "confidence": "medium", "summary": "ยังเห็นรอยร้าวมุมซ้าย",
                   "findings": ["รอยร้าวยังอยู่"], "photo_issues": [],
                   "similar_cases": [{"code": "DEF-X", "why": "คล้ายกัน"}]})


def test_ai_check_inspector(app_on):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    app_on.extensions["gemini_client"] = FakeGemini(reply=GOOD)
    r = insb.post(f"/defects/{did}/ai-check")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/defects/{did}#ai")
    with app_on.app_context():
        chk = AICheck.query.filter_by(defect_id=did).one()
        assert chk.status == "done" and chk.verdict == "not_fixed" and chk.round == 1
        assert chk.result["summary"] == "ยังเห็นรอยร้าวมุมซ้าย"
        assert db.session.get(Defect, did).status == "submitted"
        assert DefectLog.query.filter_by(defect_id=did, action="ai_check").count() == 1
    page = insb.get(f"/defects/{did}").get_data(as_text=True)
    assert "น่าจะยังไม่เสร็จ" in page and "ผู้ตรวจเป็นผู้ตัดสิน" in page and "ยังเห็นรอยร้าวมุมซ้าย" in page


def test_ai_check_permissions(app_on):
    insb, conb, admb = users(app_on)
    did = new_defect(app_on, insb, conb)
    app_on.extensions["gemini_client"] = FakeGemini(reply=GOOD)
    assert conb.post(f"/defects/{did}/ai-check").status_code == 403
    assert admb.post(f"/defects/{did}/ai-check").status_code == 302
    page = conb.get(f"/defects/{did}").get_data(as_text=True)
    assert "ความเห็น AI" not in page and "ให้ AI ตรวจรูป" not in page and "น่าจะยังไม่เสร็จ" not in page
    printed = conb.get(f"/defects/{did}/print").get_data(as_text=True)
    assert "ให้ AI ตรวจรูป" not in printed and "น่าจะยังไม่เสร็จ" not in printed
    assert "ให้ AI ตรวจรูป" in insb.get(f"/defects/{did}").get_data(as_text=True)
    open_did = new_defect(app_on, insb, conb, submit=False)
    assert insb.post(f"/defects/{open_did}/ai-check").status_code == 403


def test_ai_check_disabled(app_off):
    insb, conb, _ = users(app_off)
    did = new_defect(app_off, insb, conb)
    assert insb.post(f"/defects/{did}/ai-check").status_code == 404
    assert "ให้ AI ตรวจรูป" not in insb.get(f"/defects/{did}").get_data(as_text=True)


def test_ai_check_bad_json(app_on):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    for reply in ("not json", json.dumps({"verdict": "maybe", "confidence": "high"})):
        app_on.extensions["gemini_client"] = FakeGemini(reply=reply)
        assert insb.post(f"/defects/{did}/ai-check").status_code == 302
    with app_on.app_context():
        assert [c.status for c in AICheck.query.filter_by(defect_id=did)] == ["error", "error"]
    assert insb.get(f"/defects/{did}").status_code == 200


def test_ai_check_missing_after_photo(app_on):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    with app_on.app_context():
        for p in db.session.get(Defect, did).after_rounds[1]:
            os.remove(os.path.join(app_on.config["UPLOAD_DIR"], p.path))
    fake = FakeGemini(reply=GOOD)
    app_on.extensions["gemini_client"] = fake
    assert insb.post(f"/defects/{did}/ai-check").status_code == 302
    with app_on.app_context():
        chk = AICheck.query.filter_by(defect_id=did).one()
        assert chk.status == "error" and chk.error == "ไม่พบรูปหลังแก้ไขของรอบล่าสุด"
    assert fake.count("generate") == 0


# ---------- Task 4: เก็บเคสตอนตรวจ, เก็บกวาด, สร้างคลังย้อนหลัง ----------
def review(insb, did, decision, note=""):
    r = insb.post(f"/defects/{did}/review", {"decision": decision, "note": note})
    assert r.status_code == 302


def test_review_creates_case(app_on):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    app_on.extensions["gemini_client"] = FakeGemini(reply="ยังมีรอย")
    review(insb, did, "reject", "สียังไม่เรียบ")
    submit_fix(conb, did)
    review(insb, did, "approve", "ผ่าน")
    with app_on.app_context():
        cases = {c.round: c for c in AICase.query.filter_by(defect_id=did)}
        assert cases[1].verdict == "rejected" and cases[1].status == "done"
        assert cases[1].inspector_note == "สียังไม่เรียบ"
        assert cases[2].verdict == "approved" and cases[2].status == "done"


def test_review_without_key_no_case(app_off):
    insb, conb, _ = users(app_off)
    did = new_defect(app_off, insb, conb)
    review(insb, did, "approve")
    with app_off.app_context():
        assert AICase.query.count() == 0
        assert db.session.get(Defect, did).status == "closed"


def test_enqueue_same_round_twice(app_on):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    app_on.extensions["gemini_client"] = FakeGemini(reply="x")
    with app_on.app_context():
        d = db.session.get(Defect, did)
        ai.enqueue_case(d, 1, "rejected", "a")
        ai.enqueue_case(d, 1, "approved", "b")
        rows = AICase.query.filter_by(defect_id=did).all()
        assert len(rows) == 1 and rows[0].verdict == "approved" and rows[0].inspector_note == "b"


def test_review_survives_ai_failure(app_on):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    app_on.extensions["gemini_client"] = FakeGemini(fail=True)
    review(insb, did, "approve", "ผ่าน")
    with app_on.app_context():
        assert db.session.get(Defect, did).status == "closed"
        assert AICase.query.filter_by(defect_id=did).one().status == "error"
        # งานเก็บกวาดลองใหม่แล้วสำเร็จ
        app_on.extensions["gemini_client"] = FakeGemini(reply="ok")
        assert ai.process_pending_cases() == 1
        assert AICase.query.filter_by(defect_id=did).one().status == "done"


def test_backfill_round_mapping(tmp_path):
    app = make_app(tmp_path, key="")
    try:
        insb, conb, _ = users(app)
        did = new_defect(app, insb, conb)
        review(insb, did, "reject", "ไม่ผ่าน")
        submit_fix(conb, did)
        review(insb, did, "approve", "ผ่าน")
        app.config["AI_ENABLED"] = True
        app.extensions["gemini_client"] = FakeGemini(reply="x")
        with app.app_context():
            assert AICase.query.count() == 0
            assert ai.backfill_cases() == 2
            got = {c.round: (c.verdict, c.inspector_note) for c in AICase.query.filter_by(defect_id=did)}
            assert got == {1: ("rejected", "ไม่ผ่าน"), 2: ("approved", "ผ่าน")}
            assert ai.backfill_cases() == 0
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()


def test_admin_ai_page(app_on, tmp_path):
    insb, conb, admb = users(app_on)
    did = new_defect(app_on, insb, conb)
    with app_on.app_context():
        db.session.add(AICase(defect_id=did, round=1, verdict="rejected", status="error"))
        db.session.commit()
    page = admb.get("/admin/ai")
    assert page.status_code == 200 and "คลังเคส AI" in page.get_data(as_text=True)
    assert insb.get("/admin/ai").status_code == 403
    (tmp_path / "off").mkdir()
    app_off = make_app(tmp_path / "off", key="")
    _, _, adm_off = users(app_off)
    assert adm_off.get("/admin/ai").status_code == 404


# ---------- แก้จากการรีวิว ----------
def test_client_has_http_timeout(app_on, monkeypatch):
    import google.genai
    seen = {}

    class Spy:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(google.genai, "Client", Spy)
    app_on.extensions.pop("gemini_client", None)
    with app_on.app_context():
        ai.client()
    assert seen["api_key"] == "test-key"
    assert seen["http_options"].timeout == ai.HTTP_TIMEOUT_MS == 60_000


def test_ai_sweep_is_separate_job(app_on):
    from app.jobs import start_scheduler
    sched = start_scheduler(app_on)
    try:
        assert sched.get_job("overdue") is not None
        job = sched.get_job("ai_cases")
        assert job is not None and job.max_instances == 1
    finally:
        sched.shutdown(wait=False)


def test_scheduler_without_key_has_no_ai_job(app_off):
    from app.jobs import start_scheduler
    sched = start_scheduler(app_off)
    try:
        assert sched.get_job("ai_cases") is None
    finally:
        sched.shutdown(wait=False)


def test_ai_check_empty_reply_thai_error(app_on):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    app_on.extensions["gemini_client"] = FakeGemini(reply="")
    page = insb.post(f"/defects/{did}/ai-check", follow_redirects=True).get_data(as_text=True)
    with app_on.app_context():
        chk = AICheck.query.filter_by(defect_id=did).one()
    assert chk.status == "error" and "Expecting value" not in chk.error
    assert "AI ตอบไม่อยู่ในรูปแบบ" in chk.error and "Expecting value" not in page


def test_ai_check_survives_retrieval_failure(app_on):
    insb, conb, _ = users(app_on)
    other = new_defect(app_on, insb, conb, submit=False)
    did = new_defect(app_on, insb, conb)
    with app_on.app_context():
        db.session.add(_done_case(other, 1, unit(0)))
        db.session.commit()
    app_on.extensions["gemini_client"] = FakeGemini(reply=GOOD, fail_embed=True)
    insb.post(f"/defects/{did}/ai-check")
    with app_on.app_context():
        chk = AICheck.query.filter_by(defect_id=did).one()
        assert chk.status == "done" and chk.verdict == "not_fixed"
        assert json.loads(chk.ref_case_ids) == []


# ---------- แก้เรื่องเล็กจากการรีวิว ----------
def _case(app, did, rnd=1, status="pending", verdict="rejected"):
    with app.app_context():
        c = AICase(defect_id=did, round=rnd, verdict=verdict, status=status)
        db.session.add(c)
        db.session.commit()
        return c.id


def test_claimed_case_not_processed_twice(app_on):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    cid = _case(app_on, did)
    fake = FakeGemini(reply="x")
    app_on.extensions["gemini_client"] = fake
    with app_on.app_context():
        assert ai.claim_case(cid) is True
        assert ai.claim_case(cid) is False
        ai.process_case(cid)  # มีคนอื่นกำลังทำอยู่ -> ไม่เรียก Gemini ซ้ำ
        assert fake.count("generate") == 0
        assert db.session.get(AICase, cid).status == "working"


def test_startup_resets_stuck_working_cases(app_on):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    cid = _case(app_on, did, status="working")
    app2 = create_app({"SQLALCHEMY_DATABASE_URI": app_on.config["SQLALCHEMY_DATABASE_URI"],
                       "UPLOAD_DIR": app_on.config["UPLOAD_DIR"], "RUN_SCHEDULER": False,
                       "TESTING": True, "GEMINI_API_KEY": "k", "AI_INLINE": True})
    with app2.app_context():
        assert db.session.get(AICase, cid).status == "pending"


def test_sweep_processes_pending_before_errors(app_on):
    insb, conb, _ = users(app_on)
    d1 = new_defect(app_on, insb, conb)
    d2 = new_defect(app_on, insb, conb)
    errs = [_case(app_on, d1, rnd=r, status="error") for r in range(1, 4)]
    pend = _case(app_on, d2, rnd=1, status="pending")
    app_on.extensions["gemini_client"] = FakeGemini(reply="x")
    with app_on.app_context():
        ai.process_pending_cases(limit=1)
        assert db.session.get(AICase, pend).status == "done"
        assert all(db.session.get(AICase, e).status == "error" for e in errs)


def test_sweep_rotates_error_cases(app_on, monkeypatch):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    ids = [_case(app_on, did, rnd=r, status="error") for r in range(1, 6)]
    picked = []
    monkeypatch.setattr(ai, "process_case", lambda cid: picked.append(cid))
    monkeypatch.setattr(ai.random, "sample", lambda pop, k: list(pop)[-k:])
    with app_on.app_context():
        ai.process_pending_cases(limit=2)
    assert picked == ids[-2:]  # สุ่มเลือก ไม่ใช่เอาแถว id ต่ำสุดเสมอ


def test_backfill_throttles_and_client_retries(app_on, monkeypatch):
    insb, conb, admb = users(app_on)
    did = new_defect(app_on, insb, conb)
    _case(app_on, did, rnd=1)
    sleeps = []
    monkeypatch.setattr(ai.time, "sleep", lambda s: sleeps.append(s))
    app_on.extensions["gemini_client"] = FakeGemini(reply="x")
    with app_on.app_context():
        ai.process_pending_cases(limit=10, delay=4)
    assert sleeps == [4]
    assert app_on.config["AI_BACKFILL_DELAY_SEC"] == 4

    import google.genai
    seen = {}
    monkeypatch.setattr(google.genai, "Client", lambda **kw: seen.update(kw))
    app_on.extensions.pop("gemini_client", None)
    with app_on.app_context():
        ai.client()
    assert seen["http_options"].retry_options.attempts == 3


def test_process_case_defect_deleted_midway(app_on):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    cid = _case(app_on, did)
    fake = FakeGemini(fail=True)

    def vanish():
        with db.engine.begin() as conn:
            conn.execute(AICase.__table__.delete().where(AICase.id == cid))

    fake.on_generate = vanish
    app_on.extensions["gemini_client"] = fake
    with app_on.app_context():
        assert ai.process_case(cid) is None


def test_process_case_missing_after_files_skipped(app_on):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    with app_on.app_context():
        for p in db.session.get(Defect, did).after_rounds[1]:
            os.remove(os.path.join(app_on.config["UPLOAD_DIR"], p.path))
    cid = _case(app_on, did)
    fake = FakeGemini(reply="x")
    app_on.extensions["gemini_client"] = fake
    with app_on.app_context():
        c = ai.process_case(cid)
        assert c.status == "skip" and fake.count("generate") == 0
        assert ai.process_pending_cases() == 0  # ไม่ถูกลองซ้ำ
        assert ai.case_stats()["skip"] == 1


def test_process_case_empty_summary_is_error(app_on):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    cid = _case(app_on, did)
    app_on.extensions["gemini_client"] = FakeGemini(reply="   ")
    with app_on.app_context():
        assert ai.process_case(cid).status == "error"


def test_retrieve_skips_wrong_dimension(app_on):
    insb, conb, _ = users(app_on)
    target = new_defect(app_on, insb, conb)
    other = new_defect(app_on, insb, conb, submit=False)
    other2 = new_defect(app_on, insb, conb, submit=False)
    with app_on.app_context():
        db.session.add_all([_done_case(other, 1, [1.0, 0.0, 0.0]), _done_case(other2, 1, unit(0))])
        db.session.commit()
        app_on.extensions["gemini_client"] = FakeGemini(embed_fn=lambda t: unit(0))
        got = ai.retrieve(db.session.get(Defect, target))
        assert [c.defect_id for c, _ in got] == [other2]


def test_retrieve_caches_vectors(app_on, monkeypatch):
    insb, conb, _ = users(app_on)
    target = new_defect(app_on, insb, conb)
    other = new_defect(app_on, insb, conb)
    with app_on.app_context():
        db.session.add(_done_case(other, 1, unit(0)))
        db.session.commit()
        app_on.extensions["gemini_client"] = FakeGemini(embed_fn=lambda t: unit(0))
        d = db.session.get(Defect, target)
        ai.retrieve(d)
        parsed = []
        real = ai._parse_vec
        monkeypatch.setattr(ai, "_parse_vec", lambda s: parsed.append(1) or real(s))
        assert ai.retrieve(d)[0][1] == pytest.approx(1.0)
        assert parsed == []  # ครั้งที่สองใช้ cache
        # ประมวลผลเคสใหม่ -> cache ต้องอัปเดตตาม
        app_on.extensions["gemini_client"] = FakeGemini(reply="x", embed_fn=lambda t: unit(1))
        c = AICase.query.filter_by(defect_id=other).one()
        c.status = "pending"
        db.session.commit()
        ai.process_case(c.id)
        app_on.extensions["gemini_client"] = FakeGemini(embed_fn=lambda t: unit(0))
        assert ai.retrieve(d)[0][1] == pytest.approx(0.0)


def test_backfill_survives_concurrent_insert(app_on, monkeypatch):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    app_on.config["AI_ENABLED"] = False
    review(insb, did, "approve", "ผ่าน")
    app_on.config["AI_ENABLED"] = True
    _case(app_on, did, rnd=1, verdict="approved")
    monkeypatch.setattr(ai, "_existing_keys", lambda: set())  # จำลองว่าอ่านก่อนอีกเธรดเพิ่มแถว
    with app_on.app_context():
        assert ai.backfill_cases() == 0
        assert AICase.query.filter_by(defect_id=did).count() == 1


def test_ai_check_ajax_returns_card(app_on):
    insb, conb, _ = users(app_on)
    did = new_defect(app_on, insb, conb)
    app_on.extensions["gemini_client"] = FakeGemini(reply=GOOD)
    r = insb.post(f"/defects/{did}/ai-check", headers={"X-Requested-With": "fetch"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and 'id="ai"' in body and "น่าจะยังไม่เสร็จ" in body
    assert "<html" not in body.lower()
    assert "data-ai-check" in insb.get(f"/defects/{did}").get_data(as_text=True)
