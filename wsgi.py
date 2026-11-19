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


def lan_ip():
    """IP ของเครื่องในวง Wi-Fi/LAN (ไม่ได้ส่งข้อมูลออกไปจริง แค่ถามระบบว่าจะใช้การ์ดเครือข่ายไหน)"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


if __name__ == "__main__":
    import logging

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5100"))
    print(f"Defect Management System: http://localhost:{port}", flush=True)
    ip = lan_ip() if host == "0.0.0.0" else None
    if ip and not ip.startswith("127."):
        print(f"  มือถือ / เครื่องอื่นในวง Wi-Fi เดียวกัน: http://{ip}:{port}", flush=True)
    print("  (Ctrl+C เพื่อหยุด)", flush=True)
    # ซ่อนบรรทัด "Serving on http://0.0.0.0:..." ของ waitress — ที่อยู่นั้นเปิดในเบราว์เซอร์ไม่ได้
    logging.getLogger("waitress").setLevel(logging.WARNING)
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8, max_request_body_size=app.config["MAX_CONTENT_LENGTH"])
    except ImportError:  # ยังไม่ได้ติดตั้ง waitress
        app.run(host=host, port=port, debug=False)
