#Requires -Version 7.6
#Requires -PSEdition Core
<#
Песочница для проверки установки плагина RAGRAF в DeepSeek Harness.

Зачем отдельный DSH_HOME. Установка правит профиль клиента (слой патчей, список
пакетов) и файлы `$DSH_HOME/ragraf`. Проверять такое на живом профиле нельзя:
ошибка в маске правки снимает чужие записи, и заметно это только после
перезапуска клиента. Поэтому профиль собирается здесь — с боевыми ИМЕНАМИ
записей (mcp-ragraf, hooks-shared, agent-instructions) и приманками, которых
правка касаться не должна.

Приманки: запись ui-chat (чужая настройка), сервер mcp-xerj (чужой MCP) и
AGENTS.md размером больше базового бюджета 65 536 — последний нужен, чтобы
проверить именно ту причину, из-за которой правила не доезжали до модели.
#>
[CmdletBinding()]
param(
    [string]$Root = (Join-Path ([IO.Path]::GetTempPath()) 'dsh-sbx'),
    [int]$AgentsBytes = 156000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (Test-Path -LiteralPath $Root) { Remove-Item -Recurse -Force -LiteralPath $Root }
$sbxHome = Join-Path $Root 'home'
$profileDir = Join-Path $sbxHome 'profiles\desktop'
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null

# AGENTS.md — «большой глобальный файл»; содержимое не важно, важен размер.
$line = 'строка правил ' * 30 + "`n"
$builder = [System.Text.StringBuilder]::new()
while ($builder.Length -lt $AgentsBytes) { [void]$builder.Append($line) }
[IO.File]::WriteAllText((Join-Path $sbxHome 'AGENTS.md'), $builder.ToString(), [Text.UTF8Encoding]::new($false))

[IO.File]::WriteAllText((Join-Path $profileDir 'cordis.yml'), "[]`n", [Text.UTF8Encoding]::new($false))
$package = @'
{
  "name": "dsh-profile-desktop",
  "private": true,
  "dsh": { "profile": { "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"] } },
  "dependencies": {}
}
'@
[IO.File]::WriteAllText((Join-Path $profileDir 'package.json'), $package, [Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $profileDir 'pnpm-workspace.yaml'),
    "packages:`n  - .`n`nnodeLinker: hoisted`nautoInstallPeers: false`n",
    [Text.UTF8Encoding]::new($false))

$patch = @'
# ПРИМАНКА: чужая настройка, обязана остаться
- id: ui-chat
  name: "@deepseek-ai/dsh-client-ui-chat"
  config:
    transcriptView: compact

# Боевая запись, которую пакет должен заменить (имя записи как в живом профиле)
- insert:
    - id: mcp-ragraf
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: ragraf
        transport: stdio
        command: '__PYTHON__'
        args:
          - '__MCP_SCRIPT__'
        cwd: '__PROJECT_ROOT__'
        failOnStartupError: false

# ПРИМАНКА: чужой MCP-сервер, обязан остаться
- insert:
    - id: mcp-xerj
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: xerj
        transport: stdio
        command: '__DECOY_SERVER__'
        args: ['mcp']
        failOnStartupError: false

- insert:
    - id: hooks-shared
      name: '@deepseek-ai/dsh-hooks-claude-code'
      config:
        configPath: __PROJECT_ROOT__\harness\all-hooks.json
        defaultTimeoutMs: 60000

# Бюджет на уровне базового слоя: именно он отбрасывал файл целиком
- id: agent-instructions
  name: '@deepseek-ai/dsh-agent-instructions'
  config:
    maxBytes: 65536
'@
# Пути машины в самом скрипте не держим: проверяльщик поставки считает «путь
# внутрь приватного дерева» находкой (правило 24 в AGENTS.md), а песочнице нужен
# любой валидный путь. Токены выше подставляются здесь, при запуске.
$projectRoot = Split-Path -Parent $PSScriptRoot
$substitutions = [ordered]@{
    '__PYTHON__'       = Join-Path $projectRoot '.venv\Scripts\python.exe'
    '__MCP_SCRIPT__'   = Join-Path $projectRoot 'tools\ragraf-mcp.py'
    '__PROJECT_ROOT__' = $projectRoot
    '__DECOY_SERVER__' = Join-Path $Root 'xerj-decoy.exe'
}
foreach ($pair in $substitutions.GetEnumerator()) {
    $patch = $patch.Replace($pair.Key, $pair.Value)
}
[IO.File]::WriteAllText((Join-Path $profileDir 'cordis.patch.yml'), $patch, [Text.UTF8Encoding]::new($false))

Write-Output "sandbox DSH_HOME: $sbxHome"
Write-Output ("AGENTS.md: {0} байт" -f (Get-Item -LiteralPath (Join-Path $sbxHome 'AGENTS.md')).Length)
Get-ChildItem -Recurse -LiteralPath $Root -File | Select-Object Length, Name | Format-Table -AutoSize
