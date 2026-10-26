# Источники поставки: откуда берётся каждый файл обвязки

Документ отвечает на один вопрос: **откуда взялся каждый файл поставки и чем это
подтверждено**. Он появился потому, что обвязка существовала только в публикации:
живое дерево не содержало ни `CHANGELOG`, ни `CONTRIBUTING`, ни `SECURITY`, ни
`CITATION`, а `.github/**` было старее публичного набора. Сборка «из живого дерева»
в таком состоянии теряла обвязку целиком — и заметить это можно было только
сверкой, которой до сих пор никто не делал.

**Публичный `main` на момент сверки:** `18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf`
(2026-10-26 23:49 MSK, «выпуск 0.4.0 — брокер секретов, снятые обещания»),
репозиторий https://github.com/paulmann/RAGRAF, клон в песочнице
`%TEMP%\ragraf-pub-0.4.0` (`git clone`, затем
`git fetch origin`, `git rev-parse origin/main`, `git archive origin/main`).

Проверка этих утверждений — `tools/check_community_wrapper.py` (24-я проверка
чекера конвенций). Она сверяет три вещи: файл, строку таблицы и признак
совпадения. Состав поставки проверяют `tools/release/**` и 25-я проверка
(`tools/check_release_manifest.py`): манифест, сборщик, проверяльщик и гейт. Молчаливого пропуска нет: файл без строки в таблице — провал,
строка с неверным хэшем — провал, признак «совпадает» при различных хэшах — провал.

## 1. Таблица источников

Таблица пересчитана **после** публикации 0.4.0 (голова `18d7bfe`): все 23 строки
совпадают с публичным `main` побайтово, расхождений нет. Прежние редакции этого
абзаца называли 27 строк — число было от прежнего состава обвязки, а не измерением
сегодняшнего; актуальное число строк равно числу файлов `community/**`, и это
проверяется, а не запоминается. Сверка не «на глаз»:
`tools/check_community_wrapper.py` (24-я проверка чекера) сравнивает три вещи —
файл, строку таблицы и признак совпадения — и падает на любом расхождении, включая
случай «написано да, а хэши разные».

Порядок при следующей публикации: выпуск уходит в `main`, затем таблица
пересчитывается на новой голове. Таблица, оставленная на прежней голове, — это
описание прошлого, а не текущего состояния.


> **Изменение 26.10.2026 (этап И17).** `community/EULA.md` переписан: поставка
> одна, Pro и Enterprise — уровни лицензии внутри неё, переход между уровнями —
> активация лицензии, а не установка (решение владельца от 26.10.2026). Выпуск
> отправлен, хэши совпадают, адрес источника указывает на голову выпуска.

> **Изменение 26.10.2026 (этап И18).** В `community/EULA.md` добавлен уровень
> Pro — `secret-broker` переведён в предмет (код есть и проверен), а `connectors`
> и `fleet` сняты из предмета всех уровней и названы в плане развития. Тогда выпуск
> НЕ отправлялся, и строка честно говорила «не совпадает с main».

> **Изменение 26.10.2026 (этап И19).** Выпуск 0.4.0 опубликован: голова
> `18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf`, тег `v0.4.0` на ней же. Строки
> `community/EULA.md`, `community/CHANGELOG.md`, `community/CITATION.cff` и
> `community/ROADMAP.md` пересчитаны на этой голове — все четыре теперь «да»,
> расхождений в таблице нет. Таблица, оставленная на прежней голове, была бы
> описанием прошлого; поэтому порядок «сначала выпуск, потом таблица» и соблюдён.

Хэши — sha256, полные (64 знака). Столбец «совпадает» означает: файл в дереве
побайтово равен файлу публичного `main`, то есть перенос выполнен без правки
содержимого.

