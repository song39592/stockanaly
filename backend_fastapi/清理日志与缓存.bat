@echo off
rem ============================================================
rem  清理：日志轮转旧文件 / 旧诊断包 / 可重建缓存 / 跨周周榜结果
rem  后端起不来也能跑（cleanup.py 刻意不依赖 FastAPI 与业务模块）。
rem
rem  用法：
rem    双击本文件                   -> 预览模式（只列出会删什么，不真删）
rem    清理日志与缓存.bat --go       -> 实际清理
rem    清理日志与缓存.bat --go --bak  -> 连 bars\*.db.bak 一起清（约 1GB，不可逆）
rem ============================================================
cd /d "%~dp0"

set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo 数据盘清理
echo 解释器: %PY%
echo.

if "%1"=="--go" goto real
if "%1"=="-go" goto real

echo [预览模式] 只列出将要删除的内容，不会真的删除。
echo            要实际清理请加参数 --go 重新运行本脚本。
echo.
"%PY%" "%~dp0cleanup.py" --dry-run %*
goto done

:real
"%PY%" "%~dp0cleanup.py" %2 %3 %4 %5 %6 %7 %8 %9

:done
echo.
echo 完成。
pause
