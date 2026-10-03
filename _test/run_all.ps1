# Pre-delivery self-check: runs every regression suite + browser acceptance.
#
# Usage:  pwsh -File _test\run_all.ps1            (or right-click > Run with PowerShell)
#         _test\run_all.ps1 -SkipBrowser          (API suites only)
#
# Notes
#  - This file is intentionally pure ASCII: Windows PowerShell 5.1 reads .ps1
#    using the OEM codepage, so non-ASCII text here would be mangled.
#    Non-ASCII paths are passed through environment variables instead.
#  - Only the server/chrome started by this script are stopped at the end.
#  - Exits 1 if any suite failed (the old run_all.cmd always exited 0).

param(
    [string]$Port = "8799",
    [string]$DebugPort = "9222",
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Continue"
$WS = Split-Path -Parent $PSScriptRoot
Set-Location $WS
$env:PYTHONIOENCODING = "utf-8"

if (-not $env:TH12_GAME_DIR) {
    $cand = Join-Path $WS "game\[th12] 东方星莲船 (汉化版+日文版)"
    if (Test-Path -LiteralPath $cand) { $env:TH12_GAME_DIR = $cand }
}
if (-not $env:TH12_GAME_DIR) {
    Write-Host "TH12_GAME_DIR is not set. Example:" -ForegroundColor Red
    Write-Host "  `$env:TH12_GAME_DIR = 'D:\Games\th12'"
    exit 2
}
if (-not $env:TH12_DAMAGED_DIR) {
    $c2 = Join-Path $WS "测试\[th12] 东方星莲船 (汉化版+日文版)"
    if (Test-Path -LiteralPath $c2) { $env:TH12_DAMAGED_DIR = $c2 }
}
# The on-disk loop suites copy a game dir and must not touch a modified one.
# Prefer an explicit pristine dir; otherwise fall back to TH12_GAME_DIR
# (those suites only ever write into their own temp copy).
if (-not $env:TH12_PRISTINE_DIR) { $env:TH12_PRISTINE_DIR = $env:TH12_GAME_DIR }

# ---------------------------------------------------------------------------
# Run everything against a throwaway COPY of the game directory.
#
# Several suites (verify_save, verify_restore, verify_bgm_replace_loop, and the
# browser ones) write into the directory they are pointed at. Aimed at a real
# install that silently damages it: it did exactly that once, leaving
# th12c.dat unreadable. Copying costs a few seconds and makes the whole run
# non-destructive, so the pristine dir can stay pristine.
#
# TH12_GAME_DIR is retargeted at the copy before the suites start; the
# caller's original value is remembered as $script:RealGameDir.
# ---------------------------------------------------------------------------
$script:RealGameDir = $env:TH12_GAME_DIR
$script:TempGame = $null
$script:RealConfig = $null

function New-TempGameCopy {
    # Delegated to Python: Windows PowerShell 5.1 treats paths containing
    # characters like < > as wildcards and mangles non-ASCII paths through
    # the OEM codepage, so Copy-Item silently produced an empty copy.
    $out = & python _test\make_game_copy.py $script:RealGameDir 2>&1
    $code = $LASTEXITCODE
    $path = @($out | Where-Object { $_ -is [string] -and $_ -match '^[A-Za-z]:\\' })
    if ($code -ne 0 -or $path.Count -eq 0) {
        Write-Host "  copy failed: $($out | Select-Object -Last 1)" -ForegroundColor Yellow
        return $false
    }
    $script:TempGame = $path[-1].Trim()
    $env:TH12_GAME_DIR = $script:TempGame
    $env:TH12_PRISTINE_DIR = $script:TempGame
    Write-Host "  game dir   : $($script:RealGameDir)"
    Write-Host "  test copy  : $($script:TempGame)  (all suites run here)"
    return $true
}

function Remove-TempGameCopy {
    if ($script:TempGame -and (Test-Path -LiteralPath $script:TempGame)) {
        # Also drop the wrapper dir the helper created around the copy.
        Remove-Item -LiteralPath (Split-Path -Parent $script:TempGame) -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $script:TempGame -Recurse -Force -ErrorAction SilentlyContinue
    }
    # Start-TestServer blanks config.json on every restart; restore the caller's
    # own copy byte-for-byte (Copy-Item, no PowerShell string round-trip).
    $bak = "tools\modtool\config.json.selftest-bak"
    if (Test-Path -LiteralPath $bak) {
        Copy-Item -LiteralPath $bak -Destination "tools\modtool\config.json" -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $bak -Force -ErrorAction SilentlyContinue
    }
}

$script:Results = @()
$script:Server = $null
$script:Chrome = $null

function Stop-TestServer {
    if ($script:Server -and -not $script:Server.HasExited) {
        Stop-Process -Id $script:Server.Id -Force -ErrorAction SilentlyContinue
    }
    $script:Server = $null
    Start-Sleep -Milliseconds 800
}

function Start-TestServer {
    Stop-TestServer
    Set-Content -LiteralPath "tools\modtool\config.json" -Value "{}" -Encoding UTF8 -NoNewline
    $script:Server = Start-Process python `
        -ArgumentList "tools\modtool\server.py", "--port", $Port, "--no-browser" `
        -WindowStyle Hidden -PassThru
    $url = "http://127.0.0.1:$Port/api/state"
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
    }
    return $false
}