| файл в дереве | источник (URL + коммит) | sha256 в публичном main | sha256 в дереве | совпадает | путь в поставке |
|---|---|---|---|---|---|
| community/CHANGELOG.md | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/CHANGELOG.md | b5ea007e9b8bccf1e5cf8752c4543c6c909d5bef4972113b98cfdfe8a564abed | b5ea007e9b8bccf1e5cf8752c4543c6c909d5bef4972113b98cfdfe8a564abed | да | CHANGELOG.md |
| community/CITATION.cff | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/CITATION.cff | 5c308089f1f5ac1587a2639b97cb1e1f5fbefb836f81e2661919015af4951232 | 5c308089f1f5ac1587a2639b97cb1e1f5fbefb836f81e2661919015af4951232 | да | CITATION.cff |
| community/CODE_OF_CONDUCT.md | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/CODE_OF_CONDUCT.md | 34596ad809e62b3c5a0f9cfb3106c971c21b4f145b18c7e70704959d6996b580 | 34596ad809e62b3c5a0f9cfb3106c971c21b4f145b18c7e70704959d6996b580 | да | CODE_OF_CONDUCT.md |
| community/CONTRIBUTING.md | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/CONTRIBUTING.md | 4702989e9f57c205f752d6e5977b692316fae0fa4c61c32355d5289e05d98689 | 4702989e9f57c205f752d6e5977b692316fae0fa4c61c32355d5289e05d98689 | да | CONTRIBUTING.md |
| community/install.ps1 | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/install.ps1 | 5a55c8d88ba70066e43147d9b772d98352f46ca82a88ef18a2d8ada71509e8e0 | 5a55c8d88ba70066e43147d9b772d98352f46ca82a88ef18a2d8ada71509e8e0 | да | install.ps1 |
| community/mkdocs.yml | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/mkdocs.yml | fafbfabe326de2139b7075cc831c358729070a28f0e1b90baa26dbf8bcd4a0e4 | fafbfabe326de2139b7075cc831c358729070a28f0e1b90baa26dbf8bcd4a0e4 | да | mkdocs.yml |
| community/README.de.md | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/README.de.md | a43a47f12b3f2c362f5da1e428a9e3cb6701830c9d9254a14bec3fb9cd3b660f | a43a47f12b3f2c362f5da1e428a9e3cb6701830c9d9254a14bec3fb9cd3b660f | да | README.de.md |
| community/README.es.md | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/README.es.md | 603d4e7f287ab163475ca8a1d745b07187a4be5e5254e61c1276e68d9a55c007 | 603d4e7f287ab163475ca8a1d745b07187a4be5e5254e61c1276e68d9a55c007 | да | README.es.md |
| community/README.hi.md | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/README.hi.md | c8e4bee16f3300f5f9e9b905afa479f18cb30beec54877a7c35aa60869a35cf7 | c8e4bee16f3300f5f9e9b905afa479f18cb30beec54877a7c35aa60869a35cf7 | да | README.hi.md |
| community/README.zh.md | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/README.zh.md | f4252d8b8e51a9aa0c1e7c6375de755b39d6d61c35fb7dbe510cc3fdff88b1de | f4252d8b8e51a9aa0c1e7c6375de755b39d6d61c35fb7dbe510cc3fdff88b1de | да | README.zh.md |
| community/README.ru.md | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/README.ru.md | 0b54d4d486bab6c2d6838175ac15d416b45c37d69f3a1f488ecef50cc5f8cede | 0b54d4d486bab6c2d6838175ac15d416b45c37d69f3a1f488ecef50cc5f8cede | да | README.ru.md |
| community/EULA.md | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/EULA.md | de3a473f73d39dedb64f362384554f25584979d62b51f8ea50c5d531bab75a3a | de3a473f73d39dedb64f362384554f25584979d62b51f8ea50c5d531bab75a3a | да | EULA.md |
| community/ROADMAP.md | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/ROADMAP.md | 1973da88532a9dab0b96873df9acf57575b286af40f4904a36e87ccf5c6457be | 1973da88532a9dab0b96873df9acf57575b286af40f4904a36e87ccf5c6457be | да | ROADMAP.md |
| community/SECURITY.md | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/SECURITY.md | dbc7d9c2bd4d3e2d1481a36d00655633c3b45603d1f6753a26c9357414495700 | dbc7d9c2bd4d3e2d1481a36d00655633c3b45603d1f6753a26c9357414495700 | да | SECURITY.md |
| community/.github/dependabot.yml | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/.github/dependabot.yml | 37360b6fa679b5639b08465c684bca8d86c56f9cdf5b1e8c9dd3e1d813f79532 | 37360b6fa679b5639b08465c684bca8d86c56f9cdf5b1e8c9dd3e1d813f79532 | да | .github/dependabot.yml |
| community/.github/PULL_REQUEST_TEMPLATE.md | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/.github/PULL_REQUEST_TEMPLATE.md | 30c5a2c60f7d1933b201b326d55ce6955aad46457d3d65503c9b666706ab82b0 | 30c5a2c60f7d1933b201b326d55ce6955aad46457d3d65503c9b666706ab82b0 | да | .github/PULL_REQUEST_TEMPLATE.md |
| community/.github/release.yml | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/.github/release.yml | 48c9059c309e7e04177c3e649c02ff3aa5b93b0045a55eff3998004e4356560c | 48c9059c309e7e04177c3e649c02ff3aa5b93b0045a55eff3998004e4356560c | да | .github/release.yml |
| community/.github/ISSUE_TEMPLATE/config.yml | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/.github/ISSUE_TEMPLATE/config.yml | 0bc9435ed1245c47bbde5fc751fb937c75b554553be3010da264be10d2c89654 | 0bc9435ed1245c47bbde5fc751fb937c75b554553be3010da264be10d2c89654 | да | .github/ISSUE_TEMPLATE/config.yml |
| community/.github/ISSUE_TEMPLATE/defect-report.yml | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/.github/ISSUE_TEMPLATE/defect-report.yml | abab94c9ded87699c6046f527486ff84c0abb6d1df8c7f3447baeb25f58fc2a6 | abab94c9ded87699c6046f527486ff84c0abb6d1df8c7f3447baeb25f58fc2a6 | да | .github/ISSUE_TEMPLATE/defect-report.yml |
| community/.github/ISSUE_TEMPLATE/doc-divergence.yml | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/.github/ISSUE_TEMPLATE/doc-divergence.yml | 743c9e238dcaaa3eb10f4739a53f7ec7cab43d81ff3c1d93adfab768adcf3aff | 743c9e238dcaaa3eb10f4739a53f7ec7cab43d81ff3c1d93adfab768adcf3aff | да | .github/ISSUE_TEMPLATE/doc-divergence.yml |
| community/.github/workflows/ci.yml | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/.github/workflows/ci.yml | ea6d8ae5a2007164ca196a0e1b6fc7e71976c9a354ebe55efc50f32d7e6b3a99 | ea6d8ae5a2007164ca196a0e1b6fc7e71976c9a354ebe55efc50f32d7e6b3a99 | да | .github/workflows/ci.yml |
| community/.github/workflows/codeql.yml | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/.github/workflows/codeql.yml | 5c09bbc2cec2fe16c338d4527100b92b63badfc6dbf3dea34b2652d5a0726bfb | 5c09bbc2cec2fe16c338d4527100b92b63badfc6dbf3dea34b2652d5a0726bfb | да | .github/workflows/codeql.yml |
| community/.github/workflows/pages.yml | https://github.com/paulmann/RAGRAF/blob/18d7bfe9584eb94bf3ffd56a4c96420142a5b2cf/.github/workflows/pages.yml | 4735800e7da31eb2b1ef550ccff883867e072118f8edb164b8ddbddd65abeb38 | 4735800e7da31eb2b1ef550ccff883867e072118f8edb164b8ddbddd65abeb38 | да | .github/workflows/pages.yml |
| scripts/install.ps1 | https://github.com/paulmann/RAGRAF/blob/5b98bccd3dc8f1904e930f03e39a55ba4a20d317/scripts/install.ps1 | f6cee5d8bb7f98039c297b578cc86c15fb439e14ed2206289d63382031be5996 | f6cee5d8bb7f98039c297b578cc86c15fb439e14ed2206289d63382031be5996 | да | scripts/install.ps1 |
| scripts/ragraf.ps1 | https://github.com/paulmann/RAGRAF/blob/5b98bccd3dc8f1904e930f03e39a55ba4a20d317/scripts/ragraf.ps1 | f7dbe0bb262b92456945a7863e6263bce3b4678f1cb04ae5f9e81c6a05488813 | f7dbe0bb262b92456945a7863e6263bce3b4678f1cb04ae5f9e81c6a05488813 | да | scripts/ragraf.ps1 |
| scripts/install-joern.ps1 | https://github.com/paulmann/RAGRAF/blob/5b98bccd3dc8f1904e930f03e39a55ba4a20d317/scripts/install-joern.ps1 | c3ebcf0e2c344b95e1fc1d9c3f60937ac87fe6fac7d76351683c8b8faf4f8a27 | c3ebcf0e2c344b95e1fc1d9c3f60937ac87fe6fac7d76351683c8b8faf4f8a27 | да | scripts/install-joern.ps1 |
| scripts/PSScriptAnalyzerSettings.psd1 | https://github.com/paulmann/RAGRAF/blob/5b98bccd3dc8f1904e930f03e39a55ba4a20d317/scripts/PSScriptAnalyzerSettings.psd1 | dd5fbe1cb5be2fa1bc9ca6f9fc5b45b3b1ec40cecb452ae5b2d938c6c9473657 | dd5fbe1cb5be2fa1bc9ca6f9fc5b45b3b1ec40cecb452ae5b2d938c6c9473657 | да | scripts/PSScriptAnalyzerSettings.psd1 |

