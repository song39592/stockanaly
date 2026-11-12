# INTEGRITY: STOCKPOOL_CHECK_DOCS_V1
# 文档一致性自检（退出码 0 = 通过，1 = 有硬错误）
#   [FAIL] Markdown 链接目标不存在
#   [FAIL] /api/... 路径不在任何已声明的路由前缀下
#   [WARN] 反引号里提到的仓库路径不存在（可能是描述历史 / 已删除，需人工确认）
#   [WARN] 规则区字符量超预算
# 语义是否准确仍要靠人 —— 见 .codebuddy/rules/docs-sync.md

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$failures = New-Object System.Collections.Generic.List[string]
$warns = New-Object System.Collections.Generic.List[string]

function Add-Fail { param([string]$m) $script:failures.Add($m) }
function Add-Warn { param([string]$m) $script:warns.Add($m) }

$skipDirs = @('venv', 'node_modules', '__pycache__', '.git', 'site-packages', 'dist', 'build')
$utf8 = New-Object System.Text.UTF8Encoding($false)
$sep = [string][char]92

function Read-Text {
    param([string]$Path)
    try { return [System.IO.File]::ReadAllText($Path, $script:utf8) } catch { return '' }
}

function Test-SkipDir {
    param([string]$Full)
    foreach ($s in $script:skipDirs) {
        if ($Full.Contains($script:sep + $s + $script:sep)) { return $true }
        if ($Full.EndsWith($script:sep + $s)) { return $true }
    }
    return $false
}

# ---------------------------------------------------------------- 索引仓库路径
$repoPaths = New-Object System.Collections.Generic.List[string]

function Add-Tree {
    param([string]$Dir)
    if (-not (Test-Path $Dir)) { return }
    foreach ($f in Get-ChildItem $Dir -Recurse -File -Force -ErrorAction SilentlyContinue) {
        if (Test-SkipDir $f.FullName) { continue }
        $script:repoPaths.Add($f.FullName.Substring($script:root.Length + 1).Replace($script:sep, '/'))
    }
    foreach ($d in Get-ChildItem $Dir -Recurse -Directory -Force -ErrorAction SilentlyContinue) {
        if (Test-SkipDir $d.FullName) { continue }
        $script:repoPaths.Add($d.FullName.Substring($script:root.Length + 1).Replace($script:sep, '/') + '/')
    }
}

foreach ($d in @('launcher', 'backend_fastapi', 'frontend', 'agent_dsh', 'docs', 'scripts', '.codebuddy')) {
    $abs = Join-Path $root $d
    if (-not (Test-Path $abs)) { continue }
    $repoPaths.Add($d + '/')          # 顶层目录本身也是合法路径
    Add-Tree $abs
}
foreach ($f in @('README.md', 'LICENSE', '.gitignore', 'stockpool.exe')) {
    if (Test-Path (Join-Path $root $f)) { $repoPaths.Add($f) }
}

# 运行期数据目录分区名 —— 不是仓库路径，跳过
$dataSegments = @('state', 'logs', 'cache', 'config', 'bars', 'chip')

# 只有首段属于仓库顶层（或仓库内的功能目录）时才当作路径校验，
# 否则 `WindowsApps/python.exe` 这类外部路径会刷屏
$pathRoots = @('launcher', 'backend_fastapi', 'frontend', 'agent_dsh', 'docs', 'scripts', '.codebuddy',
    'core', 'features', 'indicators', 'strategies', 'chipformulas', 'formulas',
    'plugins', 'skills_library', 'shared', 'home', 'chip-scr', 'mentor-lab', 'rules')

function Expand-Tokens {
    param([string]$Token)
    $t = $Token.Trim().Replace($script:sep, '/')
    if ($t -match '\{([^}]*)\}') {
        $out = @()
        foreach ($alt in ($Matches[1] -split ',')) {
            $out += Expand-Tokens ($t -replace '\{[^}]*\}', $alt.Trim())
        }
        return $out
    }
    return @($t)
}

function Test-RepoPath {
    param([string]$Token)
    $t = $Token.Trim().Replace($script:sep, '/')
    if (-not $t) { return $true }
    $first = ($t -split '/')[0].ToLowerInvariant()
    if ($dataSegments -contains $first) { return $true }
    while ($t.StartsWith('../')) { $t = $t.Substring(3) }
    if ($t.StartsWith('/')) { $t = $t.TrimStart('/') }
    if (-not $t) { return $true }
    foreach ($p in $script:repoPaths) {
        if ($p.Equals($t, 'OrdinalIgnoreCase')) { return $true }
        if (-not $t.EndsWith('/') -and $p.EndsWith('/' + $t, 'OrdinalIgnoreCase')) { return $true }
        if ($t.EndsWith('/') -and $p.EndsWith($t, 'OrdinalIgnoreCase')) { return $true }
    }
    return $false
}

# ---------------------------------------------------------------- 收集要扫的 md
$mdFiles = New-Object System.Collections.Generic.List[string]

function Add-Md {
    param([string]$Path)
    if (Test-Path $Path -PathType Leaf) {
        $script:mdFiles.Add((Resolve-Path $Path).Path)
        return
    }
    if (-not (Test-Path $Path)) { return }
    foreach ($f in Get-ChildItem $Path -Recurse -File -Filter *.md -Force -ErrorAction SilentlyContinue) {
        if (Test-SkipDir $f.FullName) { continue }
        $rel = $f.FullName.Substring($script:root.Length + 1).Replace($script:sep, '/')
        if ($rel -eq 'docs/changelog.md') { continue }          # 历史流水
        if ($rel.StartsWith('docs/archive/')) { continue }     # 归档件
        $script:mdFiles.Add($f.FullName)
    }
}
Add-Md (Join-Path $root 'README.md')
foreach ($d in @('backend_fastapi', 'frontend', 'agent_dsh', 'docs', '.codebuddy')) { Add-Md (Join-Path $root $d) }

