# 股票池追踪系统 · 环境安装脚本（PowerShell 版）
#
# INTEGRITY: STOCKPOOL_ENV_INSTALL_V2
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File launcher\install_env.ps1 -Root "<项目根目录>" [-Silent] [-ConfigOnly] [-RuntimeOnly]
#   -ConfigOnly：只跑「跨设备配置自愈」，不装依赖、不联网、不校验依赖。
#   -RuntimeOnly：只准备 AI 服务运行时（Node.js + npm 依赖），不装 / 不校验后端依赖。
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
    [switch]$Silent,
    [switch]$ConfigOnly,
    [switch]$RuntimeOnly
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
# 便携 Node.js：换设备时自动装，免安装器、免管理员权限
#
# 为什么不用 msi：msi 要管理员权限，静默装也常被安全软件拦；官方 zip 版解压即用，
#   不写系统 PATH、不动注册表 → 目录随项目走，换设备时一起拷过去。
# 装不上怎么办：只警告、不阻断 —— AI 服务（:3080）是可选功能，后端与网页不受影响。
# ---------------------------------------------------------------------------

# 项目内便携 Node 的目录：agent_dsh\.node\（唯一用途就是跑 dsh，放它旁边最内聚）
function Get-PortableNodeDir([string]$dshDir) { return (Join-Path $dshDir '.node') }

function Get-PortableNode([string]$dshDir) {
    $exe = Join-Path (Get-PortableNodeDir $dshDir) 'node.exe'
    if (Test-Path -LiteralPath $exe) { return $exe }
    return $null
}

# 结果用脚本级变量传，不用返回值 ——
# ⚠ PowerShell 里 `$x = Some-Function` 会把函数内 Write-Output 的**日志**一起吞进 $x，
#   所以「有日志的函数」一律不靠返回值传结果。
$script:AiNodeExe = $null

function Install-PortableNode([string]$dshDir) {
    $dir = Get-PortableNodeDir $dshDir
    $existing = Get-PortableNode $dshDir
    if ($existing) {
        Say "       项目内已有便携 Node：$existing"
        $script:AiNodeExe = $existing
        return
    }

    Say '       没有找到 Node.js，自动下载便携版（Windows zip，约 30MB，无需安装器与管理员权限）...'
    # 版本号从官方 index.json 取（不写死版本 → 不用跟着 Node 发版改脚本）
    $ver = $null
    try {
        $idx = Invoke-RestMethod -Uri 'https://nodejs.org/dist/index.json' -TimeoutSec 90
        $ver = ($idx | Where-Object { $_.version -match '^v22\.' } | Select-Object -First 1).version
    } catch { }
    if (-not $ver) {
        Say '       [WARN] 取不到 Node 版本列表（没联网 / 被代理拦），跳过自动安装。'
        Say '              AI 服务（:3080）不可用；后端与网页不受影响。'
        return
    }

    $file = "node-$ver-win-x64.zip"
    $zip = Join-Path $env:TEMP "stockpool_node_$ver.zip"
    $urls = @(
        "https://nodejs.org/dist/$ver/$file",
        "https://cdn.npmmirror.com/binaries/node/$ver/$file"
    )
    foreach ($u in $urls) {
        Say "       下载 $u ..."
        try { (New-Object System.Net.WebClient).DownloadFile($u, $zip) } catch { }
        if (Test-Path -LiteralPath $zip) { break }
    }
    if (-not (Test-Path -LiteralPath $zip)) {
        Say '       [WARN] 下载 Node.js 失败（没联网 / 被代理或杀毒拦）。'
        Say '              AI 服务（:3080）不可用；后端与网页不受影响。'
        Say '              手动装法：下载 Windows zip 版（不是 msi，msi 要管理员权限）解压到任意目录，'
        Say '                        再在设置页「Node 路径」填 node.exe。'
        return
    }

    $tmp = Join-Path $env:TEMP ('stockpool_node_x_' + [Guid]::NewGuid().ToString('N'))
    try {
        Expand-Archive -Force -Path $zip -DestinationPath $tmp
        $inner = Get-ChildItem -LiteralPath $tmp -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $inner) { throw '解压结果里没有目录' }
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        foreach ($item in (Get-ChildItem -LiteralPath $inner.FullName -Force)) {
            Move-Item -LiteralPath $item.FullName -Destination $dir -Force -ErrorAction SilentlyContinue
        }
    } catch {
        Say "       [WARN] 解压 Node.js 失败：$($_.Exception.Message)"
        return
    } finally {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }

    $exe = Get-PortableNode $dshDir
    if ($exe) {
        Say "       便携 Node 就绪：$exe"
        $script:AiNodeExe = $exe
    } else {
        Say '       [WARN] 便携 Node 装好后仍没找到 node.exe。'
    }
}