> **Изменение 24.10.2026 (этап И5).** `community/SECURITY.md` отредактирован в
> живом дереве после переноса из публичного `main`: дефекты C-01 и C-04 закрыты,
> и документ называет, чем именно они проверены и что осталось вне модели угроз.
> Поэтому хэш в дереве больше не равен хэшу публичного `main`, а признак
> «совпадает» стоит «нет» — это заявленное расхождение, а не порча переноса.


**Девять позиций обвязки из задания** — `CITATION.cff`, `CODE_OF_CONDUCT.md`,
`CONTRIBUTING.md`, `SECURITY.md`, `ROADMAP.md`, `README.ru.md`, `CHANGELOG.md`,
`mkdocs.yml`, `.github/**` — на месте. Строк в таблице двадцать две: восемнадцать
файлов `community/**` (по числу файлов; `.github/**` раскрыт по файлам, потому что
проверять каталог целиком значит не заметить потерю одного файла внутри него),
`community/install.ps1` отдельным решением (раздел 2) и четыре файла `scripts/`
(раздел 4). Признак «совпадает» означает побайтовое равенство публичному `main`:
пять строк обвязки помечены «нет» после правки лицензионных текстов (раздел 5).

Каталога `community/` до этого изменения не существовало: перенос аддитивен,
ни один файл владельца не удалён и не перезаписан этим переносом.

