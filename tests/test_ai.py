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
