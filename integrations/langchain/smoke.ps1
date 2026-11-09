#Requires -Version 7.6
#Requires -PSEdition Core
<#
.SYNOPSIS
    Смоук интеграции RAGRAF ↔ LangChain: получена ли ссылка path:line.

.DESCRIPTION
    Уровень L2 по правилам проекта (docs/03_implementation/AGENT-INTEGRATIONS.md):
    клиент вызвал поиск, и в ответе есть адрес вида путь:строка. Проверка не
    оценивает качество ответа модели — только то, что данные дошли до LangChain
    в виде, который можно цитировать.

    Коды возврата:
      0 — retriever и инструменты вернули path:line;
      1 — интеграция работает, но адреса нет;
      2 — окружение не готово (нет venv, нет Python, нет конфигурации корпуса);
      3 — прямой путь в порядке, недоступен MCP-путь (langchain.mcp).

.PARAMETER Python
    Интерпретатор, в котором установлены langchain и зависимости RAGRAF.
    По умолчанию — переменная окружения RAGRAF_LANGCHAIN_PYTHON, иначе окружение
    .venvs\langchain рядом с каталогом проекта, иначе `python` из PATH.
    Путь машины в файле не держится: проверяльщик поставки считает такой путь находкой.

.PARAMETER Config
    Конфигурация корпуса; по умолчанию config\ragraf.yaml корня проекта.

.EXAMPLE
    pwsh -NoProfile -File integrations/langchain/smoke.ps1
#>
[CmdletBinding()]
param(
    [string]$Python = '',
    [string]$Config = '',
    [switch]$NoMcp
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$packageDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent (Split-Path -Parent $packageDir)
# Интерпретатор: параметр, затем переменная окружения, затем окружение рядом с
# проектом, затем `python` из PATH. Ни один из этих путей не вписан в файл.
if (-not $Python) { $Python = $env:RAGRAF_LANGCHAIN_PYTHON }
if (-not $Python) {
    $candidate = Join-Path (Split-Path -Parent $projectRoot) '.venvs\langchain\Scripts\python.exe'
    if (Test-Path -LiteralPath $candidate) { $Python = $candidate }
}
if (-not $Python) {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { $Python = $command.Source }
}
if (-not $Config) { $Config = Join-Path $projectRoot 'config\ragraf.yaml' }

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "не готово: нет интерпретатора $Python" -ForegroundColor Red
    Write-Host '  поставьте окружение и укажите его: -Python <путь> или RAGRAF_LANGCHAIN_PYTHON' 
    Write-Host '  затем: pip install langchain "langchain[mcp]" -r requirements.txt'
    exit 2
}
if (-not (Test-Path -LiteralPath $Config)) {
    Write-Host "не готово: нет конфигурации корпуса $Config" -ForegroundColor Red
    exit 2
}

$arguments = @('-u', '-m', 'ragraf_langchain.smoke', '--config', $Config)
if ($NoMcp) { $arguments += '--no-mcp' }

Push-Location -LiteralPath $packageDir
try {
    $output = & $Python @arguments 2>&1
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}
$output | ForEach-Object { Write-Host $_ }

# Главный признак: адрес path:line. Без него ответ модели нечем подтвердить.
$address = $output | Select-String -Pattern '\S+\.\w+:\d+' | Select-Object -First 1
if ($address) {
    Write-Host "`npath:line = $($address.Matches[0].Value)" -ForegroundColor Green
}
else {
    Write-Host "`npath:line не найден: интеграция не дала доказательства" -ForegroundColor Red
    if ($code -eq 0) { $code = 1 }
}

switch ($code) {
    0 {
        if ($NoMcp) { Write-Host 'смоук: ок (прямой путь; MCP не проверялся — -NoMcp)' -ForegroundColor Green }
        else { Write-Host 'смоук: ок (прямой путь и MCP)' -ForegroundColor Green }
    }
    1 { Write-Host 'смоук: провал проверки' -ForegroundColor Red }
    2 { Write-Host 'смоук: окружение не готово' -ForegroundColor Yellow }
    3 { Write-Host 'смоук: прямой путь ок, MCP-путь недоступен (см. причину выше)' -ForegroundColor Yellow }
    default { Write-Host "смоук: неизвестный код $code" -ForegroundColor Yellow }
}
exit $code