## 2. Решение по установщику

Установщик существовал в **трёх** версиях, а не в двух. Измерено:

| версия | размер | строк | sha256 (12) | функции | где |
|---|---|---|---|---|---|
| живое дерево (было) | 34 242 | 588 | ca4407fb1a47 | 5 | `scripts/install.ps1` |
| публичный main, `scripts/` | 37 794 | 803 | 223530f6ded7 | 10 | репозиторий, коммит 284ba03 (2026-10-12) |
| публичный main, корень | 51 062 | — | 4ad85ec94240 | 26 | репозиторий, коммит 6341be8 (2026-10-16) |
| старая сборочная копия | 37 794 | 803 | 223530f6ded7 | 10 | `.staging/release-v0.2/RAGRAF/scripts/install.ps1` |

**База выбрана по содержимому: `scripts/install.ps1` публичного `main`** (37 794
байта, sha256 `223530f6ded7…`). Основания, каждое проверено:

1. В ней есть исправления D-01…D-12: `Stop-Install`, `Close-InstallTranscript`,
   `Write-SummaryJson`, `Get-FreeGb`, `Get-LastErrorLine`, `Invoke-Ragraf`,
   плюс параметры `-NoTranscript`, `-SummaryJson` и `-TimeoutSec` у
   `Invoke-Checked`. В живой версии всего этого не было.