function Add-Result {
    param([string]$Name, [bool]$Ok, [string]$Detail)
    $tag = if ($Ok) { "PASS" } else { "FAIL" }
    $script:Results += [pscustomobject]@{ Name = $Name; Ok = $Ok; Detail = $Detail }
    $color = if ($Ok) { "Green" } else { "Red" }
    Write-Host ("  [{0}] {1,-26} {2}" -f $tag, $Name, $Detail) -ForegroundColor $color
}

function Invoke-PyTest {
    param([string]$Name, [string]$Script, [string[]]$Extra = @())
    Start-TestServer | Out-Null
    $a = @("_test\$Script", $Port) + $Extra
    $out = & python @a 2>&1
    $code = $LASTEXITCODE
    $tail = ($out | Where-Object { $_ -match "passed|failed|PASS|FAIL|\u901a\u8fc7|\u5931\u8d25" } | Select-Object -Last 1)
    if (-not $tail) { $tail = ($out | Select-Object -Last 1) }
    if ($tail -is [array]) { $tail = $tail -join " / " }
    Add-Result $Name ($code -eq 0) ([string]$tail).Trim()
    if ($code -ne 0) {
        $out | Select-Object -Last 8 | ForEach-Object { Write-Host "         $_" }
    }
}

function Invoke-NodeTest {
    param([string]$Name, [string]$Script, [string[]]$Extra = @())
    if ($SkipBrowser) { return }
    # Reset the server before every browser suite: these suites mutate state
    # (e.g. cdp_bgm replaces track 0 with a 20s clip), and a later suite
    # assuming pristine data would then fail for the wrong reason.
    Start-TestServer | Out-Null
    $body = @{ game_dir = $env:TH12_GAME_DIR } | ConvertTo-Json -Compress
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/config" -Method POST `
            -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
            -ContentType "application/json" -UseBasicParsing -TimeoutSec 30 | Out-Null
    } catch { }
    $a = @("_test\$Script", $Port, $DebugPort) + $Extra
    $out = & node @a 2>&1
    $code = $LASTEXITCODE
    $tail = ($out | Where-Object { $_ -match "all passed|\u5168\u90e8\u901a\u8fc7|failed \d+" } | Select-Object -Last 1)
    if (-not $tail) { $tail = ($out | Select-Object -Last 1) }
    if ($tail -is [array]) { $tail = $tail -join " / " }
    Add-Result $Name ($code -eq 0) ([string]$tail).Trim()
    if ($code -ne 0) {
        $out | Select-Object -Last 10 | ForEach-Object { Write-Host "         $_" }
    }
}

# Keep the caller's real config.json; Start-TestServer blanks it on every start.
# Handled by a Python helper rather than PowerShell: reading the game path into
# a PS variable goes through the OEM codepage, which cannot represent the
# fullwidth parentheses in these directory names, so a PS-side round-trip would
# write mojibake back into config.json.
if (Test-Path -LiteralPath "tools\modtool\config.json") {
    Copy-Item -LiteralPath "tools\modtool\config.json" -Destination "tools\modtool\config.json.selftest-bak" -Force -ErrorAction SilentlyContinue
}

Write-Host ("=" * 74)
Write-Host "PRE-DELIVERY SELF-CHECK"
if (-not (New-TempGameCopy)) {
    Write-Host "  game dir   : $($env:TH12_GAME_DIR)  (in-place: suites WILL modify it)"
}
if ($env:TH12_DAMAGED_DIR) { Write-Host "  damaged dir: $($env:TH12_DAMAGED_DIR)" }
Write-Host ("=" * 74)

Write-Host ""
Write-Host "--- static audit ---"
& python _test\audit.py 2>&1 |
    Where-Object { $_ -match "id|\u63a5\u53e3|\u6b7b\u4ee3\u7801|\u5171 \d+ \u5904|missing|route" } |
    Select-Object -First 14 | ForEach-Object { Write-Host "  $_" }

