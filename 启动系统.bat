@echo off
chcp 65001 >nul
title 股票池追踪系统 - 一键启动
cd /d "D:\ai\backend_fastapi"

rem 后端已在本机 8000 端口运行时直接打开网页，避免重复启动
curl -s -m 2 http://127.0.0.1:8000/health >nul 2>&1
if not errorlevel 1 goto open

echo 正在启动 FastAPI 后端（已最小化，日志见 backend_fastapi\uvicorn.log）...
start "股票池后端服务" /min cmd /c "venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 > uvicorn.log 2>&1"
rem 稍等后端就绪再打开网页
timeout /t 3 /nobreak >nul

:open
echo 正在打开网页...
start "" "D:\ai\frontend\index.html"
exit
