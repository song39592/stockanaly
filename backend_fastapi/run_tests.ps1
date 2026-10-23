# 一键跑全部后端单元测试（仅标准库 unittest，零新依赖）。
# 显式列出 9 个测试模块，避免 `unittest discover` 递归进 venv / 第三方包目录。
# 用法：在 backend_fastapi 目录下执行  .\run_tests.ps1
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

# test_support 是测试基建（helper，无 TestCase），不列入。
$modules = @(
    'test_price_store',
    'test_history_store',
    'test_instance_lock',
    'test_integrity',
    'test_mentor_store',
    'test_market_symbol',
    'test_main',
    'test_settings_download'
)

# 直接调用可执行字面路径（不绕 & 变量），PowerShell 原生执行、退出码可靠。
# 注意：unittest 把结果写到 stderr，勿设 $ErrorActionPreference='Stop'，否则会被误判为错误。
if (Test-Path .\venv\Scripts\python.exe) {
    .\venv\Scripts\python.exe -m unittest $modules -v
} else {
    python -m unittest $modules -v
}
exit $LASTEXITCODE
