@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "BOOTSTRAP_PY="
python -c "import sys, venv; print(sys.executable)" >nul 2>&1
if not errorlevel 1 set "BOOTSTRAP_PY=python"
if not defined BOOTSTRAP_PY py -3.11 -c "import sys, venv; print(sys.executable)" >nul 2>&1
if not defined BOOTSTRAP_PY if not errorlevel 1 set "BOOTSTRAP_PY=py -3.11"
set "CODEX_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not defined BOOTSTRAP_PY if exist "%CODEX_PY%" set "BOOTSTRAP_PY=%CODEX_PY%"
if not defined BOOTSTRAP_PY (
  echo No Python 3 environment was found. Install Python and try again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" call %BOOTSTRAP_PY% -m venv .venv
if errorlevel 1 (
  echo Failed to create backend_fastapi\.venv.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed.
  pause
  exit /b 1
)
echo Backend environment is ready.
pause
