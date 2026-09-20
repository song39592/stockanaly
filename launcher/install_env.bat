@echo off
rem =========================================================================
rem  环境安装脚本
rem  由「股票池追踪系统」启动器在环境检查未通过时拉起，也可以双击手动运行。
rem
rem  用法：install_env.bat "<项目根目录>" [silent]
rem        silent = 结尾不 pause（启动器拉起时用，避免卡住不返回）
rem
rem  退出码：0 成功 / 1 根目录不对 / 2 没有 Python / 3 建虚拟环境失败
rem          4 装依赖失败 / 5 依赖校验失败
rem
rem  原则：每个步骤最多补一次（pip 默认源失败才换镜像），绝不反复重试；
rem        失败立刻带原因退出，由启动器显示日志并退出。
rem =========================================================================
chcp 65001 >nul
setlocal

set "ROOT=%~1"
if not defined ROOT set "ROOT=%~dp0.."
for %%i in ("%ROOT%") do set "ROOT=%%~fi"
set "BACKEND_DIR=%ROOT%\backend_fastapi"
set "DSH_DIR=%ROOT%\agent_dsh"
set "CODEX_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "SILENT=%~2"

echo ==== 股票池追踪系统 - 环境安装 ====
echo 项目根目录：%ROOT%

if not exist "%BACKEND_DIR%\requirements.txt" (
  echo [FATAL] 找不到 %BACKEND_DIR%\requirements.txt，项目根目录不对。
  if /i not "%SILENT%"=="silent" pause
  exit /b 1
)

rem ---- 1. 找 Python：已有虚拟环境就直接复用，没有才用系统 Python 建一个 ----
set "VENV_PY=%BACKEND_DIR%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" if exist "%BACKEND_DIR%\venv\Scripts\python.exe" set "VENV_PY=%BACKEND_DIR%\venv\Scripts\python.exe"
if exist "%VENV_PY%" (
  echo [1/4] 复用已有虚拟环境：%VENV_PY%
  goto pip
)

set "BOOTSTRAP_PY="
where python >nul 2>&1
if not errorlevel 1 (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 set "BOOTSTRAP_PY=python"
)
if not defined BOOTSTRAP_PY (
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "BOOTSTRAP_PY=py -3"
  )
)
if not defined BOOTSTRAP_PY if exist "%CODEX_PY%" (
  "%CODEX_PY%" -c "import sys" >nul 2>&1
  if not errorlevel 1 set "BOOTSTRAP_PY=%CODEX_PY%"
)
if not defined BOOTSTRAP_PY (
  echo [FATAL] 没有找到可用的 Python 3，装不下去。
  echo         请先安装 Python 3.9 及以上版本（安装时勾选 Add python.exe to PATH），
  echo         装完重新打开启动器：https://www.python.org/downloads/
  if /i not "%SILENT%"=="silent" pause
  exit /b 2
)
for /f "delims=" %%v in ('%BOOTSTRAP_PY% -c "import sys; print(sys.version.split()[0])" 2^>nul') do set "BOOTSTRAP_VER=%%v"
echo [1/4] 用系统 Python 创建虚拟环境：%BOOTSTRAP_PY%  %BOOTSTRAP_VER%
%BOOTSTRAP_PY% -c "import venv" >nul 2>&1
if errorlevel 1 (
  echo [FATAL] 这个 Python 没有 venv 模块，没法创建虚拟环境：%BOOTSTRAP_PY%
  if /i not "%SILENT%"=="silent" pause
  exit /b 3
)
%BOOTSTRAP_PY% -m venv "%BACKEND_DIR%\.venv"
if errorlevel 1 (
  echo [FATAL] 创建虚拟环境 backend_fastapi\.venv 失败。
  if /i not "%SILENT%"=="silent" pause
  exit /b 3
)
set "VENV_PY=%BACKEND_DIR%\.venv\Scripts\python.exe"

:pip
"%VENV_PY%" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo [FATAL] 虚拟环境里没有 pip：%VENV_PY%
  if /i not "%SILENT%"=="silent" pause
  exit /b 3
)

rem ---- 2. 后端依赖 ----
echo [2/4] 安装后端依赖（requirements.txt）...
"%VENV_PY%" -m pip install -r "%BACKEND_DIR%\requirements.txt" --disable-pip-version-check
if not errorlevel 1 goto verify
echo        默认源失败，换清华镜像再试一次（只补这一次）...
"%VENV_PY%" -m pip install -r "%BACKEND_DIR%\requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple --disable-pip-version-check
if errorlevel 1 (
  echo [FATAL] 依赖安装失败：默认源和清华镜像都失败。
  echo        常见原因：没联网 / 公司代理 / 杀毒拦截。可手动执行：
  echo        "%VENV_PY%" -m pip install -r "%BACKEND_DIR%\requirements.txt"
  if /i not "%SILENT%"=="silent" pause
  exit /b 4
)

:verify
rem ---- 3. 校验依赖真的能导入 ----
echo [3/4] 校验依赖导入...
"%VENV_PY%" -c "import uvicorn, fastapi, akshare, pypdf, multipart, requests, bs4, dotenv"
if errorlevel 1 (
  echo [FATAL] 依赖装完还是导不进来，请看上面 pip 的输出。
  if /i not "%SILENT%"=="silent" pause
  exit /b 5
)
echo        后端依赖校验通过。

rem ---- 4. AI 服务依赖（可选：失败只警告，后端和网页不受影响）----
where node >nul 2>&1
if errorlevel 1 (
  echo [WARN] 没有找到 Node.js：AI 服务（:3080）起不来，后端和网页不受影响。
  goto done
)
if exist "%DSH_DIR%\node_modules\@deepseek-ai" goto done
echo        AI 服务依赖缺失，开始 npm install（可能要几分钟）...
pushd "%DSH_DIR%"
call npm install --no-audit --no-fund
if errorlevel 1 echo [WARN] npm install 失败：AI 服务（:3080）起不来，后端和网页不受影响。
popd

:done
echo ENV_OK 环境安装完成。
if /i not "%SILENT%"=="silent" pause
exit /b 0
