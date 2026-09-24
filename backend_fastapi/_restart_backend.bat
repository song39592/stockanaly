@echo off
cd /d C:\Users\Admin\Desktop\stockanaly-main\backend_fastapi
C:\Users\Admin\.workbuddy\binaries\python\versions\3.11.9\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 > C:\Users\Admin\Desktop\stockanaly-main\backend_fastapi\restart.log 2>&1
