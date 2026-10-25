#Requires -Version 7.6
#Requires -PSEdition Core
<#
.SYNOPSIS
    Показывает и (по ключу -Apply) снимает ограничение Low-целостности с дерева проекта.

.DESCRIPTION
    Замерено 15.10.2026 на этой машине: исполняемый файл, лежащий в каталоге с
    меткой целостности Low, запускается с низкой целостностью и урезанным токеном.
    Для дерева проекта это значит, что java.exe из .tools\jdk не может
    писать НИГДЕ, кроме самого дерева: ни во временный каталог пользователя, ни в
    каталоги других проектов. Отсюда отказ сборки CPG: временный php.ini для
    php2cpg и файл cpg.bin создаются за пределами дерева и получают
    AccessDeniedException.

    Права NTFS при этом ни при чём: запись внутрь дерева у java есть. Дело в метке
    целостности (SACL), поэтому и лечится она не выдачей прав на папки, а снятием
    метки Low.

    По умолчанию скрипт только измеряет и печатает отчёт: ACL, метку каталога,
    фактическую целостность процесса, запущенного из каталога, и пробу записи java.
    С ключом -Apply он снимает метку Low (ставит Medium, наследуемо на подкаталоги)
    и повторяет проверки.

    Права администратора не обязательны там, где объект подконтролен пользователю:
    замерено 15.10.2026 — icacls /setintegritylevel на подкаталоге внутри этого дерева
    сработал без повышения прав. Если запись отклонена (корень дерева принадлежит
    BUILTIN\Administrators), скрипт скажет об этом прямо и предложит повторить запуск
    в консоли администратора (код возврата 2).

.PARAMETER Path
    Корни деревьев, метку которых надо проверить и снять.
    По умолчанию: каталог проекта (родитель каталога tools)

.PARAMETER JavaExe
    java.exe для пробы записи. По умолчанию .tools\jdk\bin\java.exe проекта.

.PARAMETER TargetLabel
    Метка, которая ставится вместо Low: M (Medium, по умолчанию) или H (High).

.PARAMETER Apply
    Выполнить изменения. Без этого ключа скрипт ничего не меняет.

.PARAMETER RemoveDenyDeleteAcl
    Дополнительно снять с корня ACE «Everyone (CI) DENY DC», если он есть.
    На работу он не влиял (проверено 15.10.2026: удаление файлов и подкаталогов
    внутри дерева проходит), поэтому по умолчанию сохраняется.

.PARAMETER LogPath
    Файл журнала. По умолчанию: <временный каталог>\ragraf-lowintegrity-<дата>.log

.EXAMPLE
    pwsh -NoLogo -NoProfile -File .\tools\Fix-LowIntegritySandbox.ps1

.EXAMPLE
    # с изменением метки; администратор нужен только если обычный запуск получил отказ
    pwsh -NoLogo -NoProfile -File .\tools\Fix-LowIntegritySandbox.ps1 -Apply -Confirm:$false -Verbose

