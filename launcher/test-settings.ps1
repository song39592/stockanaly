param([string]$AssemblyPath = "$PSScriptRoot\build\股票池追踪系统.exe")
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$assembly = [Reflection.Assembly]::LoadFrom($AssemblyPath)
$type = $assembly.GetType('StockPool.MainForm', $true)
# 不创建窗口、不启动服务，只验证编译后启动器的离线配置读取逻辑。
$form = [Runtime.Serialization.FormatterServices]::GetUninitializedObject($type)
$flags = [Reflection.BindingFlags]'Instance,NonPublic'
$method = $type.GetMethod('ResolveDataDirSetting', $flags)
$testRoot = Join-Path $PSScriptRoot ('build\settings-test-' + [Guid]::NewGuid().ToString('N'))
$backend = Join-Path $testRoot 'backend_fastapi'
New-Item -ItemType Directory -Path $backend -Force | Out-Null
$type.GetField('_root', $flags).SetValue($form, $testRoot)
$savedData = $env:DATA_DIR
$savedStock = $env:STOCK_DATA_DIR
try {
    Remove-Item Env:DATA_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:STOCK_DATA_DIR -ErrorAction SilentlyContinue
    $cases = @(
        @{ Text = 'DATA_DIR=D:\stackdata'; Expected = 'D:\stackdata' },
        @{ Text = "DATA_DIR='D:\\行情 # data'"; Expected = 'D:\行情 # data' },
        @{ Text = "DATA_DIR=C:\one`nSTOCK_DATA_DIR=D:\two"; Expected = 'D:\two' },
        @{ Text = 'export DATA_DIR = D:\three'; Expected = 'D:\three' }
    )
    foreach ($case in $cases) {
        [IO.File]::WriteAllText((Join-Path $backend '.env'), $case.Text, [Text.Encoding]::UTF8)
        $actual = $method.Invoke($form, @())
        if ($actual -ne $case.Expected) { throw "Expected '$($case.Expected)', got '$actual'" }
    }
    $env:STOCK_DATA_DIR = 'D:\external'
    if ($method.Invoke($form, @()) -ne 'D:\external') { throw 'Environment override failed' }
    Write-Output 'PASS: 5 compiled launcher configuration cases (no UI or live data changes).'
}
finally {
    $env:DATA_DIR = $savedData
    $env:STOCK_DATA_DIR = $savedStock
}
