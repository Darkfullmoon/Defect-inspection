import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _bool(name, default="0"):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _database_url():
    """DATABASE_URL ถ้ามี / ไม่งั้นต่อ MySQL ใน Docker ด้วยค่า MYSQL_* ใน .env / ไม่มีเลยใช้ SQLite"""
    if os.getenv("DATABASE_URL"):
        return os.getenv("DATABASE_URL")
    if os.getenv("MYSQL_USER") and os.getenv("MYSQL_DATABASE"):
        from urllib.parse import quote_plus
        return "mysql+pymysql://{}:{}@{}:{}/{}?charset=utf8mb4".format(
            quote_plus(os.getenv("MYSQL_USER")), quote_plus(os.getenv("MYSQL_PASSWORD", "")),
            os.getenv("MYSQL_HOST", "127.0.0.1"), os.getenv("MYSQL_PORT", "3307"),
            os.getenv("MYSQL_DATABASE"))
    return "sqlite:///" + os.path.join(BASE_DIR, "dev.db")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))
    MAX_CONTENT_LENGTH = 80 * 1024 * 1024  # ต่อ 1 คำขอ (หลายรูปรวมกัน)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # บัญชี Admin ตายตัว: ชื่อ ADM รหัสอ่านจาก .env ทุกครั้งที่ล็อกอิน
    ADMIN_USERNAME = "ADM"
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Test*123")
    # รหัสกลางสำหรับผู้ใช้ใหม่ / หลังรีเซ็ต
    DEFAULT_PASSWORD = os.getenv("DEFAULT_PASSWORD", "Test*123")


    RUN_SCHEDULER = _bool("RUN_SCHEDULER", "1")
    CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "10"))
    DEFAULT_DUE_DAYS = int(os.getenv("DEFAULT_DUE_DAYS", "7"))

    # AI ตรวจรูป (Gemini) — GEMINI_API_KEY ว่าง = ปิด AI ทั้งหมด
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    GEMINI_EMBED_MODEL = (os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001").strip()
                          or "gemini-embedding-001")
    AI_BACKFILL_DELAY_SEC = float(os.getenv("AI_BACKFILL_DELAY_SEC", "4"))  # พักระหว่างเคสตอนสร้างคลังย้อนหลัง
    AI_INLINE = _bool("AI_INLINE", "0")  # 1 = ประมวลผลคลังเคสทันทีแทน background thread (ใช้ตอนทดสอบ)
