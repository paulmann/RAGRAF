<#
.SYNOPSIS
  Одна запись моста хуков в профиле DSH вместо трёх (§3.2, починка 21.10.2026).

.DESCRIPTION
  ИЗМЕРЕННЫЙ ДЕФЕКТ. Мост `@deepseek-ai/dsh-hooks-claude-code` — один плагин
  (`hooks-claude-code`). В профиле стояли три записи insert этого пакета (xerj,
  RAGRAF, bsg), каждая со своим configPath. За неделю гейт RAGRAF не принял ни
  одного решения: в `catalog/gate_state.json` всё от 14.10 — дня установки, хотя
  сеансы начинались 15, 16, 18 и 21 октября. SessionStart-хук матчера не имеет и
  обязан срабатывать на каждом старте — значит конфиг RAGRAF не был смонтирован.

  РЕШЕНИЕ. Свод всех хуков в один файл `harness\all-hooks.json` (сборщик —
  `tools\merge_hooks.py`) и одна запись `hooks-shared` в профиле. Проектные
  конфиги остаются источниками правды.

  Кто что делает: план правки и проверку YAML считает python
  (`tools\patch_hooks_mount.py`) — у него песочница «только рабочий каталог»,
  поэтому ПИСАТЬ в `~/.dsh` может только этот скрипт (PowerShell).

.PARAMETER Check
  Только показать состояние, ничего не менять.

.EXAMPLE
  pwsh -NoLogo -NoProfile -NonInteractive -File tools\install_harness_gate.ps1
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [string]$PatchPath = (Join-Path $env:USERPROFILE '.dsh\profiles\desktop\cordis.patch.yml')
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo '.venv\Scripts\python.exe'
$shared = Join-Path $repo 'harness\all-hooks.json'

Write-Output "патч профиля: $PatchPath"

# 1. Свод должен быть собран из источников и совпадать с ними.
if (-not (Test-Path -LiteralPath $shared)) { throw "нет свода хуков: $shared (сначала: python tools\merge_hooks.py)" }
& $py (Join-Path $repo 'tools\merge_hooks.py') --check | ForEach-Object { "  $_" }
if ($LASTEXITCODE -ne 0) { throw "свод разошёлся с источниками: python tools\merge_hooks.py" }

# 2. Текущее состояние.
$state = & $py (Join-Path $repo 'tools\patch_hooks_mount.py') --verify $PatchPath 2>&1
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1) { throw "не удалось проверить патч: $state" }
$stateJson = $state | ConvertFrom-Json
Write-Output ("  записей моста хуков: {0} [{1}]" -f @($stateJson.hook_ids).Count, (@($stateJson.hook_ids) -join ', '))
Write-Output ("  всего записей верхнего уровня: {0}" -f $stateJson.entries)

if ($Check) {
    if ($stateJson.ok) { Write-Output '  состояние верное: одна сводная запись'; exit 0 }
    Write-Output '  ТРЕБУЕТСЯ ПРАВКА: мост хуков смонтирован не одной записью'
    exit 1
}
if ($stateJson.ok) {
    Write-Output '  уже одна сводная запись — правка не нужна (идемпотентно)'
    exit 0
}

# 3. План правки (python считает, PowerShell применяет).
$planRaw = & $py (Join-Path $repo 'tools\patch_hooks_mount.py') 2>&1
if ($LASTEXITCODE -ne 0) { throw "план правки не построен: $planRaw" }
$plan = $planRaw | ConvertFrom-Json
$newPatch = Join-Path $repo 'harness\.patch-new.yml'
Write-Output ("  будет удалено записей моста: [{0}]" -f (@($plan.removed_hook_ids) -join ', '))
Write-Output ("  ожидаемые записи после правки: [{0}]" -f (@($plan.expected_ids) -join ', '))

# 4. Резервная копия и замена.
$backup = "$PatchPath.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item -LiteralPath $PatchPath -Destination $backup -Force
Write-Output "  резервная копия: $backup"
$newText = Get-Content -LiteralPath $newPatch -Raw -Encoding UTF8
Set-Content -LiteralPath $PatchPath -Value $newText -Encoding UTF8

# 5. Проверка после правки; при расхождении — откат.
$verifyRaw = & $py (Join-Path $repo 'tools\patch_hooks_mount.py') --verify $PatchPath 2>&1
$verifyJson = $verifyRaw | ConvertFrom-Json
if (-not $verifyJson.ok) {
    Copy-Item -LiteralPath $backup -Destination $PatchPath -Force
    throw "ОТКАТ: после правки проверка не прошла: $verifyRaw"
}
$missing = @($plan.expected_ids | Where-Object { @($verifyJson.all_ids) -notcontains $_ })
$extra = @($verifyJson.all_ids | Where-Object { @($plan.expected_ids) -notcontains $_ })
if ($missing.Count -gt 0 -or $extra.Count -gt 0) {
    Copy-Item -LiteralPath $backup -Destination $PatchPath -Force
    throw "ОТКАТ: набор записей разошёлся. пропало: [$($missing -join ', ')]; появилось: [$($extra -join ', ')]"
}

Write-Output '  Готово: в профиле одна запись hooks-shared.'
Write-Output '  Дальше: ПЕРЕЗАПУСТИТЬ приложение DSH — конфиг хуков читается один раз при старте.'
Write-Output '  После перезапуска: pwsh -File tools\install_harness_gate.ps1 -Check; затем вызвать'
Write-Output '  grep/read и проверить, что в catalog\gate_state.json появился новый сеанс.'
exit 0
