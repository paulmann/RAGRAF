#Requires -Version 7.6
#Requires -PSEdition Core
<#
.SYNOPSIS
    Смоук интеграции OpenCode: клиент обязан вызвать RAGRAF и привести path:line.

.DESCRIPTION
    Коды возврата: 0 — ссылка получена; 1 — клиент ответил, но ссылки нет;
    2 — клиент не найден в PATH; 3 — клиент не авторизован (или провайдер не
    настроен). Код 3 не «провал интеграции»: это состояние клиента, и оно
    называется, а не маскируется.

    Что проверяется: RAGRAF отдаёт агенту проверяемый факт. Пересказ без адреса
    считается непройденной проверкой — иначе «поддержка» означала бы «клиент
    что-то ответил».
#>
[CmdletBinding()]
param(
    [string]$Invoke = 'run',
    [int]$TimeoutSec = 180
)

$ErrorActionPreference = 'Stop'
$prompt = 'Вызови инструмент ragraf_ensure для текущего корпуса, затем ragraf_search с запросом "onboarding" и приведи ОДНУ ссылку вида path:line из ответа. Без ссылки ответ не считается.'

$exe = Get-Command 'opencode' -ErrorAction SilentlyContinue
if (-not $exe) {
    Write-Host 'OpenCode: клиент opencode не найден в PATH (код 2)' -ForegroundColor Yellow
    exit 2
}

$parts = @()
if ($Invoke) {
    $parts += $Invoke.Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries)
}
$parts += $prompt

try {
    $output = & $exe.Source @parts 2>&1 | Out-String
    $code = $LASTEXITCODE
} catch {
    Write-Host "OpenCode: запуск не удался: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

if ($output -match 'Not logged in|not logged in|API key|unauthorized|Unauthorized|Missing API|requires credentials|trusted directory|configure gateway|OPENAI_API_KEY|ANTHROPIC_API_KEY') {
    Write-Host 'OpenCode: клиент не готов (нет входа, ключа или доверия к каталогу) — L2 не измерен (код 3)' -ForegroundColor Yellow
    Write-Host (($output.Trim() -split "`n") | Select-Object -First 3)
    exit 3
}

if ($output -match '[\w./\\-]+:\d+') {
    Write-Host 'OpenCode: ссылка path:line получена (код 0)' -ForegroundColor Green
    Write-Host ((($output | Select-String -Pattern '[\w./\\-]+:\d+' | Select-Object -First 1).Line).Trim())
    exit 0
}

Write-Host "OpenCode: клиент ответил кодом $code, но ссылки path:line нет (код 1)" -ForegroundColor Red
Write-Host (($output.Trim() -split "`n") | Select-Object -First 5)
exit 1