2. **Живой CI требовал того, чего живой установщик не умел.** Замер:
   `.github/workflows/ci.yml:125` вызывал
   `scripts/install.ps1 -SummaryJson install-summary.json`, а живая версия
   параметра `$SummaryJson` не объявляла вовсе — то есть CI в этом дереве
   был бы красным на шаге установки. Это не мнение о версиях, а несогласие
   интерфейса внутри одного дерева.
3. Старая сборочная копия содержит ровно тот же файл (хэши совпадают), поэтому
   «взять из 14.10» и «взять из публичного main» здесь дают одно и то же.

**Что перенесено из отвергнутой версии:** ничего, и это утверждение проверено,
а не предположено. Единственная функция, которой не было в выбранной версии, —
`Fail-Out` (живая строка 139). Её роль целиком закрыта `Stop-Install` (строка 174):
тело совпадает шаг в шаг — `Write-Host "STOP: …"`, `Show-Summary`, `exit $Code` —
и добавлены два действия, которых у `Fail-Out` не было: `Write-SummaryJson` и
`Close-InstallTranscript`. Коды возврата те же (`0/1/2/3`,
`$script:ExitOk/ExitError/ExitUsage/ExitEnvironment`), поэтому менять контракт
не пришлось. Функции `Get-FreeGb` и `Get-LastErrorLine` в живой версии
отсутствовали, а проверка свободного места и разбор последней строки ошибки
в выбранной версии есть — то есть выбранная версия строже, а не мягче.

**Корневой установщик.** В живом дереве он теперь есть как
`community/install.ps1` — побайтовая копия корневого `install.ps1` публикации
(51 062 байта, sha256 `4ad85ec94240…`). Это отдельное решение, названное прямо:
без него корневой установщик — тот самый постоянный адрес, на который указывает
инструкция пользователю, — существовал бы **только в публикации**, и следующая
сборка из живого дерева потеряла бы точку входа. Роль двух файлов разная и
пересечения не имеют:

* `community/install.ps1` — установщик развёртывания: скачивает пакет, разворачивает
  каталог, подключает клиентов, вызывает `scripts/install.ps1` как штатный путь;
* `scripts/install.ps1` — установщик зависимостей: venv, пакет, проверка
  работоспособности, отчёт для CI.

Второй копии в дереве нет: проверка обходит все `install.ps1` вне `community/`,
`.staging`, `.venv`, `.tools`, `.zwork`, `.ragraf` и требует ровно одного файла —
`scripts/install.ps1`, — и разбирает его парсером PowerShell.

**Проверено после замены:** размер 37 794, sha256 `223530f6ded7…`, 803 строки,
`Parser::ParseFile` — 0 ошибок. Прежняя версия сохранена:
`.ragraf/backup-stage2/install.ps1.bak-20261024-stage2` (34 242 байта, хэш сверен).

## 3. Решение по `.github/**`

Набор файлов в живом дереве и в публичном `main` совпадал по именам (9 файлов),
но `ci.yml` в них различался. Расхождение выравнено **в живом дереве**:

| файл | было (живое) | стало (живое = `community/` = публичный `main`) | вывод |
|---|---|---|---|
| `.github/workflows/ci.yml` | 8 532 | 8 791, `ea6d8ae5a200…` | выровнено копией из `community/` |
| остальные 8 файлов | — | хэши совпадали и совпадают | не трогались |

Различие — три строки: в публичной версии в шаг синтаксической проверки добавлен
каталог `bench` с комментарием, что измеритель — такой же код проекта.

Почему выровнено, а не «оставлено как есть». Публикуемое дерево берёт `ci.yml` из
`community/`, то есть проверяет `bench`; живое дерево до правки проверяло на один
каталог меньше. Значит зелёный CI живого дерева не доказывал того же для поставки.
После переноса оба дерева проверяют один и тот же набор каталогов. Откат:
`.ragraf/backup-stage3/.github_workflows_ci.yml.bak-20261024-stage3`.


