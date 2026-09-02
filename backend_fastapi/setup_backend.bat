@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.11 -m venv venv
if errorlevel 1 (
  echo 无法创建虚拟环境。请先安装 Python 3.11，并确保 py 命令可用。
  pause
  exit /b 1
)
venv\Scripts\python.exe -m pip install -r requirements.txt
pause
