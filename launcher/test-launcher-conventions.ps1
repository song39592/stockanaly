# INTEGRITY: STOCKPOOL_LAUNCHER_CONVENTIONS_V1
<#
    启动器约定静态自检：扫描 launcher\*.cs，挡住「把控件文本当传参通道」这类隐性回归。

    背景（2026-11-10 实测 Bug）：GridKit.OpenStock 走的是
        _quickSearch.Text = code  ->  SelectTab(...)  ->  StockOpen() 靠「再读一次 Text」取值
    而 SelectTab 内部会调 HideQuickSearch()，后者把文本清空
    -> StockOpen() 读到空串，6 位代码退化成「指标名」-> 股票不换、界面不跳，且**不报错**。

    用法：powershell -File launcher\test-launcher-conventions.ps1
    退出码：0 = 通过；1 = 有违规（逐条列出 文件:行号）。
    说明：R2 是「同文件行序」近似判定，跨方法调用靠 R1 一并兜住。
#>
param([string]$LauncherDir = $PSScriptRoot)

$ErrorActionPreference = 'Stop'
$violations = New-Object System.Collections.Generic.List[string]

function Add-Violation([string]$file, [int]$line, [string]$rule, [string]$text) {
    $rel = [IO.Path]::GetFileName($file)
    $violations.Add(("{0}:{1}  [{2}] {3}" -f $rel, $line, $rule, $text.Trim()))
}

function Get-Indent([string]$line) {
    return ($line -replace '^(\s*).*', '$1').Length
}

$files = Get-ChildItem -LiteralPath $LauncherDir -Filter *.cs -File | Sort-Object Name

foreach ($file in $files) {
    $lines = Get-Content -LiteralPath $file.FullName -Encoding UTF8

    for ($i = 0; $i -lt $lines.Count; $i++) {
        $trim = $lines[$i].Trim()
        if ($trim.StartsWith('//') -or $trim.StartsWith('*')) { continue }   # 注释行不参与检查

        # R1：StockOpen 必须显式传参；不允许无参 / 传 null（null = 又要从输入框取码）
        if ($trim -match 'StockOpen\(\s*(null)?\s*\)' -and $trim -notmatch 'void\s+StockOpen') {
            Add-Violation $file.FullName ($i + 1) 'R1 显式传参' $lines[$i]
        }

        # R4：不允许残留临时诊断标记
        if ($trim -match '待清理|临时诊断|\[DBG\]|\[QS\]|\[TAB\]') {
            Add-Violation $file.FullName ($i + 1) 'R4 无诊断残留' $lines[$i]
        }
    }

    # R2：同一文件内，禁止「先给输入框文本赋值、后切页」（切页会收起并清空输入框）
    $lastAssign = -1
    $lastSelect = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $trim = $lines[$i].Trim()
        if ($trim.StartsWith('//') -or $trim.StartsWith('*')) { continue }
        if ($trim -match '_quickSearch\.Text\s*=') { $lastAssign = $i }
        if ($trim -match 'SelectTab\s*\(') {
            if ($lastAssign -ge 0 -and $lastAssign -gt $lastSelect) {
                Add-Violation $file.FullName ($i + 1) 'R2 赋值先于切页' $lines[$i]
            }
            $lastSelect = $i
        }
    }

    # R3：HideQuickSearch 内禁止清空文本（清空只放在 ShowQuickSearch —— 每次唤出是新输入）
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match 'void\s+HideQuickSearch\s*\(') {
            $indent = Get-Indent $lines[$i]
            for ($j = $i + 1; $j -lt $lines.Count; $j++) {
                $t = $lines[$j]
                if ($t -match '^\s*\}\s*$' -and (Get-Indent $t) -le $indent) { break }
                if ($t -match '_quickSearch\.Text\s*=') {
                    Add-Violation $file.FullName ($j + 1) 'R3 隐藏不等于清空' $t
                }
            }
            break
        }
    }
}

if ($violations.Count -gt 0) {
    Write-Output ("FAIL: {0} 处违反启动器约定：" -f $violations.Count)
    foreach ($v in $violations) { Write-Output ("  - " + $v) }
    Write-Output '详见 .codebuddy/rules/launcher-ux.md「控件文本不是数据通道」。'
    exit 1
}

Write-Output ("PASS: {0} 个 launcher 源文件符合启动器约定（4 条规则）。" -f $files.Count)
exit 0
