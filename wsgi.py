"""เปิดโปรแกรมบนเครื่อง:  python wsgi.py   (หรือดับเบิลคลิก run.bat)

อ่านค่าจาก .env — ฐานข้อมูลคือ MySQL ใน Docker (localhost:3307)
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5100"))
    print(f"Defect Management System: http://localhost:{port}  (Ctrl+C เพื่อหยุด)", flush=True)
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8, max_request_body_size=app.config["MAX_CONTENT_LENGTH"])
    except ImportError:  # ยังไม่ได้ติดตั้ง waitress
        app.run(host=host, port=port, debug=False)
