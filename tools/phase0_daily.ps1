#Requires -Version 7.6
#Requires -PSEdition Core
<#
.SYNOPSIS
    Ежедневное обновление страницы «Фаза 0» в разделе /md/ сайта ragraf.ru.

.DESCRIPTION
    Порядок:
      1) пересобрать HTML из свежих данных журнала (tools/phase0_report.py);
      2) положить в клон сайта, закоммитить под идентичностью владельца;
      3) отправить в GitHub;
      4) опубликовать на боевом хосте (/usr/local/bin/ragraf-site-deploy на 176.99.5.35
         через транзит 192.168.31.31 — прямого доступа с рабочей машины нет);
      5) проверить, что страница действительно отдаётся (HTTP 200).

    Идентичность коммита берётся из истории репозитория (mid1977@gmail.com);
    переменные GIT_AUTHOR_* снимаются, иначе они сильнее `git -c`.

.NOTES
    Запуск:
      pwsh -NoLogo -NoProfile -NonInteractive -File tools\phase0_daily.ps1
      pwsh ... -File tools\phase0_daily.ps1 -SkipDeploy
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = 'C:\AutoClaw\RAGRAF',
    [string]$SiteRepo = 'C:\AutoClaw\RAGRAF.git\ragraf-site',
    [string]$JumpHost = 'root@192.168.31.31',
    [string]$ProdHost = 'root@176.99.5.35',
    [switch]$SkipDeploy
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
$keyJump = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ssh\id_debian_root'
$pageRel = 'public/md/phase0.html'
$staging = Join-Path $RepoRoot '.ragraf\phase0\phase0.html'

function Write-Step([string]$Text) {
    Write-Host "[phase0] $Text"
}

# --- 1. собрать страницу из данных ----------------------------------------
$python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "не найден python: $python" }
$report = Join-Path $RepoRoot 'tools\phase0_report.py'
if (-not (Test-Path -LiteralPath $report)) { throw "не найден генератор: $report" }

Write-Step 'сборка страницы из журнала'
& $python $report --out $staging
if ($LASTEXITCODE -ne 0) { throw "генератор вернул код $LASTEXITCODE" }

$target = Join-Path $SiteRepo $pageRel
Copy-Item -LiteralPath $staging -Destination $target -Force
Write-Step "страница обновлена: $target ($((Get-Item -LiteralPath $target).Length) байт)"

# --- 2. коммит под идентичностью владельца --------------------------------
$identity = & git -C $SiteRepo log -1 --format='%an|%ae'
if ($LASTEXITCODE -ne 0 -or -not $identity) { throw 'не удалось прочитать идентичность из истории репозитория' }
$parts = $identity.Split('|')
$authorName = $parts[0]
$authorEmail = $parts[1]

foreach ($name in 'GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL') {
    Remove-Item -LiteralPath "Env:\$name" -ErrorAction SilentlyContinue
}
$env:GIT_AUTHOR_NAME = $authorName
$env:GIT_AUTHOR_EMAIL = $authorEmail
$env:GIT_COMMITTER_NAME = $authorName
$env:GIT_COMMITTER_EMAIL = $authorEmail
$env:GIT_TERMINAL_PROMPT = '0'

& git -C $SiteRepo add $pageRel
$status = & git -C $SiteRepo status --porcelain -- $pageRel
if (-not $status) {
    Write-Step 'изменений в странице нет — коммит не нужен'
} else {
    & git -C $SiteRepo -c "user.name=$authorName" -c "user.email=$authorEmail" `
        commit -m "Фаза 0: ежедневное обновление журнала $(Get-Date -Format 'dd.MM.yyyy')"
    if ($LASTEXITCODE -ne 0) { throw "коммит не удался (код $LASTEXITCODE)" }
    $check = & git -C $SiteRepo log -1 --format='%an <%ae>'
    Write-Step "подпись коммита: $check"
    if ($check -match 'zwork') { throw 'коммит подписан агентом — публиковать нельзя' }

    Write-Step 'отправка в GitHub'
    & git -C $SiteRepo push origin main
    if ($LASTEXITCODE -ne 0) { throw "push не удался (код $LASTEXITCODE)" }
}

# --- 3. публикация на боевом хосте ---------------------------------------
if ($SkipDeploy) {
    Write-Step 'публикация пропущена (-SkipDeploy)'
} else {
    Write-Step 'публикация на 176.99.5.35 через транзит 192.168.31.31'
    $remote = "ssh -i /root/.ssh/id_mig -o BatchMode=yes $ProdHost '/usr/local/bin/ragraf-site-deploy 2>&1 | tail -3'"
    & $ssh -i $keyJump -o BatchMode=yes $JumpHost $remote
    if ($LASTEXITCODE -ne 0) { throw "публикация не удалась (код $LASTEXITCODE)" }
}

# --- 4. проверка фактической отдачи --------------------------------------
Write-Step 'проверка страницы по HTTP'
$code = (& curl.exe -sk -o NUL -w '%{http_code}' 'https://ragraf.ru/md/phase0.html').Trim()
Write-Step "https://ragraf.ru/md/phase0.html -> $code"
if ($code -ne '200') { throw "страница не отдаётся: HTTP $code" }
Write-Step 'готово'
