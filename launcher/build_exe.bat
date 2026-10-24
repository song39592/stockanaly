@echo off
chcp 65001 >nul
rem ============================================================
rem  Stock Pool Tracker - native EXE build script
rem  Uses the built-in .NET Framework csc.exe compiler:
rem    Offline build, zero third-party deps, no need to install Visual Studio / SDK
rem  Output: <project root>\stockpool-tracker.exe
rem ============================================================

set "FW=C:\Windows\Microsoft.NET\Framework64\v4.0.30319\"
set "SRC=%~dp0StockPoolLauncher.cs"
set "OUT=%~dp0..\量化分析平台.exe"

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
  "%~dp0UiKit.cs" ^
  "%~dp0Cards.cs" ^
  "%~dp0ChartKit.cs" ^
  "%~dp0SubTabStrip.cs" ^
  "%~dp0AsyncKit.cs" ^
  "%~dp0RedBullPage.cs" ^
  "%~dp0MentorLabPage.cs" ^
  "%~dp0Http.cs" ^
  "%~dp0J.cs" ^
  "%~dp0Palette.cs" ^
  "%~dp0ValuationPage.cs" ^
  "%~dp0MarketPage.cs" ^
  "%~dp0StockPage.cs" ^
  "%~dp0StockPage.Profile.cs" ^
  "%~dp0StockPage.Records.cs" ^
  "%~dp0StockPage.Valuation.cs" ^
  "%~dp0StockPage.Saolei.cs" ^
  "%~dp0StockPage.AI.cs" ^
  "%~dp0StockPage.Overlays.cs" ^
  "%~dp0GridKit.cs" ^
  "%~dp0ChipRankPage.cs" ^
  "%~dp0RpsPoolPage.cs" ^
  "%~dp0DownloadPage.cs" ^
  "%~dp0StrategyPage.cs" ^
  "%~dp0IndicatorIndexPage.cs" ^
  "%~dp0QuickSearch.cs"

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
