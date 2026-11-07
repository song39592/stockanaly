# 股票池追踪系统 · 环境安装脚本（PowerShell 版）
#
# INTEGRITY: STOCKPOOL_ENV_INSTALL_V2
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File launcher\install_env.ps1 -Root "<项目根目录>" [-Silent]
#
# 退出码：0 成功 / 1 根目录不对（项目没拷全） / 2 本机没有可用的 Python 3
#         3 创建虚拟环境失败 / 4 装依赖失败 / 5 依赖校验失败（多半被安全软件拦了 DLL）
#
# ---------------------------------------------------------------------------
# 为什么不用 .bat（跨设备踩过的坑）
#
#   cmd.exe 解析 .bat 有三个「换个设备就炸」的坑：
#     1) 行尾：.bat 被拷贝 / 压缩 / 网盘传成 LF-only 后，if/else 括号块整体错位，
#        报「xxx 不是内部或外部命令」「was unexpected at this time」；
#     2) 编码：cmd 按控制台代码页读脚本，UTF-8 中文在 936 机器上字节对齐被打乱，
#        同样把语法拆坏；
#     3) 括号块：块里任何一行带特殊字符，都可能让整块解析失败。
#
#   PowerShell 三样都不吃：行尾无关、脚本带 UTF-8 BOM 就按 UTF-8 读、语法自解释。
#   本脚本由启动器执行；启动器还会校验上面的 INTEGRITY 标记，并统一补上 UTF-8 BOM
#   （Windows PowerShell 5.1 没有 BOM 会按系统 ANSI 代码页读脚本）。
# ---------------------------------------------------------------------------

