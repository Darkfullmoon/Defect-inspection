from collections import OrderedDict
from datetime import date, datetime

import json

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects import mysql

db = SQLAlchemy()


def now():
    return datetime.now().replace(microsecond=0)


inspector_users = db.Table(
    "inspector_users",
    db.Column("inspector_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(3), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False, default="")
    email = db.Column(db.String(190), nullable=False, default="")
    role = db.Column(db.String(12), nullable=False, default="user")  # admin / inspector / user
    password_hash = db.Column(db.String(255), nullable=False, default="")
    must_change_password = db.Column(db.Boolean, nullable=False, default=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    last_login_at = db.Column(db.DateTime)

    # ผู้ตรวจ -> ผู้รับผิดชอบที่ดูแล (Admin เป็นคนจับคู่)
    team = db.relationship(
        "User",
        secondary=inspector_users,
        primaryjoin=id == inspector_users.c.inspector_id,
        secondaryjoin=id == inspector_users.c.user_id,
        backref=db.backref("inspectors", lazy="selectin"),
        lazy="selectin",
        order_by="User.username",
    )

    @property
    def display(self):
        return f"{self.username} · {self.full_name}" if self.full_name else self.username

    @property
    def initials(self):
        return self.username[:2]


class Area(db.Model):
    __tablename__ = "areas"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)


class Counter(db.Model):
    __tablename__ = "counters"
    name = db.Column(db.String(30), primary_key=True)
    value = db.Column(db.Integer, nullable=False, default=0)


class Defect(db.Model):
    __tablename__ = "defects"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(24), unique=True, nullable=False, index=True)
    area_id = db.Column(db.Integer, db.ForeignKey("areas.id"), nullable=False)
    location = db.Column(db.String(160), nullable=False, default="")
    detail = db.Column(db.Text, nullable=False)
    severity = db.Column(db.String(10), nullable=False, default="medium")
    due_date = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(db.String(12), nullable=False, default="open", index=True)
    round = db.Column(db.Integer, nullable=False, default=0)
    assignee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    inspector_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=now, index=True)
    submitted_at = db.Column(db.DateTime)
    closed_at = db.Column(db.DateTime)
    reminded_on = db.Column(db.Date)

    area = db.relationship("Area", lazy="joined")
    assignee = db.relationship("User", foreign_keys=[assignee_id], lazy="joined")
    inspector = db.relationship("User", foreign_keys=[inspector_id], lazy="joined")
    photos = db.relationship("Photo", backref="defect", cascade="all, delete-orphan",
                             order_by="Photo.id", lazy="selectin")
    logs = db.relationship("DefectLog", backref="defect", cascade="all, delete-orphan",
                           order_by="DefectLog.id")
    ncr = db.relationship("NCR", backref="defect", uselist=False, cascade="all, delete-orphan",
                          lazy="selectin")
    ai_checks = db.relationship("AICheck", backref="defect", cascade="all, delete-orphan",
                                order_by="AICheck.id")
    ai_cases = db.relationship("AICase", backref="defect", cascade="all, delete-orphan",
                               order_by="AICase.id")

    @property
    def active(self):
        return self.status != "closed"

    @property
    def days_left(self):
        return (self.due_date - date.today()).days

    @property
    def has_open_ncr(self):
        return self.ncr is not None and self.ncr.status == "open"

    @property
    def bucket(self):
        """แบ่ง 3 กลุ่มแบบไม่ทับกัน ใช้ทั้ง Dashboard และป้ายสถานะ"""
        if self.status == "closed":
            return "closed"
        if self.has_open_ncr:
            return "ncr"
        return "progress"

    @property
    def before_photos(self):
        return [p for p in self.photos if p.kind == "before"]

    @property
    def after_rounds(self):
        rounds = OrderedDict()
        for p in self.photos:
            if p.kind == "after":
                rounds.setdefault(p.round, []).append(p)
        return rounds

    @property
    def cover(self):
        b = self.before_photos
        return b[0] if b else None


class Photo(db.Model):
    __tablename__ = "photos"
    id = db.Column(db.Integer, primary_key=True)
    defect_id = db.Column(db.Integer, db.ForeignKey("defects.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = db.Column(db.String(8), nullable=False)  # before / after
    round = db.Column(db.Integer, nullable=False, default=0)
    path = db.Column(db.String(255), nullable=False)
    thumb_path = db.Column(db.String(255), nullable=False)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=now)


class DefectLog(db.Model):
    __tablename__ = "defect_logs"
    id = db.Column(db.Integer, primary_key=True)
    defect_id = db.Column(db.Integer, db.ForeignKey("defects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(20), nullable=False)
    note = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=now)

    user = db.relationship("User", lazy="joined")


class NCR(db.Model):
    __tablename__ = "ncrs"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(24), unique=True, nullable=False, index=True)
    defect_id = db.Column(db.Integer, db.ForeignKey("defects.id", ondelete="CASCADE"), nullable=False, unique=True)
    status = db.Column(db.String(8), nullable=False, default="open")  # open / closed
    issued_at = db.Column(db.DateTime, nullable=False, default=now)
    due_date_at_issue = db.Column(db.Date, nullable=False)
    closed_at = db.Column(db.DateTime)


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False, default="info")
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False, default="")
    link = db.Column(db.String(255), nullable=False, default="")
    is_read = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=now)


LongText = db.Text().with_variant(mysql.LONGTEXT(), "mysql")


class AICase(db.Model):
    """คลังเคสสำหรับ RAG: 1 แถวต่อการตัดสิน (ตรวจรับ/ตีกลับ) ของ 1 รอบ"""
    __tablename__ = "ai_cases"
    __table_args__ = (db.UniqueConstraint("defect_id", "round", name="uq_ai_case_round"),)
    id = db.Column(db.Integer, primary_key=True)
    defect_id = db.Column(db.Integer, db.ForeignKey("defects.id", ondelete="CASCADE"), nullable=False, index=True)
    round = db.Column(db.Integer, nullable=False)
    verdict = db.Column(db.String(10), nullable=False)  # approved / rejected
    inspector_note = db.Column(db.Text, nullable=False, default="")
    photo_summary = db.Column(db.Text, nullable=False, default="")
    doc_text = db.Column(db.Text, nullable=False, default="")
    embedding = db.Column(LongText)
    embed_model = db.Column(db.String(60), nullable=False, default="")
    status = db.Column(db.String(8), nullable=False, default="pending", index=True)  # pending / done / error
    error = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=now)


class AICheck(db.Model):
    """ผลการกด "ให้ AI ตรวจรูป" แต่ละครั้ง"""
    __tablename__ = "ai_checks"
    id = db.Column(db.Integer, primary_key=True)
    defect_id = db.Column(db.Integer, db.ForeignKey("defects.id", ondelete="CASCADE"), nullable=False, index=True)
    round = db.Column(db.Integer, nullable=False)
    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    status = db.Column(db.String(8), nullable=False)  # done / error
    verdict = db.Column(db.String(12), nullable=False, default="")
    result_json = db.Column(db.Text, nullable=False, default="{}")
    ref_case_ids = db.Column(db.Text, nullable=False, default="[]")
    model = db.Column(db.String(60), nullable=False, default="")
    error = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=now)

    user = db.relationship("User", lazy="joined")

    @property
    def result(self):
        try:
            r = json.loads(self.result_json or "{}")
            return r if isinstance(r, dict) else {}
        except ValueError:
            return {}
