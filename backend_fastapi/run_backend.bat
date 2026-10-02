@echo off
cd /d "%~dp0"
if not defined STOCK_POOL_PYTHON set "STOCK_POOL_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%STOCK_POOL_PYTHON%" set "STOCK_POOL_PYTHON=%~dp0venv\Scripts\python.exe"
rem 开发/自用时可设 STOCK_POOL_RELOAD=1：改后端代码自动热重载，省掉手动重启。
rem 注意：热重载会重启进程，正在跑的后台任务（K线同步等）会被打断，正式使用不要开。
rem 日志配置：把 uvicorn 的启动横幅从 stderr 改到 stdout（详见 uvicorn_logging.json），
rem 否则启动器按输出流打标签时会显示成 [err] INFO: ... 看着像报错。文件缺失则退回默认配置。
set "LOG_ARG="
if exist "%~dp0uvicorn_logging.json" set "LOG_ARG=--log-config "%~dp0uvicorn_logging.json""
if /I "%STOCK_POOL_RELOAD%"=="1" (
  "%STOCK_POOL_PYTHON%" -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload %LOG_ARG% 1>>uvicorn.log 2>>uvicorn-error.log
) else (
  "%STOCK_POOL_PYTHON%" -m uvicorn main:app --host 127.0.0.1 --port 8000 %LOG_ARG% 1>>uvicorn.log 2>>uvicorn-error.log
)
