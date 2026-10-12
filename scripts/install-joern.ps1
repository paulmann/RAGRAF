#Requires -Version 7.6
#Requires -PSEdition Core
<#
.SYNOPSIS
    Установка Joern CLI: распаковка архива и проверка работоспособности.

.DESCRIPTION
    Joern — единственный доступный инструмент с полным CPG (AST+CFG+DFG+PDG).
    Требует JVM: используется Temurin JDK 21 из .tools/jdk.

    Что проверяется после распаковки:
      * найден исполняемый файл joern (или joern.bat);
      * java отвечает на -version;
      * у Joern доступен фронтенд php (php2cpg) — от этого зависит слой CPG для пилота.

    Скрипт идемпотентен: повторный запуск не перезаписывает уже распакованный Joern.

.EXAMPLE
    pwsh -NoLogo -NoProfile -NonInteractive -File .\scripts\install-joern.ps1
#>
[CmdletBinding()]
param(
    [string]$Archive = '',
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding =
    [System.Text.UTF8Encoding]::new($false)

$toolsDir = Join-Path (Split-Path -Parent $PSScriptRoot) '.tools'
if (-not $Archive) {
    $Archive = Join-Path $toolsDir 'downloads\joern-cli.zip'
}
$joernDir = Join-Path $toolsDir 'joern-cli'
$jdkDir = Join-Path $toolsDir 'jdk'

function Write-Step {
    param([string]$Text)
    Write-Host "== $Text"
}

Write-Step 'Проверка предпосылок'

if (-not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
    throw "Архив Joern не найден: $Archive"
}
$archiveSize = (Get-Item -LiteralPath $Archive).Length
Write-Host "   архив: $([math]::Round($archiveSize / 1MB, 1)) МБ"

$java = Join-Path $jdkDir 'bin\java.exe'
if (-not (Test-Path -LiteralPath $java)) {
    throw "JDK не найден: $java. Установите JDK 21 в .tools\jdk."
}
$javaVersion = (& $java -version 2>&1 | Select-Object -First 1)
Write-Host "   java: $javaVersion"

Write-Step 'Распаковка Joern'

if ((Test-Path -LiteralPath $joernDir) -and -not $Force) {
    Write-Host '   каталог уже существует, распаковка пропущена (используйте -Force для перезаписи)'
}
else {
    if (Test-Path -LiteralPath $joernDir) {
        Remove-Item -LiteralPath $joernDir -Recurse -Force
    }
    $tmp = Join-Path $toolsDir 'joern-unpack'
    if (Test-Path -LiteralPath $tmp) {
        Remove-Item -LiteralPath $tmp -Recurse -Force
    }
    Write-Host '   распаковка (может занять несколько минут)...'
    Expand-Archive -LiteralPath $Archive -DestinationPath $tmp -Force
    # Архив содержит каталог верхнего уровня: переносим его содержимое как есть.
    $inner = Get-ChildItem -LiteralPath $tmp -Directory | Select-Object -First 1
    if ($inner) {
        Move-Item -LiteralPath $inner.FullName -Destination $joernDir -Force
        Remove-Item -LiteralPath $tmp -Recurse -Force
    }
    else {
        Move-Item -LiteralPath $tmp -Destination $joernDir -Force
    }
    Write-Host '   распаковано'
}

Write-Step 'Поиск исполняемого файла'

$candidates = @('joern.bat', 'joern', 'joern-cli\joern.bat', 'joern-cli\joern')
$joernExe = $null
foreach ($name in $candidates) {
    $p = Join-Path $joernDir $name
    if (Test-Path -LiteralPath $p) {
        $joernExe = $p
        break
    }
}
if (-not $joernExe) {
    $found = Get-ChildItem -LiteralPath $joernDir -Recurse -File -Filter 'joern*' -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in '.bat', '', '.sh' } |
        Select-Object -First 1
    if ($found) { $joernExe = $found.FullName }
}
if (-not $joernExe) {
    throw "Не найден исполняемый файл Joern в $joernDir"
}
Write-Host "   найден: $joernExe"

Write-Step 'Проверка запуска Joern'
$env:JAVA_HOME = $jdkDir
$env:PATH = "$(Join-Path $jdkDir 'bin');$env:PATH"

$helpOut = ''
try {
    $helpOut = (& $joernExe --help 2>&1 | Out-String)
    Write-Host "   Joern ответил, длина вывода: $($helpOut.Length)"
}
catch {
    Write-Host "   предупреждение: запуск --help завершился ошибкой: $($_.Exception.Message)"
}

Write-Step 'Проверка фронтенда php'
# php2cpg — ключевая возможность для пилота: без него слой CPG для PHP недоступен.
$phpSupport = $helpOut -match 'php'
Write-Host "   поддержка php в выводе: $(if ($phpSupport) { 'да' } else { 'не подтверждена (проверьте вручную: --language PHP)' })"

Write-Step 'Результат'
[ordered]@{
    joern_path    = $joernExe
    jdk_home      = $jdkDir
    java          = "$javaVersion"
    php_confirmed = $phpSupport
    archive_mb    = [math]::Round($archiveSize / 1MB, 1)
} | Format-List | Out-String | Write-Host

Write-Host 'Для включения CPG в индексации: RAGRAF_JOERN_HOME и RAGRAF_JAVA_HOME указывают на эти каталоги.'
exit 0