# AI 服务运行时：node（项目内便携版优先 → 系统 PATH → 自动装便携版）+ npm 依赖。
# 任何一步失败都只警告、不阻断：AI 服务是可选功能。
function Prepare-AiRuntime([string]$dshDir) {
    Say '[4/4] 准备 AI 服务运行时（可选）...'

    $nodeExe = $null
    $npmCmd = $null
    $portable = Get-PortableNode $dshDir
    if ($portable) {
        $nodeExe = $portable
        $npmCmd = Join-Path (Split-Path -Parent $portable) 'npm.cmd'
        Say "       使用项目内便携 Node：$nodeExe"
    } else {
        $cmd = Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cmd) {
            $nodeExe = $cmd.Source
            $npm = Get-Command npm -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($npm) { $npmCmd = $npm.Source }
            Say "       使用系统 Node：$nodeExe"
        }
    }

    if (-not $nodeExe) {
        $script:AiNodeExe = $null
        Install-PortableNode -dshDir $dshDir
        $nodeExe = $script:AiNodeExe
        if ($nodeExe) { $npmCmd = Join-Path (Split-Path -Parent $nodeExe) 'npm.cmd' }
    }

    if (-not $nodeExe) {
        Say '[WARN] 没有可用的 Node.js：AI 服务（:3080）起不来，后端和网页不受影响。'
        return
    }
    if (-not (Test-Path -LiteralPath $dshDir)) {
        Say "[WARN] 找不到 AI 服务目录：$dshDir（不影响后端与网页）。"
        return
    }
    if (Test-Path -LiteralPath (Join-Path $dshDir 'node_modules\@deepseek-ai')) {
        Say '       AI 服务依赖已就绪。'
        return
    }

    Say '       AI 服务依赖缺失，开始 npm install（可能要几分钟）...'
    if (-not $npmCmd -or -not (Test-Path -LiteralPath $npmCmd)) {
        Say '[WARN] 找不到 npm，无法安装 AI 服务依赖（AI 服务不可用，后端与网页不受影响）。'
        return
    }
    Push-Location $dshDir
    try { & $npmCmd install --no-audit --no-fund } catch { }
    Pop-Location
    if ($LASTEXITCODE -ne 0) {
        Say '[WARN] npm install 失败：AI 服务（:3080）起不来，后端和网页不受影响。'
        return
    }
    Say '       AI 服务依赖安装完成。'
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
# 1.5 跨设备配置自愈
#
# 整目录拷到新电脑后，配置里残留的「旧机器绝对路径」必须在拉起后端之前修掉，
# 否则会出现「依赖自检全过、后端却起不来」：
#   · .env 的 DATA_DIR 指向旧机器用户目录 → instance_lock.acquire() 建目录失败；
#   · .env 的 DATA_SECRET_SEALED 是旧机器 DPAPI 密封 → crypto.secret() 拒绝生成新密钥；
#   · .venv\python3x._pth 里的 '.' 只解析到 .venv 自身（不是工作目录），
#     少了 '..' 时 `-m uvicorn main:app` 报 Could not import module "main"；
#   · agent_dsh\cordis.runtime.yml 里的插件路径是旧机器的 file:/// 绝对 URL。
#
# 纪律：只改「有结论性判据」的项，判据不成立一律不动；动作幂等、可重复运行；
#       不打印 .env 里的任何值；不改 INTEGRITY 标记（旧启动器要能继续执行本脚本）。
# ---------------------------------------------------------------------------