## 4. Три файла `scripts/`, которые были старее публичных: решения

Два файла в живом дереве были старее публичных, третий отсутствовал. Решение по
каждому — «перенести»; размер и хэш замерены в этапе 3, число строк и функций —
в этапе 2 (таблица ниже — сводка двух замеров).

| файл | было в живом дереве | стало | основание |
|---|---|---|---|
| `scripts/ragraf.ps1` | 1 841 Б, 59 строк, 0 функций | 4 525 Б, 113 строк, `Resolve-Interpreter` | без разрешения интерпретатора обёртка берёт первый `python` из PATH; этот случай записан в самом установщике (в PATH лежал встроенный интерпретатор без модуля `venv`), плюс появляется порог Python ≥ 3.13 |
| `scripts/install-joern.ps1` | 5 314 Б, 137 строк, 1 функция | 20 431 Б, 425 строк, 6 функций | живая версия качает архив Joern без проверки SHA-256 и не восстанавливает `JAVA_HOME`/`PATH` — то есть ставит сторонний бинарник без проверки целостности |
| `scripts/PSScriptAnalyzerSettings.psd1` | отсутствовал | 1 594 Б | на файл ссылается шаг PSScriptAnalyzer в `ci.yml` (`-Settings scripts/PSScriptAnalyzerSettings.psd1`); без него CI публикуемого дерева падал бы на отсутствующем файле настроек |

Источник — публичный `main`, коммит `59ec2618a6225fa83bebed20fc6c0d190f62512d`:
файлы скачаны по адресу коммита, хэши совпали с копиями в старой сборочной копии
(`.staging/release-v0.2/RAGRAF/scripts/`), то есть снимок 14.10 для этих трёх
файлов был верен. Прежние версии сохранены:
`.ragraf/backup-stage3/scripts_*.bak-20261024-stage3`.


## 5. Тексты лицензии в обвязке: расхождение закрыто

Этап 2 перенёс байты публикации эпохи v0.1.0, а лицензия живого дерева с этапа 1 —
BUSL-1.1. Четыре файла называли прежнюю лицензию. Этап 3 их исправил — ссылкой на
файл лицензии, а не пересказом её условий: пересказ и был причиной того, что дерево
публиковало условия PolyForm при поле `license`, где стояло MIT.

| файл | было | стало |
|---|---|---|
| `community/README.ru.md:429` | «PolyForm Noncommercial 1.0.0» и утверждение `license = "PolyForm-Noncommercial-1.0.0"` | ссылка на `LICENSE`, выражение `BUSL-1.1`, история смены лицензий — в `CHANGELOG.md` |
| `community/README.ru.md:431` | «значок показывает Other, потому что PolyForm отсутствует в каталоге» | «значку верить нельзя, источники истины — `LICENSE` и `pyproject.toml`» |
| `community/CITATION.cff:34` | `license: PolyForm-Noncommercial-1.0.0` | `license: BUSL-1.1` (нормализуется именно это выражение; `BSL-1.1` даёт `InvalidLicenseExpression` — замер этапа 1) |
| `community/mkdocs.yml:26` | подпись в подвале сайта «PolyForm Noncommercial License 1.0.0» | «Business Source License 1.1» |
| `community/CONTRIBUTING.md:73` | условие вклада под PolyForm | условие вклада под BUSL-1.1 плюс ссылка на `CLA.md` |
| `community/CHANGELOG.md` | записи о смене лицензий нет | запись «Changed (licence metadata, 2026-10-24)»; прежние записи **не переписаны** |

Функциональный дефект обвязки, найденный при этой правке и исправленный:
`community/install.ps1` распознавал в тексте `LICENSE` только «PolyForm Noncommercial»
и «MIT License», поэтому на исправной поставке с BUSL-1.1 установщик сообщал
«лицензия: не опознана» — отчёт расходился с фактом. Добавлена первая ветвь
(`Business Source License`), прежние сохранены: установщик читает и поставки эпохи
MIT/PolyForm. Проверено разбором парсером PowerShell (0 ошибок).

