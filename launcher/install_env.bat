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

rem ---- 1. 找 Python：已有虚拟环境就直接复用，否则按 系统python / py / codex 顺序找；都没有则自动装免安装版 ----
set "VENV_PY="
if exist "%BACKEND_DIR%\.venv\Scripts\python.exe" set "VENV_PY=%BACKEND_DIR%\.venv\Scripts\python.exe"
if not defined VENV_PY if exist "%BACKEND_DIR%\.venv\python.exe" set "VENV_PY=%BACKEND_DIR%\.venv\python.exe"
if not defined VENV_PY if exist "%BACKEND_DIR%\venv\Scripts\python.exe" set "VENV_PY=%BACKEND_DIR%\venv\Scripts\python.exe"
if defined VENV_PY (
  "%VENV_PY%" -m pip --version >nul 2>&1
  if not errorlevel 1 (
    rem venv 只是启动器，真正的解释器和 DLL 在 base 安装目录；pyvenv.cfg 指向的
    rem 底座 Python 不在了，说明这是**从别的电脑拷来的** venv，必须重建。
    call :venv_portable "%VENV_PY%"
    if not errorlevel 1 (
      echo [1/4] 复用已有虚拟环境：%VENV_PY%
      goto pip
    )
    echo [WARN] 已有虚拟环境来自另一台电脑：pyvenv.cfg 里登记的底座 Python 不在本机。
    echo        venv 不能跨机器拷贝（pandas 等包的 DLL 会加载失败），删除后重建。
  ) else (
    echo [WARN] 已有虚拟环境不可用（没有 pip，可能是旧版/损坏），删除后重建：%VENV_PY%
  )
  rmdir /s /q "%BACKEND_DIR%\.venv" >nul 2>&1
  rmdir /s /q "%BACKEND_DIR%\venv" >nul 2>&1
  set "VENV_PY="
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

rem ---- 1.5 本机没有任何 Python：自动下载免安装版（embeddable），装到 backend_fastapi\.venv\，不写 PATH、免管理员权限 ----
if not defined BOOTSTRAP_PY (
  call :auto_embeddable
  set "VENV_PY=%BACKEND_DIR%\.venv\python.exe"
  if exist "%VENV_PY%" (
    set "BOOTSTRAP_PY=%VENV_PY%"
    goto pip
  )
)
if not defined BOOTSTRAP_PY (
  echo [FATAL] 没有找到可用的 Python 3，且自动下载免安装版失败，装不下去。
  echo         请先手动安装 Python 3.9 及以上版本（安装时勾选 Add python.exe to PATH）：
  echo         https://www.python.org/downloads/
  echo         或检查网络 / 代理后重试（自动下载需要联网）。
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
set "VERIFY_LOG=%TEMP%\stockpool_env_verify.log"
"%VENV_PY%" -c "import uvicorn, fastapi, akshare, pypdf, multipart, requests, bs4, dotenv" >"%VERIFY_LOG%" 2>&1
if not errorlevel 1 goto verify_ok
type "%VERIFY_LOG%"
echo.
rem 依赖已装齐却导不进来，绝大多数是本机安全策略拦了 pandas 的 .pyd（不是装漏了）。
findstr /i /c:"应用程序控制策略" /c:"Application Control" /c:"DLL load failed" /c:"无法加载" "%VERIFY_LOG%" >nul 2>&1
if not errorlevel 1 call :dll_blocked
findstr /i /c:"No module named" "%VERIFY_LOG%" >nul 2>&1
if not errorlevel 1 echo [HINT] 依赖确实没装全：请检查网络 / 代理 / pip 源后重跑本脚本。
echo [FATAL] 依赖装完还是导不进来，请看上面的导入报错。
if /i not "%SILENT%"=="silent" pause
exit /b 5

:verify_ok
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

rem ============================================================
rem  自动下载免安装版 Python（embeddable），仅当本机没有任何 Python 时由 :1.5 调用
rem  产物：backend_fastapi\.venv\python.exe（带 pip），与「复用已有虚拟环境」分支无缝衔接
rem ============================================================
:auto_embeddable
echo [1/4] 本机没有 Python，自动下载免安装版（embeddable，约 10MB，无需权限）...
set "PYVER=3.11.9"
set "TARGET=%BACKEND_DIR%\.venv"
set "ZIP=%TEMP%\stockpool_python_%PYVER%.zip"
set "URL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip"
if not exist "%TARGET%" mkdir "%TARGET%"

echo        下载 %URL%
powershell -NoProfile -Command "try { (New-Object System.Net.WebClient).DownloadFile('%URL%','%ZIP%') } catch { Write-Host 'download-failed'; exit 1 }"
if not exist "%ZIP%" (
  echo        默认源失败，换清华镜像...
  set "URL=https://mirrors.tuna.tsinghua.edu.cn/python/%PYVER%/python-%PYVER%-embed-amd64.zip"
  powershell -NoProfile -Command "try { (New-Object System.Net.WebClient).DownloadFile('%URL%','%ZIP%') } catch { Write-Host 'download-failed'; exit 1 }"
)
if not exist "%ZIP%" (
  echo [WARN] 自动下载 Python 失败（可能没联网 / 被代理或杀毒拦）。
  exit /b 1
)

echo        解压到 %TARGET%
powershell -NoProfile -Command "if (Test-Path '%TARGET%\python.exe') { exit 0 }; Expand-Archive -Force '%ZIP%' '%TARGET%'"

rem embeddable 默认把 site 注释掉，启用它才能 import 第三方包
if exist "%TARGET%\python311._pth" (
  powershell -NoProfile -Command "(Get-Content '%TARGET%\python311._pth') -replace '#import site','import site' | Set-Content '%TARGET%\python311._pth'"
)

echo        下载并安装 pip（get-pip.py）...
powershell -NoProfile -Command "try { (New-Object System.Net.WebClient).DownloadFile('https://bootstrap.pypa.io/get-pip.py','%TARGET%\get-pip.py') } catch { Write-Host 'getpip-failed'; exit 1 }"
if not exist "%TARGET%\get-pip.py" (
  echo [WARN] 下载 get-pip.py 失败。
  exit /b 1
)
"%TARGET%\python.exe" "%TARGET%\get-pip.py" --no-warn-script-location >nul 2>&1
if errorlevel 1 (
  echo [WARN] 安装 pip 失败。
  exit /b 1
)

if not exist "%TARGET%\python.exe" (
  echo [WARN] embeddable Python 装配后仍未找到 %TARGET%\python.exe。
  exit /b 1
)
echo        embeddable Python 就绪：%TARGET%\python.exe
exit /b 0

rem ============================================================
rem  校验 venv 是不是「本机能用」的
rem  venv 里只有启动器，真正的解释器和 DLL 在 pyvenv.cfg 登记的底座 Python
rem  （home = ...）目录下。跨机器拷贝的 venv 底座路径在本机不存在，pandas 的
rem  _libs 等 .pyd 就会加载失败。
rem  返回 0 = 可用；1 = 底座 Python 不在本机（应删除重建）
rem ============================================================
:venv_portable
set "CFG=%~dp1..\pyvenv.cfg"
if not exist "%CFG%" exit /b 0
set "BASE_PY="
for /f "tokens=1,* delims==" %%a in ('findstr /b /i "home" "%CFG%" 2^>nul') do set "BASE_PY=%%b"
if not defined BASE_PY exit /b 0
rem pyvenv.cfg 形如「home = C:\Python311」，等号后带一个空格，先剥掉
for /f "tokens=* delims= " %%c in ("%BASE_PY%") do set "BASE_PY=%%c"
if not defined BASE_PY exit /b 0
if not exist "%BASE_PY%\python.exe" exit /b 1
exit /b 0

rem ============================================================
rem  「依赖装好了但 DLL 被拦」的诊断：这不是依赖缺失，而是本机安全策略
rem  不让未签名的 .pyd 加载，只能在用户机器上放行，代码层面无解。
rem ============================================================
:dll_blocked
echo [FATAL] 依赖已经装好，但 Windows 阻止了 pandas 的 DLL 加载。
echo         典型报错：DLL load failed while importing _libs: 应用程序控制策略已阻止此文件
echo.
echo         这不是依赖没装全，请按顺序处理：
echo         1) Win11「智能应用控制」：设置 ^> 隐私和安全性 ^> Windows 安全中心
echo            ^> 应用和浏览器控制 ^> 智能应用控制 ^> 关闭（需重启；关闭后不可再开）
echo         2) 杀毒 / 安全软件（360、火绒、电脑管家）的应用锁或驱动防护：
echo            把项目目录加入信任区，或临时退出后重跑本脚本
echo         3) 虚拟环境是从别的电脑拷来的：删除 backend_fastapi\venv 与
echo            backend_fastapi\.venv 后重跑本脚本，由本机 Python 现场重建
echo.
exit /b 0
