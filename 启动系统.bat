@echo off
chcp 65001 >nul
title 股票池追踪系统 - 一键启动

set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%backend_fastapi"
set "BACKEND_PY="
set "PROJECT_PY=%BACKEND_DIR%\.venv\Scripts\python.exe"
set "LEGACY_PY=%BACKEND_DIR%\venv\Scripts\python.exe"
set "CODEX_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "BACKEND_LOG=%BACKEND_DIR%\uvicorn.log"
set "BACKEND_ERR=%BACKEND_DIR%\uvicorn-error.log"
set "BACKEND_RUNNER=%BACKEND_DIR%\run_backend.bat"

rem ===== 1. FastAPI 后端（8000）=====
curl.exe --fail --silent --max-time 2 http://127.0.0.1:8000/api/mentor/list >nul 2>&1
if not errorlevel 1 (
  echo FastAPI 新版后端已经运行。
  goto dsh
)
curl.exe --fail --silent --max-time 2 http://127.0.0.1:8000/health >nul 2>&1
if not errorlevel 1 (
  echo [错误] 8000 端口上运行的是旧版后端，请关闭旧后端窗口后重新启动。
  echo 为避免误杀其他程序，启动器不会强制结束未知进程。
  pause
  exit /b 1
)
echo 正在启动 FastAPI 后端（日志：backend_fastapi\uvicorn.log）...
if exist "%PROJECT_PY%" (
  "%PROJECT_PY%" -c "import uvicorn, fastapi, akshare, pypdf, multipart" >nul 2>&1
  if not errorlevel 1 set "BACKEND_PY=%PROJECT_PY%"
)
if not defined BACKEND_PY if exist "%LEGACY_PY%" (
  "%LEGACY_PY%" -c "import uvicorn, fastapi, akshare, pypdf, multipart" >nul 2>&1
  if not errorlevel 1 set "BACKEND_PY=%LEGACY_PY%"
)
if not defined BACKEND_PY if exist "%CODEX_PY%" (
  "%CODEX_PY%" -c "import uvicorn, fastapi, akshare, pypdf, multipart" >nul 2>&1
  if not errorlevel 1 set "BACKEND_PY=%CODEX_PY%"
)
if not defined BACKEND_PY (
  echo [错误] 没有找到可用的 Python 后端环境。
  echo Run backend_fastapi\setup_backend.bat first.
  pause
  exit /b 1
)
set "STOCK_POOL_PYTHON=%BACKEND_PY%"
start "股票池后端服务" /min "%ComSpec%" /d /c call "%BACKEND_RUNNER%"

rem 最多等待 30 秒，以健康检查成功为准
set /a BACKEND_WAIT=0
:wait_backend
curl.exe --fail --silent --max-time 1 http://127.0.0.1:8000/api/mentor/list >nul 2>&1
if not errorlevel 1 (
  echo FastAPI 后端已就绪。
  goto dsh
)
set /a BACKEND_WAIT+=1
if %BACKEND_WAIT% GEQ 30 goto backend_failed
ping.exe -n 2 127.0.0.1 >nul
goto wait_backend

:backend_failed
echo [错误] FastAPI 后端在 30 秒内未就绪。
echo 请检查：%BACKEND_ERR%
if exist "%BACKEND_ERR%" type "%BACKEND_ERR%"
pause
exit /b 1

:dsh
rem ===== 2. 红色小牛 dsh 服务（3080）=====
curl.exe --fail --silent --max-time 2 http://127.0.0.1:3080/ >nul 2>&1
if not errorlevel 1 goto open
echo 正在启动红色小牛 dsh 服务（日志：agent_dsh\dsh.log）...
cd /d "%ROOT_DIR%agent_dsh"
node generate-patch.mjs
start "红色小牛 dsh 服务" /min cmd /c "node_modules\.bin\dsh.cmd web --patch cordis.runtime.yml > dsh.log 2>&1"

:open
rem ===== 3. 后端已确认就绪，打开网页 =====
ping.exe -n 3 127.0.0.1 >nul
echo 正在打开网页...
if /I "%STOCK_POOL_NO_OPEN%"=="1" goto done
start "" "%ROOT_DIR%frontend\index.html"
:done
exit