Граница проверок названа прямо: 23-я проверка (`tools/check_licensing.py`)
осматривает документы ТЕКУЩЕГО состояния по явному списку
(`tools/check_licensing.py:106`) — корневые `README.md`, `NOTICE`, `CLA.md`,
`SUPPORT.md`, `docs/03_implementation/LICENSING.md`. Файлы `community/**` в список
не входят: они описывают поставку, а не текущее состояние проекта. Их согласие
проверяют другие правила — 24-е (таблица источников и хэши) и 25-е (сборка,
проверяльщик, гейт).


## 6. Дата и способ проверки

Снято 24.10.2026. Команды, которыми получены числа:

```powershell
git clone --depth 50 https://github.com/paulmann/RAGRAF.git $sbx
git -C $sbx fetch origin --depth 50
git -C $sbx rev-parse origin/main        # 59ec2618a6225fa83bebed20fc6c0d190f62512d

# хэши источника и приёмника
Get-FileHash <файл> -Algorithm SHA256
# побайтовая сверка переноса
python tools\check_community_wrapper.py  # осмотрено 166 свойств, сверка файла, строки и признака
pwsh -NoProfile -File tools\Invoke-ProjectDoctrine.ps1   # 24-я проверка
# разбор установщика
[System.Management.Automation.Language.Parser]::ParseFile('scripts\install.ps1',[ref]$null,[ref]$e)
```

Отдельно проверено, что перенос не задел старую сборочную копию: хэши ниже сняты
до переноса и после — совпадают.

## 7. Сборочная копия: состав и контроль

`.staging/release-v0.2/RAGRAF` — рабочее дерево клона публикации. До 24.10.2026 в
нём лежал снимок, собранный **вручную** 14.10.2026; этап 4 заменил его сборкой
конвейера (639 файлов, `checksums.sha256` на 639 строк). Прежнее содержимое
сохранено рядом как `RAGRAF.bak-<метка времени>`, каталог `.git` клона при замене
не тронут.

Прямая команда `build_release.py --out .staging/… --force` невозможна: сборщик
отказывается писать внутрь живого дерева (код 2, замер 24.10.2026). Порядок замены
описан в `RELEASE-PIPELINE.md` §2: сборка вне дерева → проверка → замена рабочего
дерева клона.

Хэши ключевых файлов снимка — чтобы изменение было видно:

| файл | sha256 |
|---|---|
| .staging/release-v0.2/RAGRAF/CHANGELOG.md | 59c60e874d706dd0d0fd2108ea83a3f11ab0e688d23dde231c31624d01ab4fed |
| .staging/release-v0.2/RAGRAF/CONTRIBUTING.md | 4702989e9f57c205f752d6e5977b692316fae0fa4c61c32355d5289e05d98689 |
| .staging/release-v0.2/RAGRAF/SECURITY.md | 8a94f86fd64b71063f9479a32721890d661bb3c69221f874d6c1a13c9456bf92 |
| .staging/release-v0.2/RAGRAF/ROADMAP.md | dae3feb0c20c70ec55cae98740401a02206875e2ebfcc8cded00637ca8ab0cf4 |
| .staging/release-v0.2/RAGRAF/README.ru.md | 0b54d4d486bab6c2d6838175ac15d416b45c37d69f3a1f488ecef50cc5f8cede |
| .staging/release-v0.2/RAGRAF/scripts/install.ps1 | 223530f6ded780547e79ebd45fc4264a3d2df46c676666b76bec18508ec935a2 |

Прежние значения (ручной снимок 14.10.2026) — для сравнения, проверкой не
используются:

