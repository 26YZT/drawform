<#
DWG 真机验证：DXF → DWG → DXF 走一圈，把过程原样记下来。

设计前提（重要）：AcCoreConsole 的命令行开关没有公开规范。我查到的 Autodesk 官方资料
只说明它能加载插件、能执行 script 做批处理。所以这个脚本**不赌开关**：
每种调用方式都把 stdout / stderr / 退出码 / 产物是否存在 记进 report\，
判定在我们这边用 back\ 里的 DXF 做。跑成功或跑失败都算结论，跑成什么样就记什么样。

只在本目录下建 out\ back\ report\，不动机器上任何其他东西。
#>
[CmdletBinding()]
param(
    [string]$ConsolePath = '',
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$sampleDir = Join-Path $PSScriptRoot 'samples'
$outDir    = Join-Path $PSScriptRoot 'out'
$backDir   = Join-Path $PSScriptRoot 'back'
$repDir    = Join-Path $PSScriptRoot 'report'
foreach ($d in @($outDir, $backDir, $repDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}

function Find-AcCoreConsole {
    if ($ConsolePath -and (Test-Path $ConsolePath)) { return $ConsolePath }
    $roots = @('C:\Program Files\Autodesk', 'C:\Program Files (x86)\Autodesk')
    $found = foreach ($root in $roots) {
        if (Test-Path $root) {
            Get-ChildItem -Path $root -Filter 'accoreconsole.exe' -Recurse -ErrorAction SilentlyContinue
        }
    }
    if (-not $found) { return $null }
    return ($found | Sort-Object FullName -Descending | Select-Object -First 1).FullName
}

# 调用一次并完整记录。喂一个换行再关掉 stdin：万一它在等交互输入，收到 EOF 就会退出，
# 而不是把脚本挂在这里。
function Invoke-Captured {
    param([string]$Exe, [string[]]$Args, [string]$Tag)

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = ($Args | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
    $psi.WorkingDirectory = $PSScriptRoot
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true

    $stdoutFile = Join-Path $repDir "$Tag.out.txt"
    $stderrFile = Join-Path $repDir "$Tag.err.txt"
    $metaFile   = Join-Path $repDir "$Tag.meta.txt"
    $started = Get-Date

    try {
        $proc = [System.Diagnostics.Process]::Start($psi)
        $proc.StandardInput.WriteLine('')
        $proc.StandardInput.Close()
        $out = $proc.StandardOutput.ReadToEnd()
        $err = $proc.StandardError.ReadToEnd()
        if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
            try { $proc.Kill() } catch { }
            $code = 'TIMEOUT'
        } else {
            $code = $proc.ExitCode
        }
    } catch {
        $out = ''; $err = $_.Exception.Message; $code = 'LAUNCH_FAILED'
    }

    Set-Content -Path $stdoutFile -Value $out -Encoding UTF8
    Set-Content -Path $stderrFile -Value $err -Encoding UTF8
    @(
        "exe     = $Exe"
        "args    = $($psi.Arguments)"
        "exit    = $code"
        "seconds = $([math]::Round(((Get-Date) - $started).TotalSeconds, 1))"
    ) | Set-Content -Path $metaFile -Encoding UTF8
    return $code
}

$exe = Find-AcCoreConsole
if (-not $exe) {
    '没找到 accoreconsole.exe。' | Set-Content (Join-Path $repDir 'FAILED.txt')
    '这台机器上大概没有装 AutoCAD（或装在非常规路径）。' |
        Add-Content (Join-Path $repDir 'FAILED.txt')
    '可以在命令行加参数：-ConsolePath "D:\...\accoreconsole.exe"' |
        Add-Content (Join-Path $repDir 'FAILED.txt')
    Write-Host '未找到 accoreconsole.exe，详情见 report\FAILED.txt' -ForegroundColor Red
    exit 2
}

(Get-Item $exe).VersionInfo | Format-List * |
    Out-File (Join-Path $repDir 'version.txt') -Encoding UTF8
# 帮助原文 —— 这份东西就是我们以后所有 DWG 文档的出处，比任何博客都硬。
null = Invoke-Captured -Exe $exe -Args @('/?') -Tag 'usage'

$samples = Get-ChildItem -Path $sampleDir -Filter '*.dxf' | Sort-Object Name
"样本 {0} 张：{1}" -f $samples.Count, ($samples.Name -join ', ') |
    Out-File (Join-Path $repDir 'manifest.txt') -Encoding UTF8

$rows = @()
foreach ($sample in $samples) {
    $stem = $sample.BaseName
    $dwg  = Join-Path $outDir  "$stem.dwg"
    $rt   = Join-Path $backDir "$stem.dxf"

    # 正向：DXF → DWG。先按常见开关试，没产出就换 script 方式再试一次。
    $code = Invoke-Captured -Exe $exe -Args @('/i', $sample.FullName, '/o', $dwg) -Tag "fwd_a_$stem"
    if (-not (Test-Path $dwg)) {
        $scr = Join-Path $repDir "save_$stem.scr"
        @('_.SAVEAS', '2018', $dwg, '') | Set-Content -Path $scr -Encoding ASCII
        $code = Invoke-Captured -Exe $exe -Args @('/i', $sample.FullName, '/s', $scr) -Tag "fwd_b_$stem"
    }
    $madeDwg = Test-Path $dwg

    # 反向：DWG → DXF。判定读的是这个文件。
    $code2 = Invoke-Captured -Exe $exe -Args @('/i', $dwg, '/o', $rt) -Tag "rev_a_$stem"
    if (-not (Test-Path $rt) -and $madeDwg) {
        $scr2 = Join-Path $repDir "dxfout_$stem.scr"
        @('_.DXFOUT', $rt, '') | Set-Content -Path $scr2 -Encoding ASCII
        $code2 = Invoke-Captured -Exe $exe -Args @('/i', $dwg, '/s', $scr2) -Tag "rev_b_$stem"
    }

    $rows += [pscustomobject]@{
        sample   = $sample.Name
        fwd_exit = $code
        dwg      = if ($madeDwg) { (Get-Item $dwg).Length } else { '缺失' }
        rev_exit = $code2
        back_dxf = if (Test-Path $rt) { (Get-Item $rt).Length } else { '缺失' }
    }
}

$rows | Format-Table -AutoSize | Out-String -Width 200 |
    Out-File (Join-Path $repDir 'summary.txt') -Encoding UTF8
$rows | Export-Csv -Path (Join-Path $repDir 'summary.csv') -NoTypeInformation -Encoding UTF8

$zip = Join-Path $PSScriptRoot 'result.zip'
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path @($outDir, $backDir, $repDir) -DestinationPath $zip

Write-Host ''
Write-Host '跑完了。请把 result.zip 原样发回来 —— 不需要你判断成败。' -ForegroundColor Green
Write-Host ("位置：{0}" -f $zip)
if ($rows | Where-Object { $_.back_dxf -eq '缺失' }) {
    Write-Host '注意：有样本没拿到导回的 DXF，report\ 里保留了 AutoCAD 的原始输出，照样发回来。' -ForegroundColor Yellow
}
