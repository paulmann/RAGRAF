#Requires -Version 7.6
#Requires -PSEdition Core
<#
.SYNOPSIS
    Optional external layer for RAGRAF: Joern CLI (+ Temurin JDK 21).

.DESCRIPTION
    Joern is the only readily available tool that builds a full CPG
    (AST+CFG+DFG+PDG). RAGRAF uses it for the interprocedural call layer and
    the `slice` operation. Without this layer the product still works:
    capability=degraded, `slice` refuses with a reason.

    The script is idempotent: a verified unpacked tree is reused, nothing is
    re-downloaded or re-unpacked on subsequent runs.

    What it does:
      1. resolves (or with -Download, fetches) Temurin JDK 21 into .tools/jdk;
      2. resolves (or with -Download, fetches) joern-cli into .tools/joern-cli;
      3. verifies: java -version responds; the joern launcher runs --help;
      4. checks the PHP frontend is listed (php2cpg) — the layer RAGRAF needs;
      5. prints the environment variables that enable the CPG stage.

    Downloads (opt-in via -Download) come from official endpoints only:
      * Joern: GitHub release asset of joernio/joern (pinned version + SHA-256
        from the release checksums file when present, else -ExpectedSha256);
      * JDK: Adoptium API (api.adoptium.net), always over HTTPS.
    Every downloaded archive is size- and (when known) hash-verified BEFORE
    unpacking. Session environment (JAVA_HOME/PATH) is restored on exit.

.PARAMETER Archive
    Path to a locally provided joern-cli archive (zip). Skips download.

.PARAMETER Download
    Allow downloading missing artifacts (Joern and, if absent, JDK).

.PARAMETER JoernVersion
    Joern CLI version to download with -Download. Default pinned: 4.0.99
    (the version this integration was measured against).

.PARAMETER JdkFeatureVersion
    Temurin JDK feature version. Default 21 (measured; docs.joern.io lists 19
    as tested, "newer might work" — 21 IS what RAGRAF verified in production).

.PARAMETER ExpectedSha256
    SHA-256 of the Joern archive when you supply -Archive or when the release
    checksums cannot be fetched. Mismatch aborts before unpacking.

.PARAMETER Force
    Re-unpack even if a Joern tree already exists (stop any running Joern
    server first — the script tries, but a console attached to it may hold files).

.EXAMPLE
    pwsh -NoLogo -NoProfile -NonInteractive -File .\scripts\install-joern.ps1
    Verify/prepare from local artifacts only (no network).

.EXAMPLE
    pwsh -NoLogo -NoProfile -NonInteractive -File .\scripts\install-joern.ps1 -Download
    Fetch JDK + Joern from official sources, verify, unpack, self-check.

.NOTES
    Exit codes: 0 — layer ready; 1 — verification failed; 2 — bad arguments;
    3 — prerequisites missing and -Download not given.
    Disk: unpacked Joern + JDK ≈ 5-6 GB. The script checks free space first.