[CmdletBinding()]
param(
    [string]$Root = '',
    [switch]$Silent
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$OutputEncoding = [Text.Encoding]::UTF8

# 子进程（pip / python）一律走 UTF-8：
#   requirements.txt 是 UTF-8 的，而 pip 读 -r 文件用的是 **locale 编码**，
#   中文 Windows（GBK）上会直接 UnicodeDecodeError 崩掉 —— 装依赖必失败。
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Say([string]$s) { Write-Output $s }

function Wait-Enter {
    if ($Silent) { return }
    try { $null = Read-Host '按回车继续' } catch { }
}

# 跑一条外部命令，只看退出码（输出全部丢弃）
function Test-Cmd {
    param([string]$exe, [string[]]$argv)
    & $exe @argv *> $null
    return ($LASTEXITCODE -eq 0)
}

# 项目里现有的虚拟环境解释器（三种布局都认）
function Get-VenvPy([string]$backendDir) {
    foreach ($rel in @('.venv\Scripts\python.exe', '.venv\python.exe', 'venv\Scripts\python.exe')) {
        $p = Join-Path $backendDir $rel
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

# venv 是不是「本机能用」的：
#   venv 里只有启动器，真正的解释器和 DLL 在 pyvenv.cfg 登记的底座 Python 目录下。
#   底座路径不在本机 → 这是从别的电脑拷来的 venv，pandas 的 _libs 等 .pyd 会加载失败。
function Test-VenvPortable([string]$venvPy) {
    $venvRoot = Split-Path -Parent $venvPy
    if ((Split-Path -Leaf $venvRoot) -eq 'Scripts') { $venvRoot = Split-Path -Parent $venvRoot }
    $cfg = Join-Path $venvRoot 'pyvenv.cfg'
    if (-not (Test-Path -LiteralPath $cfg)) { return $true }     # 不是标准 venv，不判失败
    $basePy = $null
    foreach ($line in @(Get-Content -LiteralPath $cfg -ErrorAction SilentlyContinue)) {
        if ($line -match '^\s*home\s*=\s*(.+?)\s*$') { $basePy = $matches[1]; break }
    }
    if (-not $basePy) { return $true }
    if (-not (Test-Path -LiteralPath (Join-Path $basePy 'python.exe'))) { return $false }
    return $true
}

# ---------------------------------------------------------------------------
# 1. 定位项目根目录
# ---------------------------------------------------------------------------
$rootDir = $Root
if ([string]::IsNullOrWhiteSpace($rootDir)) {
    $rootDir = Split-Path -Parent $PSScriptRoot          # launcher\.. → 项目根
}
try {
    $rootDir = (Resolve-Path -LiteralPath $rootDir -ErrorAction Stop).Path
} catch {
    # 路径不存在就按原样用，后面会报「项目根目录不对」
}
$rootDir = $rootDir.TrimEnd('\')
$backendDir = Join-Path $rootDir 'backend_fastapi'
$dshDir = Join-Path $rootDir 'agent_dsh'

Say '==== 股票池追踪系统 - 环境安装 ===='
Say "项目根目录：$rootDir"

$reqFile = Join-Path $backendDir 'requirements.txt'
if (-not (Test-Path -LiteralPath $reqFile)) {
    Say "[FATAL] 找不到 $reqFile，项目根目录不对。"
    Say '        请确认整个项目目录都拷全了（launcher / backend_fastapi / agent_dsh）。'
    Wait-Enter
    exit 1
}

# ---------------------------------------------------------------------------
# 2. 找 Python：已有虚拟环境就复用，否则按 系统python / py / codex 顺序找；
#    都没有则自动下载免安装版（embeddable）
# ---------------------------------------------------------------------------
$venvPy = Get-VenvPy $backendDir
$venvReady = $false

if ($venvPy) {
    Say "[1/4] 检查已有虚拟环境：$venvPy"
    if (Test-Cmd -exe $venvPy -argv @('-m', 'pip', '--version')) {
        if (Test-VenvPortable $venvPy) {
            Say "        复用：$venvPy"
            $venvReady = $true
        } else {
            Say '[WARN] 已有虚拟环境来自另一台电脑：pyvenv.cfg 里登记的底座 Python 不在本机。'
            Say '       venv 不能跨机器拷贝（pandas 等包的 DLL 会加载失败），删除后重建。'
        }
    } else {
        Say "[WARN] 已有虚拟环境不可用（没有 pip，可能是旧版/损坏），删除后重建：$venvPy"
    }
    if (-not $venvReady) {
        foreach ($d in @((Join-Path $backendDir '.venv'), (Join-Path $backendDir 'venv'))) {
            if (Test-Path -LiteralPath $d) {
                Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        $venvPy = $null
    }
}

$bootstrapExe = $null
$bootstrapPre = @()

if (-not $venvReady) {
    $cmd = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd -and (Test-Cmd -exe $cmd.Source -argv @('-c', 'import sys'))) {
        $bootstrapExe = $cmd.Source
    }
    if (-not $bootstrapExe) {
        $cmd = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cmd -and (Test-Cmd -exe $cmd.Source -argv @('-3', '-c', 'import sys'))) {
            $bootstrapExe = $cmd.Source
            $bootstrapPre = @('-3')
        }
    }
    if (-not $bootstrapExe) {
        $codexPy = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (Test-Path -LiteralPath $codexPy) {
            $bootstrapExe = $codexPy
            $bootstrapPre = @()
        }
    }
}

# 2.5 本机没有任何 Python：自动下载免安装版（embeddable），装到 backend_fastapi\.venv\
if (-not $venvReady -and -not $bootstrapExe) {
    Say '[1/4] 本机没有 Python，自动下载免安装版（embeddable，约 10MB，无需权限）...'
    $pyVer = '3.11.9'
    $target = Join-Path $backendDir '.venv'
    $zip = Join-Path $env:TEMP "stockpool_python_$pyVer.zip"
    $url = "https://www.python.org/ftp/python/$pyVer/python-$pyVer-embed-amd64.zip"
    if (-not (Test-Path -LiteralPath $target)) {
        New-Item -ItemType Directory -Force -Path $target | Out-Null
    }
    Say "       下载 $url ..."
    try { (New-Object System.Net.WebClient).DownloadFile($url, $zip) } catch { }
    if (-not (Test-Path -LiteralPath $zip)) {
        $url = "https://mirrors.tuna.tsinghua.edu.cn/python/$pyVer/python-$pyVer-embed-amd64.zip"
        Say '       默认源失败，换清华镜像...'
        try { (New-Object System.Net.WebClient).DownloadFile($url, $zip) } catch { }
    }
    if (-not (Test-Path -LiteralPath $zip)) {
        Say '[WARN] 自动下载 Python 失败（可能没联网 / 被代理或杀毒拦）。'
        Wait-Enter
        exit 2
    }
    Say "       解压到 $target"
    try { Expand-Archive -Force -Path $zip -DestinationPath $target } catch {
        Say "[WARN] 解压失败：$($_.Exception.Message)"
        Wait-Enter
        exit 2
    }
    # embeddable 默认把 site 注释掉，启用它才能 import 第三方包
    $pth = Join-Path $target 'python311._pth'
    if (Test-Path -LiteralPath $pth) {
        try {
            (Get-Content -LiteralPath $pth) -replace '#import site', 'import site' |
                Set-Content -LiteralPath $pth -Encoding ASCII
        } catch { }
    }
    Say '       下载并安装 pip（get-pip.py）...'
    $getPip = Join-Path $target 'get-pip.py'
    try { (New-Object System.Net.WebClient).DownloadFile('https://bootstrap.pypa.io/get-pip.py', $getPip) } catch { }
    if (-not (Test-Path -LiteralPath $getPip)) {
        Say '[WARN] 下载 get-pip.py 失败。'
        Wait-Enter
        exit 2
    }
    $embedPy = Join-Path $target 'python.exe'
    & $embedPy $getPip --no-warn-script-location *> $null
    if ($LASTEXITCODE -ne 0) {
        Say '[WARN] 安装 pip 失败。'
        Wait-Enter
        exit 2
    }
    if (-not (Test-Path -LiteralPath $embedPy)) {
        Say '[WARN] embeddable Python 装配后仍未找到 python.exe。'
        Wait-Enter
        exit 2
    }
    Say "       embeddable Python 就绪：$embedPy"
    $venvPy = $embedPy
    $venvReady = $true
}

if (-not $venvReady) {
    if (-not $bootstrapExe) {
        Say '[FATAL] 没有找到可用的 Python 3，且自动下载免安装版失败，装不下去。'
        Say '        请先手动安装 Python 3.9 及以上版本（安装时勾选 Add python.exe to PATH）：'
        Say '        https://www.python.org/downloads/'
        Say '        或检查网络 / 代理后重试（自动下载需要联网）。'
        Wait-Enter
        exit 2
    }
    # 2.6 用系统 Python 现场建虚拟环境
    $ver = @(& $bootstrapExe @bootstrapPre -c 'import sys; print(sys.version.split()[0])' 2>$null)
    Say "[1/4] 用系统 Python 创建虚拟环境：$bootstrapExe $($ver -join ' ')"
    if (-not (Test-Cmd -exe $bootstrapExe -argv (@($bootstrapPre) + @('-c', 'import venv')))) {
        Say "[FATAL] 这个 Python 没有 venv 模块，没法创建虚拟环境：$bootstrapExe"
        Wait-Enter
        exit 3
    }
    $venvDir = Join-Path $backendDir '.venv'
    & $bootstrapExe @bootstrapPre -m venv $venvDir
    if ($LASTEXITCODE -ne 0) {
        Say '[FATAL] 创建虚拟环境 backend_fastapi\.venv 失败。'
        Wait-Enter
        exit 3
    }
    $venvPy = Join-Path $venvDir 'Scripts\python.exe'
}

# ---------------------------------------------------------------------------
# 3. 后端依赖
# ---------------------------------------------------------------------------
Say '[2/4] 安装后端依赖（requirements.txt）...'
& $venvPy -m pip install -r $reqFile --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    Say '       默认源失败，换清华镜像再试一次（只补这一次）...'
    & $venvPy -m pip install -r $reqFile -i https://pypi.tuna.tsinghua.edu.cn/simple --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) {
        Say '[FATAL] 依赖安装失败：默认源和清华镜像都失败。'
        Say '       常见原因：没联网 / 公司代理 / 杀毒拦截。可手动执行：'
        Say "       `"$venvPy`" -m pip install -r `"$reqFile`""
        Wait-Enter
        exit 4
    }
}

# ---------------------------------------------------------------------------
# 4. 校验依赖真的能导入
# ---------------------------------------------------------------------------
Say '[3/4] 校验依赖导入...'
$verifyLog = Join-Path $env:TEMP 'stockpool_env_verify.log'
& $venvPy -c 'import uvicorn, fastapi, akshare, pypdf, multipart, requests, bs4, dotenv' *> $verifyLog
if ($LASTEXITCODE -eq 0) {
    Say '       后端依赖校验通过。'
} else {
    foreach ($l in @(Get-Content -LiteralPath $verifyLog -ErrorAction SilentlyContinue)) { Say $l }
    Say ''
    # 依赖已装齐却导不进来，绝大多数是本机安全策略拦了 pandas 的 .pyd（不是装漏了）。
    $blocked = Select-String -LiteralPath $verifyLog -Pattern '应用程序控制策略', 'Application Control', 'DLL load failed', '无法加载' -Quiet -ErrorAction SilentlyContinue
    if ($blocked) {
        Say '[FATAL] 依赖已经装好，但 Windows 阻止了 pandas 的 DLL 加载。'
        Say '        典型报错：DLL load failed while importing _libs: 应用程序控制策略已阻止此文件'
        Say ''
        Say '        这不是依赖没装全，请按顺序处理：'
        Say '        1) Win11「智能应用控制」：设置 > 隐私和安全性 > Windows 安全中心 >'
        Say '           应用和浏览器控制 > 智能应用控制 > 关闭（需重启；关闭后不可再开）'
        Say '        2) 杀毒 / 安全软件（360、火绒、电脑管家）的应用锁或驱动防护：'
        Say '           把项目目录加入信任区，或临时退出后重跑本脚本'
        Say '        3) 虚拟环境是从别的电脑拷来的：删除 backend_fastapi\venv 与'
        Say '           backend_fastapi\.venv 后重跑本脚本，由本机 Python 现场重建'
        Wait-Enter
        exit 5
    }
    if (Select-String -LiteralPath $verifyLog -Pattern 'No module named' -Quiet -ErrorAction SilentlyContinue) {
        Say '[HINT] 依赖确实没装全：请检查网络 / 代理 / pip 源后重跑本脚本。'
    }
    Say '[FATAL] 依赖装完还是导不进来，请看上面的导入报错。'
    Wait-Enter
    exit 5
}

# ---------------------------------------------------------------------------
# 5. AI 服务依赖（可选：失败只警告，后端和网页不受影响）
# ---------------------------------------------------------------------------
Say '[4/4] 检查 AI 服务依赖（可选）...'
$nodeCmd = Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $nodeCmd) {
    Say '[WARN] 没有找到 Node.js：AI 服务（:3080）起不来，后端和网页不受影响。'
} elseif (Test-Path -LiteralPath (Join-Path $dshDir 'node_modules\@deepseek-ai')) {
    Say '       AI 服务依赖已就绪。'
} elseif (-not (Test-Path -LiteralPath $dshDir)) {
    Say "[WARN] 找不到 AI 服务目录：$dshDir（不影响后端与网页）。"
} else {
    Say '       AI 服务依赖缺失，开始 npm install（可能要几分钟）...'
    Push-Location $dshDir
    try { & npm install --no-audit --no-fund } catch { }
    Pop-Location
    if ($LASTEXITCODE -ne 0) {
        Say '[WARN] npm install 失败：AI 服务（:3080）起不来，后端和网页不受影响。'
    }
}

Say 'ENV_OK 环境安装完成。'
Wait-Enter
exit 0
