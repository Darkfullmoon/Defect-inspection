"""ใส่ข้อมูลตัวอย่างสำหรับทดลองระบบ (อย่ารันบนฐานข้อมูลจริง)

    python seed_demo.py          # บนเครื่อง (SQLite)
    docker compose exec app python seed_demo.py

ผู้ใช้ตัวอย่างทั้งหมดรหัสผ่าน Demo#1234
"""
import io
import random
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFilter
from werkzeug.datastructures import FileStorage
from werkzeug.security import generate_password_hash

load_dotenv()
import sys  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import os  # noqa: E402

os.environ.setdefault("RUN_SCHEDULER", "0")
os.environ["RUN_SCHEDULER"] = "0"

from app import create_app  # noqa: E402
from app.jobs import check_overdue  # noqa: E402
from app.models import Area, Defect, DefectLog, Photo, User, db  # noqa: E402
from app.utils import next_code, save_image  # noqa: E402

PROBLEMS = ["ผนังมีรอยแตกบริเวณมุมห้อง", "ท่อน้ำรั่ว มีน้ำซึม", "โคมไฟไม่ติด", "สีผนังลอก",
            "ตะแกรงระบายน้ำอุดตัน", "กระเบื้องพื้นแตก", "ประตูปิดไม่สนิท", "ฝ้าเพดานมีคราบน้ำ",
            "ปลั๊กไฟหลวม", "ยาแนวห้องน้ำหลุด", "หน้าต่างรั่วซึมเวลาฝนตก", "ราวบันไดโยก"]


def fake_photo(seed, after=False):
    rnd = random.Random(seed)
    base = (rnd.randint(150, 210),) * 3 if not after else (rnd.randint(205, 235),) * 3
    im = Image.new("RGB", (1200, 900), base)
    dr = ImageDraw.Draw(im)
    for _ in range(4000):
        x, y = rnd.randint(0, 1199), rnd.randint(0, 899)
        v = rnd.randint(-25, 25)
        dr.point((x, y), fill=tuple(max(0, min(255, c + v)) for c in base))
    if not after:
        x, y = rnd.randint(200, 900), rnd.randint(100, 300)
        for _ in range(18):
            nx, ny = x + rnd.randint(-40, 60), y + rnd.randint(20, 50)
            dr.line((x, y, nx, ny), fill=(60, 55, 50), width=rnd.randint(3, 7))
            x, y = nx, ny
    im = im.filter(ImageFilter.GaussianBlur(1.2))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    buf.seek(0)
    return FileStorage(buf, filename="demo.jpg")


def main():
    app = create_app({"RUN_SCHEDULER": False})
    with app.app_context():
        if User.query.filter_by(username="INA").first():
            print("มีข้อมูลตัวอย่างอยู่แล้ว")
            return
        pw = generate_password_hash("Demo#1234")
        mk = lambda u, n, r: User(username=u, full_name=n, role=r, password_hash=pw, must_change_password=False)
        ins = [mk("INA", "สมชาย ตรวจงาน", "inspector"), mk("INB", "วิภา คุมงาน", "inspector")]
        users = [mk("CTA", "ช่างเอก ซ่อมดี", "user"), mk("CTB", "ช่างบี งานไว", "user"),
                 mk("CTC", "ทีมไฟฟ้า ส่องสว่าง", "user"), mk("CTD", "ทีมประปา ไหลดี", "user")]
        db.session.add_all(ins + users)
        db.session.flush()
        ins[0].team = users[:3]
        ins[1].team = users[1:]
        areas = Area.query.all()
        rnd = random.Random(7)
        today = date.today()
        for i in range(46):
            insp = rnd.choice(ins)
            who = rnd.choice(insp.team)
            created = datetime.combine(today - timedelta(days=rnd.randint(0, 170)), datetime.min.time()) + timedelta(hours=rnd.randint(8, 17))
            due = created.date() + timedelta(days=rnd.choice([3, 5, 7, 7, 10, 14]))
            d = Defect(code=next_code("DEF", 5), area=rnd.choice(areas), location=f"ชั้น {rnd.randint(1, 8)}",
                       detail=rnd.choice(PROBLEMS), severity=rnd.choice(["low", "medium", "medium", "high"]),
                       due_date=due, assignee_id=who.id, inspector_id=insp.id, created_at=created)
            db.session.add(d)
            for k in range(rnd.randint(1, 3)):
                p, t = save_image(fake_photo(i * 10 + k))
                d.photos.append(Photo(kind="before", path=p, thumb_path=t, uploaded_by=insp.id, created_at=created))
            db.session.add(DefectLog(defect=d, user_id=insp.id, action="created", created_at=created))
            fate = rnd.random()
            if due < today and fate < 0.62 or (due >= today and fate < 0.25):
                done = min(created + timedelta(days=rnd.randint(1, (due - created.date()).days + 3)), datetime.now())
                d.round = 1
                p, t = save_image(fake_photo(i * 10 + 9, after=True))
                d.photos.append(Photo(kind="after", round=1, path=p, thumb_path=t, uploaded_by=who.id, created_at=done))
                d.status, d.submitted_at = "submitted", done
                db.session.add(DefectLog(defect=d, user_id=who.id, action="submitted", note="แก้ไขเรียบร้อย", created_at=done))
                if rnd.random() < 0.8:
                    d.status, d.closed_at = "closed", min(done + timedelta(days=1), datetime.now())
                    db.session.add(DefectLog(defect=d, user_id=insp.id, action="approved", created_at=d.closed_at))
        db.session.commit()
        print("NCR / reminders:", check_overdue())
        print("เพิ่มข้อมูลตัวอย่างแล้ว: ผู้ตรวจ INA, INB  ผู้รับผิดชอบ CTA–CTD  รหัส Demo#1234")


if __name__ == "__main__":
    main()