Write-Host ""
Write-Host "--- API suites (each restarts the server) ---"
Invoke-PyTest "settings"          "verify_settings.py"
Invoke-PyTest "damaged-data"      "verify_damaged.py"
Invoke-PyTest "workflow"          "verify_workflow.py"
Invoke-PyTest "real-save"         "verify_save.py"
Invoke-PyTest "restore-safety"    "verify_restore.py"
Invoke-PyTest "bgm-replace-loop"  "verify_bgm_replace_loop.py"
Invoke-PyTest "bgm-loop-same-len" "verify_bgm_loop.py"
Invoke-PyTest "bgm-loop-diff-len" "verify_bgm_loop2.py"
# These two read the value back from a real archive on disk (the suites above
# only check the server's own answers), which is what actually proves a loop
# point survives a save.
Invoke-PyTest "bgm-loop-ondisk"   "verify_e2e_loop.py"
Invoke-PyTest "bgm-replace+loop"  "verify_loop_combo.py"
# Replacement must accept whatever WAV the user drags in (mono / 8-bit /
# 24-bit / 48kHz ...) and must reject broken files instead of silently
# writing half a track.
Invoke-PyTest "bgm-replace-fmt"   "verify_replace_formats.py"
# The loop point is implemented by splicing audio (the engine ignores the
# thbgm.fmt loop field), so this one verifies the splice on real PCM.
Invoke-PyTest "bgm-loop-splice"   "verify_loop_splice.py"
# Repeated editing is the main real-world usage pattern: change the loop point,
# replace the track, change it again. A single mistake here silently makes the
# audio grow on every save, so it gets its own suite.
Invoke-PyTest "bgm-repeat-edits"  "verify_repeat_edits.py"

Write-Host ""
Write-Host "--- browser acceptance ---"
if ($SkipBrowser) {
    Write-Host "  (skipped: -SkipBrowser)"
} else {
    $profile = Join-Path $WS "_test\chrome-profile-run"
    New-Item -ItemType Directory -Force -Path $profile | Out-Null
    $chromeExe = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $chromeExe) {
        Write-Host "  Chrome/Edge not found - skipping browser suites" -ForegroundColor Yellow
    } else {
        # --autoplay-policy: the wave-player suite needs autoplay to work.
        # Headless Chrome blocks autoplay by default, so audio metadata would
        # never load and seeking (click-to-jump) would silently do nothing.
        $script:Chrome = Start-Process $chromeExe -ArgumentList @(
            "--headless=new", "--remote-debugging-port=$DebugPort",
            "--remote-allow-origins=*", "--user-data-dir=$profile",
            "--no-first-run", "--no-default-browser-check",
            "--disable-gpu", "--disable-crashpad", "--no-sandbox",
            "--autoplay-policy=no-user-gesture-required", "about:blank"
        ) -WindowStyle Hidden -PassThru
        Start-Sleep -Seconds 5
        Start-TestServer | Out-Null
        $body = @{ game_dir = $env:TH12_GAME_DIR } | ConvertTo-Json -Compress
        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/config" -Method POST `
                -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
                -ContentType "application/json" -UseBasicParsing -TimeoutSec 30 | Out-Null
        } catch { }
        Invoke-NodeTest "browser:pick-file" "cdp_pick_file.js" @("$($env:TH12_GAME_DIR)\th12c.dat", $env:TH12_GAME_DIR)
        Invoke-NodeTest "browser:one-dir"   "cdp_one_dir.js"   @($env:TH12_GAME_DIR)
        Invoke-NodeTest "browser:encoding"  "cdp_encoding.js"
        Invoke-NodeTest "browser:bgm"       "cdp_bgm.js"
        Invoke-NodeTest "browser:wave"      "cdp_wave.js"
    }
}

Stop-TestServer
if ($script:Chrome -and -not $script:Chrome.HasExited) {
    Stop-Process -Id $script:Chrome.Id -Force -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*chrome-profile-run*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Set-Content -LiteralPath "tools\modtool\config.json" -Value "{}" -Encoding UTF8 -NoNewline
Remove-Item -LiteralPath (Join-Path $WS "_test\chrome-profile-run") -Recurse -Force -ErrorAction SilentlyContinue
# Drop the throwaway game copy and put the caller's config.json back.
Remove-TempGameCopy

Write-Host ""
Write-Host ("=" * 74)
$pass = @($script:Results | Where-Object { $_.Ok }).Count
$failRows = @($script:Results | Where-Object { -not $_.Ok })
$fail = $failRows.Count
Write-Host ("SUMMARY: {0} passed, {1} failed" -f $pass, $fail)
if ($fail -gt 0) {
    $failRows | ForEach-Object {
        Write-Host ("  FAILED: {0} - {1}" -f $_.Name, $_.Detail) -ForegroundColor Red
    }
    exit 1
}
Write-Host "ALL PASSED" -ForegroundColor Green
exit 0
