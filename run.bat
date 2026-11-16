@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ---- ฐานข้อมูล: MySQL + phpMyAdmin ใน Docker ----
docker compose up -d
if errorlevel 1 (
  echo.
  echo [!] เปิด Docker ไม่สำเร็จ - กรุณาเปิด Docker Desktop ก่อน แล้วรัน run.bat อีกครั้ง
  pause
  exit /b 1
)

rem ---- ติดตั้ง Python packages ครั้งแรก ----
if not exist ".venv\Scripts\python.exe" (
  echo กำลังเตรียม Python ครั้งแรก...
  python -m venv .venv || (echo [!] ไม่พบ Python 3 & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

rem ---- เปิดโปรแกรม ----
".venv\Scripts\python.exe" wsgi.py
pause