.NOTES
    icacls /save НЕ сохраняет метку целостности (сказано в его же справке), поэтому
    перед правкой ACL и метка фиксируются текстом в журнале и в отдельном файле
    резервной копии.

    Коды возврата: 0 — проблемы нет или она устранена; 1 — проблема осталась;
    2 — изменение метки отклонено (нужен запуск от администратора);
    3 — ни один целевой каталог не найден.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string[]]$Path = @((Split-Path -Parent $PSScriptRoot)),
    [string]$JavaExe = (Join-Path (Split-Path -Parent $PSScriptRoot) '.tools\jdk\bin\java.exe'),
    [ValidateSet('M', 'H')][string]$TargetLabel = 'M',
    [switch]$Apply,
    [switch]$RemoveDenyDeleteAcl,
    [string]$LogPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:LogFile = if ($LogPath) {
    $LogPath
} else {
    Join-Path ([IO.Path]::GetTempPath()) ('ragraf-lowintegrity-{0}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
$script:AccessDenied = $false

function Write-Report {
    param([string]$Text = '')
    Write-Host $Text
    Add-Content -LiteralPath $script:LogFile -Value $Text -Encoding utf8NoBOM
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return [Security.Principal.WindowsPrincipal]::new($identity).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-IntegrityNameFromSid {
    param([Parameter(Mandatory)][string]$Sid)
    switch -Regex ($Sid) {
        'S-1-16-4096' { return 'Low' }
        'S-1-16-8192' { return 'Medium' }
        'S-1-16-12288' { return 'High' }
        'S-1-16-16384' { return 'System' }
        default { return 'Unknown' }
    }
}

function Get-DirectoryLabel {
    param([Parameter(Mandatory)][string]$Directory)
    # icacls печатает метку ИМЕНЕМ ("Mandatory Label\Low Mandatory Level:(OI)(CI)(NW)"),
    # без SID; поэтому разбираем и имя, и SID, если он всё же попал в строку.
    $text = (icacls $Directory 2>&1 | Out-String)
    $line = [regex]::Match($text, '(?m)^\s*Mandatory Label\\([^\r\n]+)')
    if (-not $line.Success) { return 'нет' }
    $value = $line.Groups[1].Value
    $sid = [regex]::Match($value, 'S-1-16-\d+')
    if ($sid.Success) { return (Get-IntegrityNameFromSid -Sid $sid.Value) }
    if ($value -match 'Low') { return 'Low' }
    if ($value -match 'Medium') { return 'Medium' }
    if ($value -match 'High') { return 'High' }
    if ($value -match 'System') { return 'System' }
    return "не распознано: $value"
}

function Get-ProcessIntegrityFromDirectory {
    param([Parameter(Mandatory)][string]$Directory)
    $result = [pscustomobject]@{
        Directory = $Directory
        Integrity = 'Unknown'
        Note      = ''
    }
    $system32 = Join-Path ([Environment]::GetFolderPath('Windows')) 'System32'
    $probeDir = Join-Path $Directory ('il-probe-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    try {
        New-Item -ItemType Directory -Path $probeDir -Force | Out-Null
        $exe = Join-Path $probeDir 'whoami-probe.exe'
        Copy-Item -LiteralPath (Join-Path $system32 'whoami.exe') -Destination $exe -Force
        $out = Join-Path $probeDir 'groups.txt'
        Start-Process -FilePath $exe -ArgumentList '/groups' -RedirectStandardOutput $out -Wait -NoNewWindow
        $match = [regex]::Match((Get-Content -LiteralPath $out -Raw), 'S-1-16-(\d+)')
        if ($match.Success) {
            $result.Integrity = Get-IntegrityNameFromSid -Sid $match.Value
        } else {
            $result.Note = 'строка метки не найдена в выводе whoami'
        }
    } catch {
        $result.Note = $_.Exception.Message
    } finally {
        Remove-Item -LiteralPath $probeDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $result
}

function Test-JavaTempWrite {
    param([Parameter(Mandatory)][string]$JavaExePath)
    $result = [pscustomobject]@{
        Ran      = $false
        TmpDir   = ''
        TempFile = 'не проверялось'
        PhpIni   = 'не проверялось'
        Detail   = ''
    }
    if (-not (Test-Path -LiteralPath $JavaExePath)) {
        $result.Detail = "java не найден: $JavaExePath"
        return $result
    }
    $work = Join-Path ([IO.Path]::GetTempPath()) ('ragraf-java-probe-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $source = @'
import java.nio.file.*;
public class W {
    public static void main(String[] a) throws Exception {
        String t = System.getProperty("java.io.tmpdir");
        System.out.println("TMPDIR=" + t);
        try { Files.delete(Files.createTempFile("ragraf_", ".tmp")); System.out.println("TMPFILE=OK"); }
        catch (Throwable e) { System.out.println("TMPFILE=FAIL " + e.getClass().getSimpleName() + ": " + e.getMessage()); }
        String ini = t + java.io.File.separator + "php.ini";
        try { Files.write(Paths.get(ini), "; probe".getBytes("UTF-8")); Files.delete(Paths.get(ini)); System.out.println("PHPINI=OK"); }
        catch (Throwable e) { System.out.println("PHPINI=FAIL " + e.getClass().getSimpleName() + ": " + e.getMessage()); }
    }
}
'@
    Set-Content -LiteralPath (Join-Path $work 'W.java') -Value $source -Encoding utf8NoBOM
    try {
        $output = (& $JavaExePath (Join-Path $work 'W.java') 2>&1 | Out-String)
        $result.Ran = $true
        $tmp = [regex]::Match($output, 'TMPDIR=(.*)')
        if ($tmp.Success) { $result.TmpDir = $tmp.Groups[1].Value.Trim() }
        $result.TempFile = if ($output -match 'TMPFILE=OK') { 'OK' } else { 'FAIL' }
        $result.PhpIni = if ($output -match 'PHPINI=OK') { 'OK' } else { 'FAIL' }
        $result.Detail = (($output -split "`r?`n") | Where-Object { $_ -match 'FAIL' }) -join ' | '
    } catch {
        $result.Detail = $_.Exception.Message
    } finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $result
}

# --- 0. Заголовок -----------------------------------------------------------

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = Test-Administrator
Write-Report '=== Low-целостность дерева проекта: проверка и снятие ==='
Write-Report ('время        : {0:yyyy-MM-dd HH:mm:ss}' -f (Get-Date))
Write-Report ("пользователь : $($identity.Name)")
Write-Report ('администратор: {0}' -f $(if ($isAdmin) { 'да' } else { 'нет (доступен только отчёт)' }))
Write-Report ("журнал       : $($script:LogFile)")
Write-Report ''

$targets = @()
foreach ($item in $Path) {
    if (Test-Path -LiteralPath $item) {
        $targets += (Resolve-Path -LiteralPath $item).Path
    } else {
        Write-Report "каталог не найден: $item"
    }
}
if ($targets.Count -eq 0) {
    Write-Report 'нечего проверять: ни одного целевого каталога'
    exit 3
}

# --- 1. Факты до правки -----------------------------------------------------

Write-Report '--- 1. Факты: метка каталога, ACL, целостность процесса ---'
$lowLabeled = @()
foreach ($target in $targets) {
    $label = Get-DirectoryLabel -Directory $target
    if ($label -eq 'Low') { $lowLabeled += $target }
    $probe = Get-ProcessIntegrityFromDirectory -Directory $target
    Write-Report ''
    Write-Report "каталог      : $target"
    Write-Report "метка        : $label"
    Write-Report "процесс из него: $($probe.Integrity) $($probe.Note)"
    Write-Report 'ACL:'
    (icacls $target 2>&1 | Out-String).Trim().Split("`n") | ForEach-Object { Write-Report ('    ' + $_.TrimEnd()) }
}
$control = Get-ProcessIntegrityFromDirectory -Directory ([IO.Path]::GetTempPath())
Write-Report ''
Write-Report "контроль (временный каталог): $($control.Integrity)"

Write-Report ''
Write-Report '--- 2. Проба записи java ---'
$javaBefore = Test-JavaTempWrite -JavaExePath $JavaExe
Write-Report "java         : $JavaExe"
Write-Report "java.io.tmpdir: $($javaBefore.TmpDir)"
Write-Report "temp-файл    : $($javaBefore.TempFile)"
Write-Report "php.ini      : $($javaBefore.PhpIni)"
if ($javaBefore.Detail) { Write-Report "отказы       : $($javaBefore.Detail)" }

$broken = ($lowLabeled.Count -gt 0) -or ($javaBefore.PhpIni -eq 'FAIL')
Write-Report ''
Write-Report ('вывод        : {0}' -f $(if ($broken) {
            'ограничение действует: exe из этих каталогов запускается с низкой целостностью'
        } else {
            'ограничения нет: каталоги без метки Low, java пишет во временный каталог'
        }))

# --- 3. Правка --------------------------------------------------------------

if (-not $Apply) {
    Write-Report ''
    Write-Report '--- 3. Изменений не делалось (ключ -Apply не указан) ---'
    Write-Report 'Чтобы снять метку Low, повторите запуск в консоли администратора с -Apply.'
    exit $(if ($broken) { 1 } else { 0 })
}
if (-not $isAdmin) {
    Write-Report ''
    Write-Report 'внимание: процесс работает без прав администратора.'
    Write-Report 'метка изменится, если объект подконтролен пользователю (замерено: на своих каталогах это работает);'
    Write-Report 'при отказе в доступе повторите запуск в консоли администратора.'
}
if ($lowLabeled.Count -eq 0) {
    Write-Report ''
    Write-Report 'снимать нечего: каталогов с меткой Low нет'
    exit $(if ($javaBefore.PhpIni -eq 'FAIL') { 1 } else { 0 })
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupDir = Join-Path ([IO.Path]::GetTempPath()) "ragraf-acl-backup-$stamp"
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
Write-Report ''
Write-Report "--- 3. Правка (резервная копия: $backupDir) ---"
foreach ($target in $lowLabeled) {
    $name = ($target -replace '[:\\ ]', '_')
    icacls $target /save (Join-Path $backupDir "acl-$name.txt") /T /C /Q | Out-Null
    (icacls $target 2>&1 | Out-String) | Set-Content -LiteralPath (Join-Path $backupDir "acl-readable-$name.txt") -Encoding utf8NoBOM
    Write-Report "резервная копия ACL: $(Join-Path $backupDir "acl-$name.txt") (метку icacls не сохраняет — она выше в журнале)"

    if ($PSCmdlet.ShouldProcess($target, "снять метку Low: /setintegritylevel (OI)(CI)$TargetLabel")) {
        $out = (icacls $target /setintegritylevel "(OI)(CI)$TargetLabel" 2>&1 | Out-String).Trim()
        Write-Report "setintegritylevel -> $out"
        if ($out -match '(?i)denied|отказано') { $script:AccessDenied = $true }
    }
    if ($RemoveDenyDeleteAcl) {
        if ($PSCmdlet.ShouldProcess($target, 'снять все запрещающие ACE для Everyone')) {
            $out = (icacls $target /remove:d Everyone 2>&1 | Out-String).Trim()
            Write-Report "remove:d Everyone -> $out"
        }
    }
}

# --- 4. Проверка после правки ----------------------------------------------

Write-Report ''
Write-Report '--- 4. Проверка после правки ---'
$stillLow = 0
foreach ($target in $lowLabeled) {
    $label = Get-DirectoryLabel -Directory $target
    $probe = Get-ProcessIntegrityFromDirectory -Directory $target
    if ($probe.Integrity -eq 'Low') { $stillLow++ }
    Write-Report "$target : метка=$label, процесс из каталога=$($probe.Integrity)"
}
$javaAfter = Test-JavaTempWrite -JavaExePath $JavaExe
Write-Report "java.io.tmpdir: $($javaAfter.TmpDir)"
Write-Report "temp-файл    : $($javaAfter.TempFile)"
Write-Report "php.ini      : $($javaAfter.PhpIni)"
if ($javaAfter.Detail) { Write-Report "отказы       : $($javaAfter.Detail)" }

Write-Report ''
Write-Report '--- 5. Что дальше ---'
if ($stillLow -eq 0 -and $javaAfter.PhpIni -eq 'OK') {
    Write-Report 'метка снята, java снова пишет во временный каталог.'
    Write-Report 'пересоберите корпус с CPG:'
    Write-Report '    cd <каталог проекта>'
    Write-Report '    python tools\ragraf.py -p config\ragraf.yaml reindex'
    Write-Report 'в index_status стадия cpg должна дать items>0, а capability=full — не «перенос из прошлой ревизии».'
    exit 0
}
Write-Report 'ограничение сохранилось. Что проверить под администратором:'
Write-Report '    reg query HKLM\SECURITY\Policy\Safer /s      # правила Software Restriction Policies'
Write-Report 'Если правила SRP на этот путь нет — ограничение ставит сторонний продукт защиты'
Write-Report '(на машине работает Kaspersky) либо метка возвращается другим механизмом.'
Write-Report 'Обходной путь без правки политики: держать записи java внутри дерева'
Write-Report '(переменные TEMP/TMP и -Djava.io.tmpdir на каталог внутри проекта).'
if ($script:AccessDenied) {
    Write-Report 'изменение метки отклонено системой: повторите запуск в консоли администратора.'
    exit 2
}
exit 1