| было в снимке 14.10 | sha256 |
|---|---|
| было `.staging/release-v0.2/RAGRAF/CHANGELOG.md` | 0dd74d9f49caf2952bd838b7c8c1fdb3f332f892ba3efa115a84f6d07b8af7c6 |
| было `.staging/release-v0.2/RAGRAF/CONTRIBUTING.md` | 7613e2a281203159178a067cd3ca4121cece674ce25dca1a7e9d2cbf68429883 |
| было `.staging/release-v0.2/RAGRAF/SECURITY.md` | 8a94f86fd64b71063f9479a32721890d661bb3c69221f874d6c1a13c9456bf92 |
| было `.staging/release-v0.2/RAGRAF/ROADMAP.md` | 11505345faafb10a7a9d388889f0370cbcd1d9a9a55896bfcf1cc8042d6a4a2f |
| было `.staging/release-v0.2/RAGRAF/README.ru.md` | 1a6bea4cae23e652612353f47090aa2bbef2474ad28368cf6470c4d0850c1669 |

Три файла не изменились (SECURITY, scripts/install.ps1 — и `ROADMAP` совпал с
публичным `main`), поэтому в таблице «было» их нет или значения совпадают.

Снимок `.staging/release-v0.2/RAGRAF` пересобран конвейером в этапе 4.5:
замена выполнена инструментом `tools/release/replace_staging_tree.ps1`, прежнее
дерево лежит рядом как `RAGRAF.bak-20261024-v021`, `.git` клона сохранён, сверка
sha256 сошлась (641 файл). Поэтому таблица выше описывает **текущий** снимок, а не
ручной снимок 14.10.2026: прежние значения оставлены во второй таблице для
сравнения и проверкой не используются.

Отсюда правило, которое остаётся в силе: **источник обвязки — `community/**` в
живом дереве, а не снимок**. История этого клона расходилась с публичной: прежняя
локальная вершина — ветка `local-history-6a62a07` (4 своих коммита, 14 недостающих,
разные начальные коммиты `bfc6a4a` против `28b9455`), поэтому выпуск 0.2.0 был
поставлен на `origin/main`, а не поверх неё. Сейчас расхождения нет: HEAD `f5727a7`
= `origin/main` (замер 24.10.2026: `rev-list --left-right --count HEAD...origin/main`
даёт `0 0`).

## 8. Этап 4: что изменилось в обвязке

Сделано (замерено 24.10.2026):

1. **Версия 0.2.0 объявлена в одном месте** — `src/ragraf/__init__.py`.
   `pyproject.toml` читает её через `dynamic`, `community/CITATION.cff` повторяет
   значение (формат CFF требует поля) и получил дату выпуска `2026-10-24`.
   `community/CHANGELOG.md` получил раздел `[0.2.0]`.
2. **Таблица источников сверена заново**: хэши `community/CHANGELOG.md` и
   `community/CITATION.cff` обновлены (содержимое изменилось), признак «совпадает»
   у обоих остался «нет» — правка названа, а не спрятана.
3. **Три файла вернулись в поставку** (`tests/test_db_index.py`,
   `tools/check_admin.py`, `tools/fix_admin_check.py`): значение доступа вынесено
   в `RAGRAF_DB_PASSWORD`/файл доступа машины, два подставных значения объявлены
   маркером. Подробности и измеренная причина — `RELEASE-PIPELINE.md` §5.
4. **Живой конфиг корпуса убран из поставки**, `examples/ragraf.example.yaml`
   возвращён: до этого сборка несла `root: RAGRAF` — путь автора.
5. **Появилась карта изданий** (`editions/`, `tools/release/editions_report.py`):
   у каждого файла живого дерева есть место — публикация либо объявленное
   удержание. Обоснование — указание владельца «урезанное издание ничего не
   отменяет».

Закрыто этапом 4.5 (24.10.2026): навигация сайта переписана под фактический
состав поставки (59 целей, все существуют; домашняя страница `docs/index.md`
добавлена; русские ветви `docs/ru/**` заменены прямыми ссылками на
`docs/02_architecture/**`, `docs/03_implementation/**`, `docs/05_article/**`), и
пути машины автора убраны из поставки — 86 файлов, из них 21 документ. Проверяльщик
получил девятое правило `paths`, гейт — пятую порчу «путь машины»; `mkdocs build
--strict` на собранном дереве даёт код 0.

Осталось названным, но не сделанным: публикация в PyPI и сайт `ragraf.ru` — вне
конвейера.
