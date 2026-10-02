# Build release assets: portable zip + source zip.
# ASCII only on purpose: Windows PowerShell 5.1 reads .ps1 using the OEM
# codepage, so non-ASCII text in this file would be mangled.
#
# Usage:  .\_test\make_release.ps1
#         $env:RELEASE_VERSION = 'v1.1.0'   # optional, default v1.0.0
#
# Note: we check $LASTEXITCODE instead of using $ErrorActionPreference='Stop',
# because native tools (PyInstaller) write progress logs to stderr and
# PowerShell would treat that as a terminating error.

$Version = if ($env:RELEASE_VERSION) { $env:RELEASE_VERSION } else { 'v1.0.0' }

$WS = Split-Path -Parent $PSScriptRoot
Set-Location $WS
$env:PYTHONIOENCODING = 'utf-8'

$out  = Join-Path $WS 'release'
$name = "TH12ModTool-Portable-$Version"
$dist = Join-Path $out 'dist'
New-Item -ItemType Directory -Force -Path $out | Out-Null

function Invoke-Native {
    param([string]$Label, [scriptblock]$Block)
    & $Block 2>&1 | Where-Object {
        $_ -isnot [System.Management.Automation.ErrorRecord]
    } | Select-Object -Last 6
    if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit $LASTEXITCODE)" }
}

Write-Host "=== [1/4] PyInstaller build ($name) ==="
$pyArgs = @(
    '-m', 'PyInstaller', '--noconfirm', '--clean',
    '--name', $name,
    '--distpath', $dist,
    '--workpath', (Join-Path $out 'build'),
    '--specpath', (Join-Path $out 'build'),
    '--add-data', "$WS\tools\modtool\web;web",
    '--paths', $WS,
    '--hidden-import', 'thtk', '--hidden-import', 'thtk.anm',
    '--hidden-import', 'thtk.archive', '--hidden-import', 'thtk.bgm',
    '--hidden-import', 'thtk.crypto', '--hidden-import', 'thtk.lzss',
    '--hidden-import', 'thtk.msg', '--hidden-import', 'PIL.Image',
    '--hidden-import', 'numpy',
    '--console', "$WS\tools\modtool\server.py"
)
$log = Join-Path $out 'build.log'
& python @pyArgs *> $log
if ($LASTEXITCODE -ne 0) {
    Write-Host "--- last 20 lines of build.log ---"
    Get-Content $log -Tail 20
    throw "PyInstaller failed (exit $LASTEXITCODE), see $log"
}
Select-String -Path $log -Pattern 'Building EXE from EXE-00.toc completed|COLLECT-00.toc completed' |
    ForEach-Object { Write-Host ("  " + $_.Line.Trim()) }
if (-not (Test-Path (Join-Path $dist "$name\$name.exe"))) { throw "build failed: exe not found" }

Write-Host ""
Write-Host "=== [2/4] copy docs into the package ==="
$pkg = Join-Path $dist $name
foreach ($f in @('README.md', 'LICENSE', 'NOTICE.md', 'CHANGELOG.md')) {
    Copy-Item (Join-Path $WS $f) $pkg -Force
}
Copy-Item (Join-Path $WS 'tools\modtool\INSTALL.txt') $pkg -Force
Get-ChildItem $pkg -File | Where-Object { $_.Extension -ne '.exe' } |
    Select-Object -ExpandProperty Name | ForEach-Object { Write-Host "  $_" }

Write-Host ""
Write-Host "=== [3/4] zip ==="
$zip = Join-Path $out "$name.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
& python -c "import shutil,sys; shutil.make_archive(sys.argv[1],'zip',sys.argv[2])" (Join-Path $out $name) $pkg *> $null
if ($LASTEXITCODE -ne 0) { throw "zip failed" }

$srczip = Join-Path $out "th12-modkit-$Version-source.zip"
if (Test-Path $srczip) { Remove-Item $srczip -Force }
& git archive --format=zip --prefix="th12-modkit-$Version/" -o $srczip HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }

Write-Host ""
Write-Host "=== [4/4] assets ==="
foreach ($z in @($zip, $srczip)) {
    $i = Get-Item $z
    "{0,-50} {1,7:N1} MB" -f $i.Name, ($i.Length / 1MB)
}
Write-Host ""
Write-Host "output dir: $out"
