@echo off
cd /d "%~dp0"
set "BACKEND_PY=%~dp0venv\Scripts\python.exe"
set "CODEX_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%BACKEND_PY%" "%BACKEND_PY%" -c "import uvicorn, fastapi, akshare, pypdf, multipart" >nul 2>&1
if errorlevel 1 set "BACKEND_PY="
if not defined BACKEND_PY if exist "%CODEX_PY%" (
  "%CODEX_PY%" -c "import uvicorn, fastapi, akshare, pypdf, multipart" >nul 2>&1
  if not errorlevel 1 set "BACKEND_PY=%CODEX_PY%"
)
if not defined BACKEND_PY (
  echo 未找到可用的 Python 环境。请先运行 setup_backend.bat。
  pause
  exit /b 1
)
"%BACKEND_PY%" -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
