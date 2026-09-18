@echo off
cd /d "%~dp0"
if not defined STOCK_POOL_PYTHON set "STOCK_POOL_PYTHON=%~dp0.venv\Scripts\python.exe"
"%STOCK_POOL_PYTHON%" -m uvicorn main:app --host 127.0.0.1 --port 8000 1>>uvicorn.log 2>uvicorn-error.log
