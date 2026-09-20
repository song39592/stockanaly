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
rem 版本探针：/health 里带 stock_history 才是新版后端。
rem 不能用 /api/mentor/list 判断——旧版也有该接口，会把旧后端误判成新版而直接跳过启动，
rem 结果就是「新页面 + 旧进程」= 新接口 404。
set "HEALTH_TMP=%TEMP%\stock-pool-health-%RANDOM%.json"
curl.exe --fail --silent --max-time 2 http://127.0.0.1:8000/health > "%HEALTH_TMP%" 2>nul
if not errorlevel 1 (
  findstr /c:stock_history "%HEALTH_TMP%" >nul
  if not errorlevel 1 (
    del /q "%HEALTH_TMP%" >nul 2>&1
    echo FastAPI 新版后端已经运行。
    goto dsh
  )
  del /q "%HEALTH_TMP%" >nul 2>&1
  echo.
  echo [提示] 8000 端口上跑的是旧版后端：代码已更新，但旧进程启动时加载的还是旧代码，
  echo        不会自动生效；继续用会出现「404 Not Found」（新页面调的新接口旧进程里没有）。
  echo        必须重启后端才能让新代码生效。
  call :kill_old_backend
  if errorlevel 1 exit /b 1
)
del /q "%HEALTH_TMP%" >nul 2>&1

:start_backend
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
echo 提示：更新过前端代码后请按 Ctrl+F5 强刷页面（页面是本地文件，浏览器会缓存）。
if /I "%STOCK_POOL_NO_OPEN%"=="1" goto done
start "" "%ROOT_DIR%frontend\index.html"
:done
exit

rem ============================================================
rem  结束占用 8000 端口的旧后端。
rem  只有确认该 PID 是 python 进程才允许自动结束，避免误杀其他程序。
rem  返回 0=已结束、可继续启动；1=需用户自行处理。
rem ============================================================
:kill_old_backend
set "BACKEND_PID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":8000 .*LISTENING"') do (
  if not defined BACKEND_PID set "BACKEND_PID=%%p"
)
if not defined BACKEND_PID (
  echo        未找到占用 8000 端口的进程，请手动关闭旧后端窗口后重新运行本脚本。
  pause
  exit /b 1
)
tasklist /fi "PID eq %BACKEND_PID%" /fo csv /nh | findstr /i "python" >nul
if errorlevel 1 (
  echo        占用 8000 端口的不是 Python 进程（PID %BACKEND_PID%），为安全起见不自动结束。
  echo        请自行处理后重新运行本脚本。
  pause
  exit /b 1
)
echo        占用进程：PID %BACKEND_PID%（python）
choice /c YN /t 20 /d N /m "  自动结束它并启动新版后端？(Y=结束并重启，N=我自己关窗口)"
if errorlevel 2 (
  echo        请关闭旧后端窗口后重新运行本脚本。
  pause
  exit /b 1
)
taskkill /f /pid %BACKEND_PID% >nul 2>&1
ping.exe -n 3 127.0.0.1 >nul
exit /b 0
