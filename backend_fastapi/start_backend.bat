@echo off
cd /d "%~dp0"
set "BACKEND_PY="
set "PROJECT_PY=%~dp0.venv\Scripts\python.exe"
set "LEGACY_PY=%~dp0venv\Scripts\python.exe"
set "CODEX_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
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
  echo No usable Python environment. Run setup_backend.bat first.
  pause
  exit /b 1
)
rem 日志配置：把 uvicorn 的启动横幅从 stderr 改到 stdout（详见 uvicorn_logging.json），
rem 否则会被打成 [err] INFO: ... 看着像报错。文件缺失则退回默认配置。
set "LOG_ARG="
if exist "%~dp0uvicorn_logging.json" set "LOG_ARG=--log-config "%~dp0uvicorn_logging.json""
"%BACKEND_PY%" -m uvicorn main:app --host 127.0.0.1 --port 8000 %LOG_ARG%
pause