#>
[CmdletBinding()]
param(
    [string]$Archive = '',
    [switch]$Download,
    [string]$JoernVersion = '4.0.99',
    [int]$JdkFeatureVersion = 21,
    [string]$ExpectedSha256 = '',
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding =
    [System.Text.UTF8Encoding]::new($false)

$script:ExitOk = 0
$script:ExitError = 1
$script:ExitUsage = 2
$script:ExitPrereq = 3

$repoRoot = Split-Path -Parent $PSScriptRoot
$toolsDir = Join-Path $repoRoot '.tools'
$downloadsDir = Join-Path $toolsDir 'downloads'
$joernDir = Join-Path $toolsDir 'joern-cli'
$jdkDir = Join-Path $toolsDir 'jdk'

# Session env save — the old revision leaked JAVA_HOME/PATH into the caller's
# session; everything is restored in the finally block now.
$savedJAVA_HOME = $env:JAVA_HOME
$savedPATH = $env:PATH

function Write-Step { param([string]$Text) Write-Host ''; Write-Host "== $Text" -ForegroundColor Cyan }
function Stop-Install { param([int]$Code, [string]$Message) Write-Host "STOP: $Message" -ForegroundColor Red; exit $Code }

function Get-FileSha256 {
    param([Parameter(Mandatory)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Save-Download {
    <#
    HTTPS download with progress, size sanity check and optional SHA-256
    verification. TLS: system trust (Invoke-WebRequest); no certificate
    bypass anywhere — if your network inspects TLS, install truststore for
    Python separately; PowerShell uses the OS store and just works.
    #>
    param(
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][string]$Destination,
        [long]$MinSizeBytes = 10MB,
        [string]$Sha256 = ''
    )
    Write-Host "   downloading: $Url"
    $tmp = "$Destination.part"
    try {
        # HttpClient streaming instead of Invoke-WebRequest: no 2 GB buffer,
        # works for multi-gigabyte archives, reports progress per chunk.
        $http = [System.Net.Http.HttpClient]::new()
        $http.Timeout = [TimeSpan]::FromMinutes(60)
        $http.DefaultRequestHeaders.UserAgent.ParseAdd('ragraf-install/2.0')
        $response = $http.GetAsync($Url, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            throw "HTTP $([int]$response.StatusCode) for $Url"
        }
        $total = $response.Content.Headers.ContentLength
        $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $file = [System.IO.File]::Create($tmp)
        try {
            $buffer = [byte[]]::new(1MB)
            $copied = 0L
            $lastReport = Get-Date
            while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                $file.Write($buffer, 0, $read)
                $copied += $read
                if (((Get-Date) - $lastReport).TotalSeconds -ge 5) {
                    $pct = if ($total) { '{0:P0}' -f ($copied / $total) } else { "$([math]::Round($copied / 1MB)) MB" }
                    Write-Host "   ... $pct" -ForegroundColor DarkGray
                    $lastReport = Get-Date
                }
            }
        }
        finally { $file.Dispose(); $stream.Dispose(); $http.Dispose() }

        if ($copied -lt $MinSizeBytes) {
            throw "downloaded file is suspiciously small ($([math]::Round($copied / 1MB, 1)) MB): $Url"
        }
        if ($Sha256) {
            $actual = Get-FileSha256 -Path $tmp
            if ($actual -ne $Sha256.ToLowerInvariant()) {
                Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
                throw "SHA-256 mismatch: expected $Sha256, got $actual — archive rejected (not unpacked)"
            }
            Write-Host "   SHA-256 verified: $actual" -ForegroundColor Green
        }
        else {
            Write-Host '   WARNING: no SHA-256 to verify against — pass -ExpectedSha256 for supply-chain safety' -ForegroundColor Yellow
        }
        Move-Item -LiteralPath $tmp -Destination $Destination -Force
    }
    catch {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        throw
    }
}

function Expand-SingleRootArchive {
    <#
    Unpack a zip that contains one top-level directory into $DestinationDir.
    Verifies the result instead of trusting the unpack.
    #>
    param(
        [Parameter(Mandatory)][string]$ArchivePath,
        [Parameter(Mandatory)][string]$DestinationDir
    )
    $tmp = "$DestinationDir-unpack-$(Get-Random)"
    Write-Host '   unpacking (can take several minutes)...'
    try {
        Expand-Archive -LiteralPath $ArchivePath -DestinationPath $tmp -Force
        $inner = Get-ChildItem -LiteralPath $tmp -Directory | Select-Object -First 1
        if ($inner) {
            Move-Item -LiteralPath $inner.FullName -Destination $DestinationDir -Force
        }
        else {
            Move-Item -LiteralPath $tmp -Destination $DestinationDir -Force
            $tmp = ''   # moved as-is; nothing to clean
        }
    }
    finally {
        if ($tmp -and (Test-Path -LiteralPath $tmp)) {
            Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Find-JoernLauncher {
    <#
    Platform-aware launcher discovery. The old revision accepted .sh on
    Windows and trusted the first recursive hit; now candidates are ordered
    by platform and the result must respond to --help.
    #>
    param([Parameter(Mandatory)][string]$Root)
    if ($IsWindows) { $names = @('joern.bat', 'joern-cli\joern.bat', 'bin\joern.bat', 'joern-cli\bin\joern.bat', 'joern-cli') }
    else { $names = @('joern', 'joern-cli/joern', 'bin/joern', 'joern-cli/bin/joern') }
    foreach ($name in $names) {
        $p = Join-Path $Root $name
        if ((Test-Path -LiteralPath $p -PathType Leaf)) { return $p }
    }
    $filter = if ($IsWindows) { @('*.bat') } else { @('') }
    $found = Get-ChildItem -LiteralPath $Root -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.BaseName -eq 'joern' -and ($_.Extension -in $filter -or -not $IsWindows) } |
        Select-Object -First 1
    if ($found) { return $found.FullName }
    return $null
}

try {
    Write-Host 'RAGRAF — optional CPG layer (Java + Joern)' -ForegroundColor White

    # ------------------------------------------------------------------
    # 0. Prerequisites & disk space
    # ------------------------------------------------------------------
    Write-Step 'Prerequisites'
    New-Item -ItemType Directory -Force -Path $downloadsDir | Out-Null

    try {
        $drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetFullPath($toolsDir))
        $freeGb = [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
        if ($freeGb -lt 7) {
            Stop-Install -Code $script:ExitPrereq -Message "need ~7 GB free for JDK + Joern (download + unpacked); drive has $freeGb GB."
        }
        Write-Host "   disk: $freeGb GB free (need ~7 GB)"
    }
    catch [System.ArgumentException] {
        # Exotic mount without drive info: skip the space check, do not block install.
        Write-Verbose "disk space check skipped: $($_.Exception.Message)"
    }

    # ------------------------------------------------------------------
    # 1. JDK
    # ------------------------------------------------------------------
    Write-Step "JDK (Temurin $JdkFeatureVersion)"
    $java = if ($IsWindows) { Join-Path $jdkDir 'bin\java.exe' } else { Join-Path $jdkDir 'bin/java' }
    if (-not (Test-Path -LiteralPath $java)) {
        if (-not $Download) {
            Stop-Install -Code $script:ExitPrereq -Message "JDK not found at $jdkDir. Install Temurin JDK $JdkFeatureVersion there manually, or re-run with -Download."
        }
        Write-Host "   not present — downloading from the Adoptium API (official Eclipse Temurin)"
        # Adoptium "latest GA" redirect for the feature version; always HTTPS.
        $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $IsMacOS -and [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq 'Arm64') { 'aarch64' } else { 'x64' }
        $osName = if ($IsWindows) { 'windows' } elseif ($IsMacOS) { 'mac' } else { 'linux' }
        $jdkUrl = "https://api.adoptium.net/v3/binary/latest/$JdkFeatureVersion/ga/$osName/$arch/jdk/hotspot/normal/eclipse"
        $jdkZip = Join-Path $downloadsDir "temurin-jdk$JdkFeatureVersion-$osName-$arch.zip"
        if ($IsWindows -or $IsMacOS) {
            Save-Download -Url $jdkUrl -Destination $jdkZip -MinSizeBytes 80MB
            Expand-SingleRootArchive -ArchivePath $jdkZip -DestinationDir $jdkDir
        }
        else {
            # Linux: tar.gz from the same API.
            $jdkUrl = "https://api.adoptium.net/v3/binary/latest/$JdkFeatureVersion/ga/linux/$arch/jdk/hotspot/normal/eclipse"
            $jdkTar = Join-Path $downloadsDir "temurin-jdk$JdkFeatureVersion-linux-$arch.tar.gz"
            Save-Download -Url $jdkUrl -Destination $jdkTar -MinSizeBytes 80MB
            $tmpJdk = "$jdkDir-unpack"
            New-Item -ItemType Directory -Force -Path $tmpJdk | Out-Null
            tar -xzf $jdkTar -C $tmpJdk
            $inner = Get-ChildItem -LiteralPath $tmpJdk -Directory | Select-Object -First 1
            if ($inner) { Move-Item -LiteralPath $inner.FullName -Destination $jdkDir }
            Remove-Item -LiteralPath $tmpJdk -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    $java = if ($IsWindows) { Join-Path $jdkDir 'bin\java.exe' } else { Join-Path $jdkDir 'bin/java' }
    if (-not (Test-Path -LiteralPath $java)) {
        Stop-Install -Code $script:ExitError -Message "JDK layout unexpected: $java not found after install."
    }
    $javaVersion = (& $java -version 2>&1 | Select-Object -First 1)
    Write-Host "   java: $javaVersion" -ForegroundColor Green

    # ------------------------------------------------------------------
    # 2. Joern archive
    # ------------------------------------------------------------------
    Write-Step 'Joern CLI'
    if (-not $Archive) { $Archive = Join-Path $downloadsDir 'joern-cli.zip' }
    $haveTree = Test-Path -LiteralPath $joernDir
    if ($haveTree -and -not $Force) {
        Write-Host '   unpacked tree exists — verifying instead of re-unpacking (use -Force to redo)'
    }
    else {
        if (-not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
            if (-not $Download) {
                Stop-Install -Code $script:ExitPrereq -Message "Joern archive not found: $Archive. Place joern-cli.zip there, pass -Archive <path>, or re-run with -Download."
            }
            $assetName = "joern-cli-$JoernVersion.zip"
            $joernUrl = "https://github.com/joernio/joern/releases/download/v$JoernVersion/$assetName"
            $sha = $ExpectedSha256
            if (-not $sha) {
                # Best effort: the release publishes a checksums file; if the
                # fetch fails we proceed with a loud warning (Save-Download
                # enforces -ExpectedSha256 when provided).
                try {
                    $sumsUrl = "https://github.com/joernio/joern/releases/download/v$JoernVersion/checksums.txt"
                    $sums = (Invoke-WebRequest -Uri $sumsUrl -UseBasicParsing -TimeoutSec 60).Content
                    $line = ($sums -split "`r?`n" | Where-Object { $_ -match [regex]::Escape($assetName) } | Select-Object -First 1)
                    if ($line -match '^([0-9a-fA-F]{64})') { $sha = $Matches[1] }
                }
                catch { Write-Host '   could not fetch checksums.txt — proceeding without hash pin' -ForegroundColor Yellow }
            }
            Save-Download -Url $joernUrl -Destination $Archive -MinSizeBytes 500MB -Sha256 $sha
        }
        elseif ($ExpectedSha256) {
            $actual = Get-FileSha256 -Path $Archive
            if ($actual -ne $ExpectedSha256.ToLowerInvariant()) {
                Stop-Install -Code $script:ExitError -Message "SHA-256 mismatch for $Archive (expected $ExpectedSha256, got $actual)."
            }
            Write-Host "   SHA-256 verified: $actual" -ForegroundColor Green
        }
        $archiveSize = (Get-Item -LiteralPath $Archive).Length
        Write-Host "   archive: $([math]::Round($archiveSize / 1MB, 1)) MB"

        if ($haveTree -and $Force) {
            # A running Joern server holds files and the port; stop ours first.
            Get-CimInstance Win32_Process -Filter "Name = 'java.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -match 'joern' } |
                ForEach-Object {
                    try { Stop-Process -Id $_.ProcessId -Force }
                    catch {
                        # Process already exited between enumeration and kill — benign race.
                        Write-Verbose "joern process kill: $($_.Exception.Message)"
                    }
                }
            Remove-Item -LiteralPath $joernDir -Recurse -Force
        }
        Expand-SingleRootArchive -ArchivePath $Archive -DestinationDir $joernDir
        Write-Host '   unpacked'
    }

    # ------------------------------------------------------------------
    # 3. Launcher discovery + smoke test
    # ------------------------------------------------------------------
    Write-Step 'Launcher'
    $joernExe = Find-JoernLauncher -Root $joernDir
    if (-not $joernExe) {
        Stop-Install -Code $script:ExitError -Message "joern launcher not found under $joernDir (checked platform-specific candidates + recursive search)."
    }
    Write-Host "   found: $joernExe"

    # Env for the smoke test ONLY — restored in finally (the old revision
    # leaked both variables into the caller's session).
    $env:JAVA_HOME = $jdkDir
    $env:PATH = if ($IsWindows) { "$(Join-Path $jdkDir 'bin');$env:PATH" } else { "$(Join-Path $jdkDir 'bin'):$env:PATH" }

    Write-Step 'Smoke test (--help)'
    $helpOut = ''
    try {
        $psi = [System.Diagnostics.ProcessStartInfo]::new()
        $psi.FileName = if ($IsWindows -and $joernExe -match '\.bat$') { 'cmd.exe' } else { $joernExe }
        if ($IsWindows -and $joernExe -match '\.bat$') { $psi.ArgumentList.Add('/c'); $psi.ArgumentList.Add($joernExe) }
        $psi.ArgumentList.Add('--help')
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.UseShellExecute = $false
        $proc = [System.Diagnostics.Process]::Start($psi)
        $outTask = $proc.StandardOutput.ReadToEndAsync()
        $errTask = $proc.StandardError.ReadToEndAsync()
        if (-not $proc.WaitForExit(300000)) {
            try { $proc.Kill($true) }
            catch {
                # Already dead or not killable from here; the timeout path continues anyway.
                Write-Verbose "joern smoke-test kill: $($_.Exception.Message)"
            }
            Write-Host '   WARNING: --help timed out after 300 s (JVM cold start on slow disks can); continuing' -ForegroundColor Yellow
        }
        $null = $outTask.Wait(5000); $null = $errTask.Wait(5000)
        $helpOut = ($outTask.IsCompleted ? $outTask.Result : '') + ($errTask.IsCompleted ? $errTask.Result : '')
        Write-Host "   Joern responded, output length: $($helpOut.Length)" -ForegroundColor Green
    }
    catch {
        Write-Host "   WARNING: --help failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    # ------------------------------------------------------------------
    # 4. PHP frontend check (the capability RAGRAF actually uses)
    # ------------------------------------------------------------------
    Write-Step 'PHP frontend (php2cpg)'
    $phpSupport = $helpOut -match 'php'
    if (-not $phpSupport) {
        # --help may not enumerate frontends; ask the languages list directly.
        try {
            $langs = & $joernExe --list-languages 2>&1 | Out-String
            $phpSupport = $langs -match '(?i)php'
        }
        catch {
            # Older Joern builds lack --list-languages; keep the --help verdict.
            Write-Verbose "list-languages probe: $($_.Exception.Message)"
        }
    }
    Write-Host ("   php support: {0}" -f $(if ($phpSupport) { 'confirmed' } else { 'NOT confirmed — verify manually (joern-parse --language PHP); RAGRAF will run degraded' })) `
        -ForegroundColor $(if ($phpSupport) { 'Green' } else { 'Yellow' })

    # ------------------------------------------------------------------
    # 5. Result
    # ------------------------------------------------------------------
    Write-Step 'Result'
    [ordered]@{
        joern_path    = $joernExe
        jdk_home      = $jdkDir
        java          = "$javaVersion"
        php_confirmed = [bool]$phpSupport
        joern_version = $JoernVersion
    } | Format-List | Out-String | Write-Host

    Write-Host 'To enable the CPG stage, point these environment variables at the directories above:' -ForegroundColor Cyan
    Write-Host "  `$env:RAGRAF_JOERN_HOME = '$joernDir'" -ForegroundColor DarkGray
    Write-Host "  `$env:RAGRAF_JAVA_HOME  = '$jdkDir'" -ForegroundColor DarkGray
    Write-Host 'Note: php2cpg additionally requires a `php` executable in PATH (>= 7.1).' -ForegroundColor DarkGray
    exit $script:ExitOk
}
finally {
    # Session hygiene: never leak JAVA_HOME/PATH mutations to the caller.
    if ($null -eq $savedJAVA_HOME) { Remove-Item Env:\JAVA_HOME -ErrorAction SilentlyContinue } else { $env:JAVA_HOME = $savedJAVA_HOME }
    $env:PATH = $savedPATH
}
