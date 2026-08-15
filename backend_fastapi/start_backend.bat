@echo off
cd /d "D:\ai\backend_fastapi"
venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