function Get-DotenvValue {
    param([string]$envPath, [string]$key)
    if (-not (Test-Path -LiteralPath $envPath)) { return $null }
    foreach ($line in @(Get-Content -LiteralPath $envPath -Encoding UTF8 -ErrorAction SilentlyContinue)) {
        if ($line -match ('^\s*' + [regex]::Escape($key) + '\s*=\s*(.*)$')) {
            return $matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

# 结论性判据：绝对路径形如 <盘>:\Users\<用户名>\...，且该用户主目录在本机不存在。
# 「数据目录本身还没创建」不算跨机器 —— 判据落在用户主目录，避免误伤用户主动配置。
function Test-ForeignUserPath {
    param([string]$value)
    if ([string]::IsNullOrWhiteSpace($value)) { return $false }
    $m = [regex]::Match($value, '^([A-Za-z]:[\\/]+Users[\\/]+[^\\/]+)', 'IgnoreCase')
    if (-not $m.Success) { return $false }
    $userHome = $m.Groups[1].Value
    if ($userHome -ieq $env:USERPROFILE) { return $false }
    return (-not (Test-Path -LiteralPath $userHome))
}

# 把指定键的行注释掉（保留原值可追溯；注释行不会被 dotenv / crypto._env_value 读到）。
function Disable-DotenvLine {
    param([string]$envPath, [string[]]$keys, [string]$reason)
    $out = @()
    $changed = $false
    foreach ($line in @(Get-Content -LiteralPath $envPath -Encoding UTF8)) {
        $hit = $false
        foreach ($k in $keys) {
            if ($line -match ('^\s*' + [regex]::Escape($k) + '\s*=')) { $hit = $true; break }
        }
        if ($hit) {
            $out += '# [cross-device] ' + $line
            $changed = $true
        } else {
            $out += $line
        }
    }
    if (-not $changed) { return $false }
    $out = @('# [cross-device] ' + $reason) + $out
    $tmp = $envPath + '.tmp'
    try {
        [System.IO.File]::WriteAllText($tmp, (($out -join "`r`n") + "`r`n"),
            (New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false))
        Move-Item -LiteralPath $tmp -Destination $envPath -Force
        return $true
    } catch {
        Say "[WARN] 改写 .env 失败：$($_.Exception.Message)"
        return $false
    }
}

# embeddable Python 的 ._pth：相对路径以 python.exe 所在目录（.venv）为基准，
# '.' = .venv，'..' = backend_fastapi。补 '..' 后 main / config 才能被 uvicorn 导入。
function Repair-EmbeddablePth {
    param([string]$backendDir)
    $venvRoot = Join-Path $backendDir '.venv'
    if (-not (Test-Path -LiteralPath (Join-Path $venvRoot 'python.exe'))) { return }
    $pthList = @(Get-ChildItem -LiteralPath $venvRoot -Filter 'python3*._pth' -File -ErrorAction SilentlyContinue)
    if ($pthList.Count -eq 0) { return }        # 标准 venv 没有 ._pth，不需要动
    foreach ($pth in $pthList) {
        $lines = @(Get-Content -LiteralPath $pth.FullName -Encoding UTF8)
        if ($lines -contains '..') {
            Say '       embeddable Python 的 sys.path 已含后端目录，跳过。'
            continue
        }
        $out = @()
        $inserted = $false
        foreach ($line in $lines) {
            $out += $line
            if (-not $inserted -and $line.Trim() -eq '.') {
                $out += '..'
                $inserted = $true
            }
        }
        if (-not $inserted) { $out += '..' }
        [System.IO.File]::WriteAllText($pth.FullName, (($out -join "`r`n") + "`r`n"),
            (New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false))
        Say "       已补齐 $($pth.Name)：追加 '..'（backend_fastapi 进入 sys.path）"
    }
}

# dsh 插件配置：权威生成器是 agent_dsh\generate-patch.mjs（按当前目录算路径）。
# 有 Node 就调它；没有 Node 时不复制第二份生成逻辑，只把「明确指向别的电脑」的
# 坏产物改名留档 —— 装好 Node 后启动器 EnsureDshRuntimePatch() 会自动重建。
function Repair-DshRuntime {
    param([string]$dshDir)
    $target = Join-Path $dshDir 'cordis.runtime.yml'
    $pluginsDir = Join-Path $dshDir 'plugins'
    if (-not (Test-Path -LiteralPath $pluginsDir)) { return }
    $expected = ([System.Uri]$pluginsDir).AbsoluteUri.TrimEnd('/')
    $needFix = $false
    $why = ''
    if (-not (Test-Path -LiteralPath $target)) {
        $needFix = $true
        $why = '产物不存在'
    } else {
        $text = Get-Content -LiteralPath $target -Raw -Encoding UTF8
        if ($text -match '__PLUGIN_BASE__') {
            $needFix = $true
            $why = '仍是模板占位符'
        } elseif ($text.IndexOf($expected, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            $needFix = $true
            $why = '插件路径不指向当前目录'
        }
    }
    if (-not $needFix) {
        Say '       dsh 插件路径已是当前目录，跳过。'
        return
    }
    $gen = Join-Path $dshDir 'generate-patch.mjs'
    $nodeCmd = Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $generated = $false
    if ($nodeCmd -and (Test-Path -LiteralPath $gen)) {
        try { $generated = Test-Cmd -exe $nodeCmd.Source -argv @($gen) } catch { $generated = $false }
    }
    if ($generated -and (Test-Path -LiteralPath $target)) {
        Say '       已用 generate-patch.mjs 按当前目录重建 cordis.runtime.yml。'
        return
    }
    if (Test-Path -LiteralPath $target) {
        Say "       [WARN] 未找到 Node.js，无法重建 dsh 插件配置（$why）。"
        Say '              现有文件指向另一台电脑的插件目录，改名留档；装好 Node 后启动器会自动重新生成。'
        Move-Item -LiteralPath $target -Destination ($target + '.foreign.bak') -Force -ErrorAction SilentlyContinue
    } else {
        Say "       [WARN] 未找到 Node.js，dsh 插件配置暂缺（$why）；装好 Node 后启动器会自动生成。"
    }
}

function Repair-CrossDeviceConfig {
    param([string]$backendDir, [string]$dshDir)
    Say '[1.5/4] 跨设备配置自愈（只修有结论性判据的旧机器残留）…'
    $envPath = Join-Path $backendDir '.env'
    if (Test-Path -LiteralPath $envPath) {
        $dataDirValue = Get-DotenvValue -envPath $envPath -key 'DATA_DIR'
        if (Test-ForeignUserPath -value $dataDirValue) {
            Say '       .env 的数据目录指向另一台电脑的用户目录（该用户主目录不存在）。'
            Say '       动作：注释 DATA_DIR（回退默认目录）与 DATA_SECRET_SEALED（旧机器 DPAPI 密钥），'
            Say '             原行保留在注释中便于追溯；后端会在本机重新生成校验密钥。'
            $keysToDisable = @('DATA_DIR', 'DATA_SECRET_SEALED')
            $fixReason = '由 install_env.ps1 自动修复：跨机器残留配置，原行保留在下方注释中'
            if (Disable-DotenvLine -envPath $envPath -keys $keysToDisable -reason $fixReason) {
                Say '       已修复 .env。'
            } else {
                Say '       .env 未改动（没有命中的行或改写失败）。'
            }
        } else {
            Say '       数据目录配置正常（或不是跨机器残留），未改动。'
        }
    } else {
        Say '       没有 .env（首次运行），跳过。'
    }
    Repair-EmbeddablePth -backendDir $backendDir
    Repair-DshRuntime -dshDir $dshDir
}

if (-not $RuntimeOnly) {
    Repair-CrossDeviceConfig -backendDir $backendDir -dshDir $dshDir
}

if ($ConfigOnly) {
    Say 'CONFIG_OK 配置自愈完成（-ConfigOnly：未安装 / 校验依赖）。'
    Wait-Enter
    exit 0
}

if ($RuntimeOnly) {
    # 只准备 AI 服务运行时（Node.js + npm 依赖）：不装 / 不校验后端依赖。
    # 失败只警告、不阻断 —— AI 服务是可选功能，后端与网页不受影响。
    Prepare-AiRuntime -dshDir $dshDir
    Say 'RUNTIME_OK AI 服务运行时准备完成（-RuntimeOnly：未安装 / 校验后端依赖）。'
    Wait-Enter
    exit 0
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
    # 2.5 分支刚用 Set-Content 重写过 ._pth，而 1.5 段跑在解压之前、补不到这个文件。
    # 这里再补一次，保证「首次自动下载 Python」这条路径同样能把 backend_fastapi 放进 sys.path。
    Repair-EmbeddablePth -backendDir $backendDir
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
Say '[3/4] 校验依赖...'

# ---- 3.1 逐包核对 requirements.txt（**单一来源**：backend_fastapi\check_deps.py）----
# ⚠️ 别再手抄 import 清单：过去 launcher 与本脚本各抄了一份，两份**都漏过 pypinyin**
#    （11-05 才加进 requirements.txt，venv 却是 9-15 建的）—— 于是缺它时两处都判定
#    「装齐了」→ 永远不装 → 拼音搜索整片失效，而界面/后端/健康检查全都正常，无任何报错。
#    脚本不存在 = 目标目录是老版本 → 跳过这项，不阻塞安装。
$depCheck = Join-Path $backendDir 'check_deps.py'
if (Test-Path -LiteralPath $depCheck) {
    $depJson = (& $venvPy $depCheck --json 2>&1 | Out-String)
    $dep = $null
    try { $dep = $depJson | ConvertFrom-Json } catch { $dep = $null }
    if ($dep -and $dep.missing -and $dep.missing.Count -gt 0) {
        Say '       依赖没装齐（按 requirements.txt 逐包核对）：'
        foreach ($m in $dep.missing) { Say "         $m" }
        Say ''
        Say '[FATAL] 装完仍缺依赖，请检查网络 / 代理 / pip 源后重跑本脚本。'
        Say "       也可手动执行：`"$venvPy`" -m pip install -r `"$reqFile`""
        Wait-Enter
        exit 5
    }
    Say '       依赖逐包核对通过。'
    # ⚠️ **可选依赖缺失**（requirements.txt 里标了 `# optional`）：只让对应功能降级，
    #    不阻断 —— 离线设备上不能因为一个可选库把整个程序卡死。只提示。
    if ($dep -and $dep.optional_missing -and $dep.optional_missing.Count -gt 0) {
        foreach ($m in $dep.optional_missing) {
            Say "         可选依赖缺失：$m（不影响其它功能；如需修复可重跑本脚本）"
        }
    }
    # 版本不符**只提示、不阻塞**：越界的版本多半还能跑，贸然降级可能打断正在运行的任务
    if ($dep -and $dep.mismatch -and $dep.mismatch.Count -gt 0) {
        foreach ($m in $dep.mismatch) {
            Say "         版本不符：$($m.name) 装了 $($m.installed)（要求 $($m.spec)）"
        }
    }
}

# ---- 3.2 import 校验：装齐了**能不能真的用起来**（如 pandas 的 .pyd 被系统策略拦住）----
$verifyLog = Join-Path $env:TEMP 'stockpool_env_verify.log'
& $venvPy -c 'import uvicorn, fastapi, akshare, pypdf, multipart, requests, bs4, dotenv' *> $verifyLog
if ($LASTEXITCODE -eq 0) {
    Say '       后端依赖导入校验通过。'
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
Prepare-AiRuntime -dshDir $dshDir

Say 'ENV_OK 环境安装完成。'
Wait-Enter
exit 0
