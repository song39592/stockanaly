@echo off
chcp 65001 >nul
title 股票池追踪系统 - 一键启动

rem ===== 1. FastAPI 后端（8000）=====
curl -s -m 2 http://127.0.0.1:8000/health >nul 2>&1
if not errorlevel 1 goto dsh
echo 正在启动 FastAPI 后端（日志：backend_fastapi\uvicorn.log）...
cd /d "D:\ai\backend_fastapi"
start "股票池后端服务" /min cmd /c "venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 > uvicorn.log 2>&1"

:dsh
rem ===== 2. 红色小牛 dsh 服务（3080）=====
curl -s -m 2 http://127.0.0.1:3080/ >nul 2>&1
if not errorlevel 1 goto open
echo 正在启动红色小牛 dsh 服务（日志：agent_dsh\dsh.log）...
cd /d "D:\ai\agent_dsh"
start "红色小牛 dsh 服务" /min cmd /c "node_modules\.bin\dsh.cmd web --patch cordis.patch.yml > dsh.log 2>&1"

:open
rem ===== 3. 等待服务就绪后打开网页 =====
timeout /t 4 /nobreak >nul
echo 正在打开网页...
start "" "D:\ai\frontend\index.html"
exit
