@echo off
chcp 65001 >nul
rem ============================================================
rem  股票池追踪系统 · 原生 EXE 构建脚本
rem  使用 Windows 自带的 .NET Framework 编译器 csc.exe：
rem    离线编译、零第三方依赖、不需要安装 Visual Studio / SDK
rem  产物：项目根目录\股票池追踪系统.exe
rem ============================================================

set "FW=C:\Windows\Microsoft.NET\Framework64\v4.0.30319\"
set "SRC=%~dp0StockPoolLauncher.cs"
set "OUT=%~dp0..\股票池追踪系统.exe"

if not exist "%FW%csc.exe" (
  echo [错误] 未找到 csc.exe：%FW%
  echo 请确认系统已安装 .NET Framework 4.x
  pause
  exit /b 1
)

echo 正在编译原生窗口程序...
"%FW%csc.exe" /nologo /noconfig /target:winexe /platform:anycpu /optimize+ /codepage:65001 ^
  "/out:%OUT%" ^
  "/r:%FW%mscorlib.dll" ^
  "/r:%FW%System.dll" ^
  "/r:%FW%System.Core.dll" ^
  "/r:%FW%System.Drawing.dll" ^
  "/r:%FW%System.Windows.Forms.dll" ^
  "/r:%FW%System.Web.dll" ^
  "/r:%FW%System.Web.Extensions.dll" ^
  "%SRC%" ^
  "%~dp0ValuationPage.cs" ^
  "%~dp0MarketPage.cs"

if errorlevel 1 (
  echo.
  echo [错误] 编译失败，请检查上方错误信息。
  pause
  exit /b 1
)

echo.
echo 编译成功：%OUT%
for %%F in ("%OUT%") do echo 文件大小：%%~zF 字节
echo.
echo 双击该 EXE 即可运行（原生窗口，不加载 HTML）。
echo 如需重新构建，重跑本脚本即可。
pause