Write-Host "扫描文档：$($mdFiles.Count) 个" -ForegroundColor Cyan

# ---------------------------------------------------------------- 1 链接 / 2 路径
$linkRx = [regex]'\[[^\]]*\]\(([^)\s]+)'
$tickRx = [regex]'`([^`\r\n]+)`'
$extRx = '\.(py|cs|md|ps1|bat|json|yml|yaml|html|js|mjs|css|txt|exe|db|sql|lock|cfg|ini|log)$'

foreach ($md in ($mdFiles | Sort-Object -Unique)) {
    $text = Read-Text $md
    $relMd = $md.Substring($root.Length + 1).Replace($script:sep, '/')

    # 1) Markdown 链接 —— 链接必须能点开
    foreach ($m in $linkRx.Matches($text)) {
        $t = $m.Groups[1].Value
        if ($t -match '^(https?|mailto):') { continue }
        $t = ($t -split '#')[0]
        if (-not $t) { continue }
        $hit = $false
        foreach ($c in @((Join-Path (Split-Path -Parent $md) $t), (Join-Path $root $t))) {
            try { if (Test-Path -LiteralPath $c -ErrorAction Stop) { $hit = $true; break } } catch { }
        }
        if (-not $hit) { Add-Fail "$relMd : 链接目标不存在 -> $t" }
    }

    # 2) 反引号里的仓库路径 —— 仅告警（可能是描述历史或已删除文件）
    foreach ($m in $tickRx.Matches($text)) {
        $raw = $m.Groups[1].Value.Trim()
        if ($raw -match '[<>*?$|%]') { continue }
        if ($raw -match '^[A-Za-z]:') { continue }
        if ($raw -notmatch '[/\\]') { continue }
        if ($raw -match ' ') { continue }
        if (-not ($raw -match $extRx -or $raw -match '[/\\]$')) { continue }
        foreach ($tok in (Expand-Tokens $raw)) {
            $probe = $tok.Replace('\', '/')
            while ($probe.StartsWith('../')) { $probe = $probe.Substring(3) }
            $seg0 = ($probe -split '/')[0].ToLowerInvariant()
            if ($pathRoots -notcontains $seg0) { continue }    # 不是仓库路径，跳过
            if (-not (Test-RepoPath $tok)) {
                Add-Warn "$relMd : 提到的路径不存在 -> $tok"
                break
            }
        }
    }
}

# ---------------------------------------------------------------- 3 API 前缀
$prefixes = New-Object System.Collections.Generic.List[string]
foreach ($f in Get-ChildItem (Join-Path $root 'backend_fastapi') -Recurse -File -Filter *.py -ErrorAction SilentlyContinue) {
    if ($f.FullName -like "*$($script:sep)venv$($script:sep)*") { continue }
    foreach ($line in (Read-Text $f.FullName) -split "`n") {
        if ($line -match 'APIRouter\(\s*prefix\s*=\s*"([^"]+)"') { $prefixes.Add($Matches[1]) }
    }
}
$prefixes.Add('/api/bull')   # dsh Agent 的 REST 网关（agent_dsh/plugins/bull-http.mjs）
Write-Host "已知路由前缀：$($prefixes.Count) 个（含 dsh 的 /api/bull）" -ForegroundColor Cyan

$apiRx = [regex]'/api/[A-Za-z0-9_\-/]+'
foreach ($md in ($mdFiles | Sort-Object -Unique)) {
    $text = Read-Text $md
    $relMd = $md.Substring($root.Length + 1).Replace($script:sep, '/')
    foreach ($m in $apiRx.Matches($text)) {
        $p = $m.Value.TrimEnd('/')
        $ok = $false
        foreach ($pfx in $prefixes) {
            if ($p.Equals($pfx, 'OrdinalIgnoreCase')) { $ok = $true; break }
            if ($p.StartsWith($pfx + '/', 'OrdinalIgnoreCase')) { $ok = $true; break }
        }
        if (-not $ok) { Add-Fail "$relMd : API 路径不在任何已声明前缀下 -> $p" }
    }
}

# ---------------------------------------------------------------- 4 规则区预算
$ruleTotal = 0
foreach ($f in Get-ChildItem (Join-Path $root '.codebuddy/rules') -File -Filter *.md -ErrorAction SilentlyContinue) {
    $len = (Read-Text $f.FullName).Length
    $ruleTotal += $len
    if ($len -gt 8000) { Add-Warn "规则文件超单文件上限 8000 字符：$($f.Name) = $len" }
}
Write-Host "规则区字符总量：$ruleTotal / 28000" -ForegroundColor Cyan
if ($ruleTotal -gt 28000) { Add-Warn "规则区超总量上限 28000 字符（常驻加载，会稀释每条规则的权重）" }

# ---------------------------------------------------------------- 汇总
Write-Host ''
foreach ($w in $warns) { Write-Host "[WARN] $w" -ForegroundColor Yellow }
foreach ($f in $failures) { Write-Host "[FAIL] $f" -ForegroundColor Red }

if ($failures.Count -gt 0) {
    Write-Host ''
    Write-Host "文档自检未通过：$($failures.Count) 个硬错误，$($warns.Count) 个待确认项" -ForegroundColor Red
    exit 1
}
Write-Host ''
Write-Host "文档自检通过（$($warns.Count) 个待确认项）。" -ForegroundColor Green
exit 0
