#!/usr/bin/env python3
"""Проверка ссылок «файл:строка» в документах текущего состояния.

Класс дефекта измерен 25.10.2026 (ПРОМТ-И13) и он не про опечатку. Ссылка
`tools/Invoke-ProjectDoctrine.ps1:2386` вела в ПУСТУЮ строку (свойства двинули
код, номер остался), `src/ragraf/cli.py:336-356` — в запись прогона вместо отказа
`slice`, `store.py:122-131` — в загрузку sqlite-vec вместо DDL таблицы `node`.
Файл существует, номер строки в пределах файла, разошёлся ОБЪЕКТ — поэтому
глазами это не видно, а «документ указывает в пустоту» стоит доверия к факту.

Проверяются два свойства, и они разные по силе:

1. Адресуемость — путь содержит каталог и существует, строка в пределах файла.
   Ловит удалённый файл и уехавший номер.
2. Попадание в объект — для пар, объявленных в PAIRS: в документе есть
   утверждение (якорь), ссылка в этом абзаце ведёт в объявленный файл, и в
   указанном диапазоне встречается ожидаемый признак. Это ловит главный дефект:
   номер живой, объект чужой.

Область — ЯВНЫЙ список документов текущего состояния. Исторические отчёты
прежних этапов сохраняют свои адреса намеренно (переписывать историю нельзя) и
не проверяются; иллюстративные примеры объявлены в ILLUSTRATIVE с причиной.

Коды возврата: 0 — всё верно, 1 — есть находки, 2 — проверять нечего
(ноль осмотренных ссылок). Код 2 обязателен: «ноль ссылок» не должен выглядеть
как PASS.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]

# Документы текущего состояния: те, по которым агент принимает решение.
# Список явный, а не «все .md»: исторические отчёты (docs/04_research,
# docs/06_reports, editions/legacy-*) держат адреса своих ревизий.
SCOPE = (
    "AGENTS.md",
    "AGENTS-TRAPS.md",
    "docs/00_plan/PLAN.md",
    "docs/AGENTS-context.md",
    "docs/02_architecture/ARCHITECTURE.md",
    "docs/02_architecture/TZ.md",
    "docs/02_architecture/ACCEPTANCE.md",
    "docs/03_implementation/HARNESS_INTEGRATION.md",
    "docs/03_implementation/AUTOCLAW_INTEGRATION.md",
    "docs/03_implementation/AGENTS_SETUP_AND_CAPABILITIES.md",
    "docs/03_implementation/INTEGRATION.md",
    "docs/03_implementation/SECRETS-VAULT.md",
    "docs/03_implementation/LICENSING.md",
    "docs/03_implementation/RELEASE-SOURCES.md",
    "docs/03_implementation/AGENT-INTEGRATIONS.md",
    "docs/03_implementation/CONTRACT-ANCHORS.md",
    "docs/03_implementation/QUALITY-CASES.md",
    "docs/03_implementation/PREFLIGHT.md",
    "docs/03_implementation/CONTRACT-PIN.md",
    "docs/03_implementation/STAGES-X1-X6.md",
    "docs/skills/ragraf-core/SKILL.md",
    "docs/skills/ragraf-core/reference/repair.md",
    "docs/skills/ragraf-core/reference/operations.md",
    "docs/skills/ragraf-secrets/SKILL.md",
    "harness/skills/ragraf-core/SKILL.md",
    "harness/skills/ragraf-core/reference/repair.md",
    "harness/skills/ragraf-core/reference/operations.md",
    "docs/03_implementation/STALE-CASCADE.md",
    "docs/03_implementation/QUALITY-TRACES.md",
    "docs/03_implementation/DELIVERY-MAP.md",
)

# Путь:строка или путь:строка-строка. Обратная кавычка перед путём — обычная
# запись в этих документах, поэтому она в lookbehind не исключается.
REF = re.compile(
    r"(?<![\w/\\])"
    r"((?:[\w.\-]+[/\\])*[\w.\-]+\.(?:py|ps1|psm1|md|yaml|yml|json|toml|sql|sh|cs|php|cff))"
    r":(\d+)(?:-(\d+))?"
)
# Продолжение ссылки на той же строке: «(`src/ragraf/watch.py:189`, `:623`)».
CONTINUATION = re.compile(r"`\s*:(\d+)(?:-(\d+))?`")

# Пары «утверждение → объект». Проверяется не написание, а попадание: в абзаце,
# начинающемся с якоря, ссылка на объявленный файл обязана указывать на место,
# где встречается ожидаемый признак. Номер строки при этом может меняться —
# меняется и адрес, но признак остаётся, и расхождение становится находкой.
PAIRS = (
    dict(doc="docs/AGENTS-context.md",
         anchor="**Отсюда важное следствие, которое пришлось исправить (30.10.2026, этап 7).**",
         path="src/ragraf/paths.py", marker="def settings_path"),
    # Актуализация 2026-10-30: утверждения нового раздела плана и их объекты.
    dict(doc="docs/00_plan/PLAN.md", anchor="| версия | **0.11.0**",
         path="src/ragraf/__init__.py", marker="__version__"),
    dict(doc="docs/00_plan/PLAN.md", anchor="**Активная ревизия.** Выбор был размазан",
         path="src/ragraf/store.py", marker="def active_revision"),
    dict(doc="docs/00_plan/PLAN.md", anchor="| **7** ✅ закрыт 30.10.2026 |",
         path="src/ragraf/paths.py", marker="def settings_path"),
    # §4 инварианты AGENTS.md — «правило → место в коде».
    dict(doc="AGENTS.md", anchor="1. **Самоисключение", path="src/ragraf/config.py",
         marker="**/.ragraf/**"),
    dict(doc="AGENTS.md", anchor="2. **Исключения конфига добавляются", path="src/ragraf/config.py",
         marker="DEFAULT_EXCLUDES"),
    dict(doc="AGENTS.md", anchor="3. **Сопоставление шаблонов регистрозависимо", path="src/ragraf/util.py",
         marker="def prune_dir"),
    dict(doc="AGENTS.md", anchor="4. **Контрольная сумма корпуса", path="src/ragraf/corpus.py",
         marker="def corpus_hash"),
    dict(doc="AGENTS.md", anchor="4. **Контрольная сумма корпуса", path="src/ragraf/watch.py",
         marker="corpus.scan("),
    dict(doc="AGENTS.md", anchor="4. **Контрольная сумма корпуса", path="src/ragraf/indexer.py",
         marker="corpus.scan("),
    dict(doc="AGENTS.md", anchor="5. **Выбор «главной» копии", path="src/ragraf/dedupe.py",
         marker="def choose_keeper"),
    dict(doc="AGENTS.md", anchor="6. **PRAGMA соединения", path="src/ragraf/store.py",
         marker="busy_timeout=10000"),
    dict(doc="AGENTS.md", anchor="7. **Активная ревизия — последняя ЗАВЕРШЁННАЯ",
         path="src/ragraf/watch.py", marker="state = 'ready'"),
    dict(doc="AGENTS.md", anchor="7. **Активная ревизия — последняя ЗАВЕРШЁННАЯ",
         path="src/ragraf/store.py", marker="def active_revision"),
    dict(doc="docs/AGENTS-context.md", anchor="**Решение.** Один выбор на всё ядро",
         path="src/ragraf/store.py", marker="def active_revision"),
    dict(doc="AGENTS.md", anchor="7. **Активная ревизия — последняя ЗАВЕРШЁННАЯ",
         path="src/ragraf/watch.py", marker="def unfinished_revision"),
    dict(doc="AGENTS.md", anchor="8. **Журнал расхождений — append-only", path="src/ragraf/store.py",
         marker="CREATE TABLE IF NOT EXISTS drift_entry"),
    dict(doc="AGENTS.md", anchor="9. **Ротация ревизий историю не удаляет", path="src/ragraf/revisions.py",
         marker="state = 'rotated'"),
    dict(doc="AGENTS.md", anchor="9. **Ротация ревизий историю не удаляет", path="src/ragraf/revisions.py",
         marker="def next_rev_id"),
    dict(doc="AGENTS.md", anchor="10. **Размерность 384", path="src/ragraf/config.py",
         marker="фиксирована (384)"),
    dict(doc="AGENTS.md", anchor="10. **Размерность 384", path="src/ragraf/store.py",
         marker="FLOAT[384]"),
    dict(doc="AGENTS.md", anchor="11. **Реестр пишется", path="src/ragraf/cli.py",
         marker="record_index_run"),
    dict(doc="AGENTS.md", anchor="11. **Реестр пишется", path="src/ragraf/ensure.py",
         marker="record_index_run"),
    dict(doc="AGENTS.md", anchor="11. **Реестр пишется", path="src/ragraf/admin_server.py",
         marker="record_index_run"),
    dict(doc="AGENTS.md", anchor="11. **Реестр пишется", path="src/ragraf/watch.py",
         marker="def record_index_run"),
    dict(doc="AGENTS.md", anchor="11. **Реестр пишется", path="src/ragraf/catalog.py",
         marker="def record_run"),
    # §3 и §10: контракт ответа, ресурсы и подсказки сервера, число проверок.
    dict(doc="AGENTS.md", anchor="например `slice` при", path="src/ragraf/cli.py",
         marker="OP_REQUIRES_CPG"),
    dict(doc="AGENTS.md", anchor="индекс корпуса строится в каталоге", path="src/ragraf/config.py",
         marker="def ragraf_dir"),
    dict(doc="AGENTS.md", anchor="у сервера есть MCP-ресурсы", path="src/ragraf/mcp_server.py",
         marker="STATUS_URI"),
    dict(doc="AGENTS.md", anchor="у сервера есть MCP-ресурсы", path="src/ragraf/mcp_server.py",
         marker='"name": "ragraf-check"'),
    dict(doc="AGENTS.md", anchor="у сервера есть MCP-ресурсы", path="src/ragraf/mcp_server.py",
         marker="structuredContent"),
    dict(doc="AGENTS.md", anchor="Внешний процесс есть только у слоя CPG", path="src/ragraf/cpg.py",
         marker="def find_java"),
    dict(doc="AGENTS.md", anchor="Внешний процесс есть только у слоя CPG", path="src/ragraf/cpg.py",
         marker="capability=degraded"),
    dict(doc="AGENTS.md", anchor="попадает (`src/ragraf/edition.py", path="src/ragraf/edition.py",
         marker="features_available.append"),
    dict(doc="AGENTS.md", anchor="Деградация вместо падения", path="src/ragraf/joern_server.py",
         marker="class JoernServerUnavailable"),
    dict(doc="AGENTS.md", anchor="добавляет `src` в `sys.path`", path="tools/ragraf.py",
         marker="sys.path.insert"),
    dict(doc="AGENTS.md", anchor="версия стояла в трёх местах", path="src/ragraf/__init__.py",
         marker="__version__ = "),
    dict(doc="AGENTS.md", anchor="версия стояла в трёх местах", path="pyproject.toml",
         marker="attr"),
    dict(doc="AGENTS.md", anchor="версия стояла в трёх местах", path="community/CITATION.cff",
         marker="version:"),
    dict(doc="AGENTS.md", anchor="Токен админ-панели генерируется", path="src/ragraf/admin_server.py",
         marker="TokenStore()"),
    dict(doc="AGENTS.md", anchor="Токен админ-панели генерируется", path="src/ragraf/admin_server.py",
         marker="токен доступа"),
    dict(doc="AGENTS.md", anchor="`$script:CheckFunctions.Count`", path="tools/Invoke-ProjectDoctrine.ps1",
         marker="CheckFunctions = @("),
    # Этап И34: версия объявлена в двух местах, и оба адреса проверяются попаданием.
    dict(doc="docs/AGENTS-context.md", anchor="ПРОМТ-И34", path="src/ragraf/__init__.py",
         marker="__version__"),
    dict(doc="docs/AGENTS-context.md", anchor="ПРОМТ-И34", path="community/CITATION.cff",
         marker="version:"),
    dict(doc="docs/AGENTS-context.md", anchor="сколько правил реально подключено",
         path="tools/Invoke-ProjectDoctrine.ps1", marker="CheckFunctions = @("),
    # §4.4 контекста и ловушка 21 трапов: список OPS и таблица node.
    dict(doc="docs/AGENTS-context.md", anchor="списке `OPS`", path="src/ragraf/cli.py",
         marker="OPS = ["),
    dict(doc="AGENTS-TRAPS.md", anchor="столько же имён в списке `OPS`", path="src/ragraf/cli.py",
         marker="OPS = ["),
    dict(doc="AGENTS-TRAPS.md", anchor="колонки `chunk_id` НЕТ", path="src/ragraf/store.py",
         marker="CREATE TABLE IF NOT EXISTS node"),
    dict(doc="docs/AGENTS-context.md", anchor="Шесть из них работают без конфига",
         path="src/ragraf/cli.py", marker='"secrets",'),
    # §4.1–§4.5 контекста (ПРОМТ-И14): свежесть, ревизии, сторож, детерминизм,
    # контракт ответа, деградация. Якорь — из самого утверждения, признак — имя
    # объекта, которое утверждение называет. Так проверяется попадание в объект,
    # а не написание: код может сдвинуться вместе с адресом, но имя остаётся.
    dict(doc="docs/AGENTS-context.md", anchor="максимум плюс один", path="src/ragraf/revisions.py",
         marker="def next_rev_id"),
    dict(doc="docs/AGENTS-context.md", anchor="Контрольная сумма корпуса —", path="src/ragraf/corpus.py",
         marker="def corpus_hash"),
    dict(doc="docs/AGENTS-context.md", anchor="Обоснование записано в коде", path="src/ragraf/corpus.py",
         marker="отброшенные копии"),
    dict(doc="docs/AGENTS-context.md", anchor="кэш свежести на 15 секунд", path="src/ragraf/watch.py",
         marker="FRESHNESS_TTL_SEC"),
    dict(doc="docs/AGENTS-context.md", anchor="ревизия берётся запросом", path="src/ragraf/watch.py",
         marker="rev_id DESC"),
    dict(doc="docs/AGENTS-context.md", anchor="Обёртка `Facade._install_guard`", path="src/ragraf/facade.py",
         marker="def _install_guard"),
    dict(doc="docs/AGENTS-context.md", anchor="ДЕШЁВЫЙ отпечаток входа", path="src/ragraf/corpus.py",
         marker="def stat_fingerprint"),
    dict(doc="docs/AGENTS-context.md", anchor="ДЕШЁВЫЙ отпечаток входа", path="src/ragraf/watch.py",
         marker="def quick_check"),
    dict(doc="docs/AGENTS-context.md", anchor="отпечаток лежит в", path="src/ragraf/watch.py",
         marker="FINGERPRINT_NAME"),
    dict(doc="docs/AGENTS-context.md", anchor="Поле `stale` теперь есть", path="src/ragraf/watch.py",
         marker='out["stale"]'),
    dict(doc="docs/AGENTS-context.md", anchor="поле `verified_by` называет основание", path="src/ragraf/watch.py",
         marker='out["verified_by"]'),
    dict(doc="docs/AGENTS-context.md", anchor="Ловушка этого пути", path="src/ragraf/watch.py",
         marker="def write_fingerprint"),
    dict(doc="docs/AGENTS-context.md", anchor="Глубина переиндексации выбирается", path="src/ragraf/watch.py",
         marker="reindex_after_changes"),
    dict(doc="docs/AGENTS-context.md", anchor="Порядок обхода и порядок файлов сортированы", path="src/ragraf/corpus.py",
         marker="for name in sorted(filenames)"),
    dict(doc="docs/AGENTS-context.md", anchor="Порядок обхода и порядок файлов сортированы", path="src/ragraf/corpus.py",
         marker="result.files.sort"),
    dict(doc="docs/AGENTS-context.md", anchor="Сериализация JSON сохраняет порядок", path="src/ragraf/util.py",
         marker="json.dumps(obj, ensure_ascii=False"),
    dict(doc="docs/AGENTS-context.md", anchor="В идентификаторы время не", path="src/ragraf/util.py",
         marker="def stable_id"),
    dict(doc="docs/AGENTS-context.md", anchor="идентификаторы — SHA-1", path="src/ragraf/util.py",
         marker="hashlib.sha1"),
    dict(doc="docs/AGENTS-context.md", anchor="затем правило выбора", path="src/ragraf/dedupe.py",
         marker="def choose_keeper"),
    dict(doc="docs/AGENTS-context.md", anchor="сам конверт", path="src/ragraf/facade.py",
         marker="class Envelope"),
    dict(doc="docs/AGENTS-context.md", anchor="единая точка сборки", path="src/ragraf/facade.py",
         marker="def make_env"),
    dict(doc="docs/AGENTS-context.md", anchor="называется в разделе `missing`", path="src/ragraf/edition.py",
         marker="missing.append"),
    dict(doc="docs/AGENTS-context.md", anchor="Операция вне издания получает отказ", path="src/ragraf/facade.py",
         marker="def _install_edition_gate"),
    dict(doc="docs/AGENTS-context.md", anchor="Фасад объявляет чтение", path="src/ragraf/facade.py",
         marker="Все операции чтения. Индекс не изменяется"),
    dict(doc="docs/AGENTS-context.md", anchor="исключения из этого правила ровно две", path="src/ragraf/facade.py",
         marker="INSERT INTO calibration"),
    dict(doc="docs/AGENTS-context.md", anchor="Третий блок ответа MCP", path="src/ragraf/mcp_server.py",
         marker="_OUTPUT_SCHEMA"),
    dict(doc="docs/AGENTS-context.md", anchor="Паспорт ревизии", path="src/ragraf/revisions.py",
         marker="cpg_block = {"),
    dict(doc="docs/AGENTS-context.md", anchor="При понижении покрытия CPG в отчёт пишется громкое предупреждение", path="src/ragraf/indexer.py",
         marker='"cpg_downgrade"'),
    dict(doc="docs/AGENTS-context.md", anchor="Прежний текст «CPG отключена в конфигурации» заменён", path="src/ragraf/indexer.py",
         marker="CPG в этом прогоне не собиралась"),
    dict(doc="docs/AGENTS-context.md", anchor="Коды возврата CLI", path="src/ragraf/cli.py",
         marker="EXIT_UNAVAILABLE = 3"),
    dict(doc="docs/AGENTS-context.md", anchor="Коды возврата CLI", path="src/ragraf/cli.py",
         marker="return EXIT_UNAVAILABLE"),
    dict(doc="docs/AGENTS-context.md", anchor="отсутствие внешнего слоя даёт", path="src/ragraf/cpg.py",
         marker="capability=degraded"),
    # Ловушки AGENTS-TRAPS.md: объект назван в самом утверждении.
    dict(doc="AGENTS-TRAPS.md", anchor="запускает `powershell.exe`", path="src/ragraf/joern_server.py",
         marker='"powershell.exe"'),
    dict(doc="AGENTS-TRAPS.md", anchor="отсутствующий пакет и уходит на CPU", path="src/ragraf/compute.py",
         marker="GpuUnavailable"),
    dict(doc="AGENTS-TRAPS.md", anchor="_ensure_fresh` — `src/ragraf/facade.py", path="src/ragraf/facade.py",
         marker="def _ensure_fresh"),
    dict(doc="AGENTS-TRAPS.md", anchor="`corpus.stat_fingerprint`", path="src/ragraf/corpus.py",
         marker="def stat_fingerprint"),
    dict(doc="AGENTS-TRAPS.md", anchor="`watch.FINGERPRINT_NAME`", path="src/ragraf/watch.py",
         marker="FINGERPRINT_NAME"),
    dict(doc="AGENTS-TRAPS.md", anchor="**Ловушка в пути сторожа.**", path="src/ragraf/watch.py",
         marker="def run_reindex"),
    dict(doc="AGENTS-TRAPS.md", anchor="бюджет исчерпан на стадии", path="src/ragraf/indexer.py",
         marker="бюджет исчерпан на стадии"),
    # AGENTS.md: три хранилища и два честных исключения вместо падения.
    dict(doc="AGENTS.md", anchor="три хранилища SQLite", path="src/ragraf/store.py",
         marker="control.sqlite"),
    dict(doc="AGENTS.md", anchor="**Деградация вместо падения.**", path="src/ragraf/compute.py",
         marker="class GpuUnavailable"),
    dict(doc="AGENTS.md", anchor="**Деградация вместо падения.**", path="src/ragraf/embeddings.py",
         marker="class EmbeddingUnavailable"),
    dict(doc="AGENTS.md", anchor="**Деградация вместо падения.**", path="src/ragraf/joern_server.py",
         marker="class JoernServerUnavailable"),
    # §6: необязательный набор `secrets` — строка, где он объявлен.
    dict(doc="AGENTS.md", anchor="необязательным набором `secrets`", path="pyproject.toml",
         marker='secrets = ["PyNaCl'),
    # §4.17 живая схема: два объекта правки `db_drift` и место причины, а также
    # единственная точка, где ротация ревизий решает, что удалять.
    dict(doc="docs/AGENTS-context.md", anchor="**Правка.** `db_ops.drift`",
         path="src/ragraf/db_ops.py", marker="def drift("),
    dict(doc="docs/AGENTS-context.md", anchor="`_drift_note`",
         path="src/ragraf/db_ops.py", marker="def _drift_note"),
    dict(doc="docs/AGENTS-context.md", anchor="`facade.op_db_drift`",
         path="src/ragraf/facade.py", marker="def op_db_drift"),
    dict(doc="docs/AGENTS-context.md", anchor="причину в `limits`",
         path="src/ragraf/facade.py", marker="drift_note = str("),
    dict(doc="docs/AGENTS-context.md", anchor="План чистки",
         path="src/ragraf/revisions.py", marker="def _purge_rev_data"),
    dict(doc="AGENTS-TRAPS.md", anchor="Почему история вообще накопилась",
         path="src/ragraf/revisions.py", marker="def _purge_rev_data"),
)

# Иллюстративные адреса: пример доказательства из чужого проекта, а не адрес
# этого. Объявлены с причиной, иначе проверка либо падала бы на них вечно,
# либо (хуже) молча их пропускала.
# Пары этапа И42: адреса правок сохранения настроек и выбора активной ревизии.
# Проверяется ПОПАДАНИЕ, а не написание: ссылка в абзаце обязана вести на строку,
# где ожидаемый признак действительно есть.
PAIRS = PAIRS + (
    dict(doc="docs/AGENTS-context.md", anchor="Задача на языке проекта: операция",
         path="src/ragraf/admin_server.py", marker="SettingsStore().save(values)"),
    dict(doc="docs/AGENTS-context.md", anchor="Задача на языке проекта: операция",
         path="src/ragraf/watch.py", marker="def indexed_hashes"),
    dict(doc="docs/AGENTS-context.md", anchor="1. Сохранение настроек.",
         path="src/ragraf/settings.py", marker="def save(self"),
    dict(doc="docs/AGENTS-context.md", anchor="2. Проверка на умолчания",
         path="tests/test_server_settings.py", marker="не материализует умолчания"),
    dict(doc="docs/AGENTS-context.md", anchor="2. Проверка на умолчания",
         path="tools/Invoke-ProjectDoctrine.ps1", marker="function Test-SettingsDeclaration"),
    dict(doc="docs/AGENTS-context.md", anchor="3. Сторож и незавершённая ревизия",
         path="src/ragraf/watch.py", marker="state = 'ready'"),
    dict(doc="docs/AGENTS-context.md", anchor="3. Сторож и незавершённая ревизия",
         path="src/ragraf/watch.py", marker="def unfinished_revision"),
    dict(doc="docs/AGENTS-context.md",
         anchor="**Почему живого конфига в поставке нет.**",
         path="tools/release/manifest.toml", marker="root: RAGRAF"),
    dict(doc="docs/AGENTS-context.md",
         anchor="**Что нашлось.**",
         path="tests/test_doc_layer_archive.py", marker="atexit.register(_verify_live_untouched)"),
    dict(doc="docs/AGENTS-context.md",
         anchor="**Причина — в списке таблиц ротации.**",
         path="src/ragraf/revisions.py", marker="def _purge_rev_data"),
    # docs/03_implementation/AGENT-INTEGRATIONS.md — пары «утверждение → объект»:
    # таблица «где что лежит» называет функцию и её признак, поэтому попадание
    # проверяется, а не только адресуемость.
    dict(doc="docs/03_implementation/AGENT-INTEGRATIONS.md",
         anchor="правила объявления", path="tools/check_integrations.py",
         marker="def check_index"),
    dict(doc="docs/03_implementation/AGENT-INTEGRATIONS.md",
         anchor="разбор шаблона своим разборщиком", path="tools/check_integrations.py",
         marker="def parse_template"),
    dict(doc="docs/03_implementation/AGENT-INTEGRATIONS.md",
         anchor="поиск секретов в шаблоне", path="tools/check_integrations.py",
         marker="def scan_secrets"),
    dict(doc="docs/03_implementation/AGENT-INTEGRATIONS.md",
         anchor="сверка таблицы уровней с документом", path="tools/check_integrations.py",
         marker="def check_doc"),
    dict(doc="docs/03_implementation/AGENT-INTEGRATIONS.md",
         anchor="прогон проверок", path="tools/check_integrations.py",
         marker="def run"),
    dict(doc="docs/03_implementation/AGENT-INTEGRATIONS.md",
         anchor="| проверка в чекере, функция", path="tools/Invoke-ProjectDoctrine.ps1",
         marker="function Test-AgentIntegrations"),
    dict(doc="docs/03_implementation/AGENT-INTEGRATIONS.md",
         anchor="свойство", path="tests/test_agent_integrations.py",
         marker="def main"),
    dict(doc="docs/03_implementation/AGENT-INTEGRATIONS.md",
         anchor="подсадки", path="stage-docgraph/mutations6.py",
         marker="MUTATIONS = ["),
    # §7: цена сторожа — одно утверждение с двумя адресами (фасад и MCP-сервер),
    # поэтому две пары на один якорь, каждая со своим признаком.
    dict(doc="docs/03_implementation/AGENT-INTEGRATIONS.md",
         anchor="сторож в фасаде", path="src/ragraf/facade.py",
         marker="def _ensure_fresh"),
    dict(doc="docs/03_implementation/AGENT-INTEGRATIONS.md",
         anchor="сторож в фасаде", path="src/ragraf/mcp_server.py",
         marker="watch: bool = True"),
)

ILLUSTRATIVE = (
    dict(doc="docs/02_architecture/ARCHITECTURE.md", ref="src/Pipeline/PipelineEngine.php:120",
         reason="пример ребра stated из полигона PHP, а не адрес этого проекта"),
)


# Ссылки, объявленные БЕЗ авто-проверки объекта, каждая с причиной. Это не
# «остальное»: у записи назван адрес и назван признак, по которому объект не
# сводится к одному месту (перечень, пример вывода команды, снимок замера).
# Список обязан быть полным — ссылка вне его и вне пар считается находкой.
REASONS = {
    "A": "перечень: утверждение называет несколько кандидатов или вариантов, одного объекта нет",
    "B": "снимок: адрес относится к замеру прежней ревизии, а не к текущему коду",
    "C": "прежнее состояние: ссылка фиксирует дефект в публикуемом снимке, объект исправлен",
    "D": "участок работы: порядок, формат вывода или следствие отказа — не одно место в коде",
    "E": "тот же объект проверяется парой в другом документе, здесь проверена адресуемость",
    "F": "утверждение не сведено к признаку: адресуемость проверена, попадание в объект — нет",
}

DECLARED: tuple[dict, ...] = tuple(
    dict(doc=item["doc"], ref=item["ref"], reason=REASONS[item["key"]])
    for item in (
    # AGENTS-TRAPS.md
    dict(doc="AGENTS-TRAPS.md", ref="src/ragraf/cpg.py:683-689", key="D"),
    dict(doc="AGENTS-TRAPS.md", ref="src/ragraf/watch.py:151", key="B"),
    # docs/AGENTS-context.md
    # Адреса прежнего состояния навыка (этап 3.1): числа по этим строкам исправлены,
    # ссылка фиксирует дефект, а не текущее содержимое.
    dict(doc="docs/AGENTS-context.md",
         ref="docs/skills/ragraf-core/reference/operations.md:8-9", key="C"),
    dict(doc="docs/AGENTS-context.md",
         ref="docs/skills/ragraf-core/SKILL.md:102-103", key="C"),
    dict(doc="docs/AGENTS-context.md",
         ref="docs/skills/ragraf-core/reference/repair.md:201", key="C"),
    dict(doc="docs/AGENTS-context.md", ref="tools/ragraf.py:15-19", key="E"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/cli.py:1117", key="D"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/cpg.py:118-120", key="A"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/cpg.py:9-11", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/cpg.py:173", key="A"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/cpg.py:683-689", key="D"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/cpg.py:138-150", key="A"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/cpg.py:102", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/embeddings.py:84-87", key="A"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/embeddings.py:42-43", key="E"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/compute.py:101-103", key="D"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/compute.py:70-71", key="E"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/config.py:499-501", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/joern_server.py:53-55", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/joern_server.py:291-341", key="D"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/indexer.py:1864-1868", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/config.py:846-866", key="A"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/lexvec.py:187-196", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/revisions.py:17", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/mcp_server.py:575-670", key="D"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/facade.py:902", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/mcp_server.py:908-937", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/mcp_server.py:2153", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/mcp_server.py:111", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/mcp_server.py:61-64", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/mcp_server.py:1556-1594", key="D"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/mcp_server.py:1238-1266", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/settings.py:327-386", key="A"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/i18n.py:36-37", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/admin_server.py:615", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/i18n.py:49", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/admin_server.py:376", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/admin_server.py:269", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/admin_server.py:1392", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/cli.py:988-989", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/cli.py:906", key="F"),
    # docs/03_implementation/HARNESS_INTEGRATION.md
    dict(doc="docs/03_implementation/HARNESS_INTEGRATION.md", ref="src/ragraf/mcp_server.py:1200-1201", key="F"),
    dict(doc="docs/03_implementation/HARNESS_INTEGRATION.md", ref="src/ragraf/mcp_server.py:140-147", key="F"),
    dict(doc="docs/03_implementation/HARNESS_INTEGRATION.md", ref="src/ragraf/mcp_server.py:1647-1660", key="F"),
    dict(doc="docs/03_implementation/HARNESS_INTEGRATION.md", ref="src/ragraf/mcp_server.py:1556-1594", key="E"),
    dict(doc="docs/03_implementation/HARNESS_INTEGRATION.md", ref="src/ragraf/mcp_server.py:1686-1713", key="F"),
    dict(doc="docs/03_implementation/HARNESS_INTEGRATION.md", ref="src/ragraf/mcp_server.py:2075-2088", key="F"),
    dict(doc="docs/03_implementation/HARNESS_INTEGRATION.md", ref="src/ragraf/mcp_server.py:908-937", key="E"),
    # docs/03_implementation/RELEASE-SOURCES.md
    dict(doc="docs/03_implementation/RELEASE-SOURCES.md", ref=".github/workflows/ci.yml:125", key="C"),
    dict(doc="docs/03_implementation/RELEASE-SOURCES.md", ref="community/CITATION.cff:34", key="C"),
    dict(doc="docs/03_implementation/RELEASE-SOURCES.md", ref="community/mkdocs.yml:26", key="C"),
    dict(doc="docs/03_implementation/RELEASE-SOURCES.md", ref="community/CONTRIBUTING.md:73", key="C"),
    dict(doc="docs/03_implementation/RELEASE-SOURCES.md", ref="tools/check_licensing.py:106", key="F"),
    # docs/skills/ragraf-core/SKILL.md
    dict(doc="docs/skills/ragraf-core/SKILL.md", ref="src/ragraf/cli.py:906", key="F"),
    dict(doc="docs/skills/ragraf-core/SKILL.md", ref="src/ragraf/cli.py:444-447", key="F"),
    dict(doc="docs/skills/ragraf-core/SKILL.md", ref="src/ragraf/ensure.py:540-545", key="F"),
    dict(doc="docs/skills/ragraf-core/SKILL.md", ref="src/ragraf/util.py:162-175", key="F"),
    dict(doc="docs/skills/ragraf-core/SKILL.md", ref="src/ragraf/dedupe.py:165-223", key="E"),
    dict(doc="docs/skills/ragraf-core/SKILL.md", ref="src/ragraf/compute.py:141-146", key="F"),
    # docs/skills/ragraf-core/reference/repair.md
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/corpus.py:74-85", key="E"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/corpus.py:323-342", key="F"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/ensure.py:540-545", key="F"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/cli.py:328-337", key="F"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/watch.py:636-649", key="F"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/catalog.py:260", key="F"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/cpg.py:9-11", key="F"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/cpg.py:118-120", key="A"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/cpg.py:173", key="A"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/indexer.py:1864-1868", key="F"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/util.py:162-175", key="F"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/config.py:541", key="F"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/indexer.py:781-789", key="F"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/dedupe.py:165-223", key="E"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/config.py:276-301", key="F"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/compute.py:141-146", key="F"),
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/settings.py:327-386", key="A"),
    # harness/skills/ragraf-core/reference/repair.md
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/corpus.py:74-85", key="E"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/corpus.py:323-342", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/ensure.py:540-545", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/cli.py:328-337", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/watch.py:636-649", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/catalog.py:260", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/cpg.py:9-11", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/cpg.py:118-120", key="A"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/cpg.py:173", key="A"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/indexer.py:1864-1868", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/util.py:162-175", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/config.py:541", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/indexer.py:781-789", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/dedupe.py:165-223", key="E"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/config.py:276-301", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/compute.py:141-146", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/settings.py:327-386", key="A"),
    # Этап И36: адрес замера дефекта операции code (внешняя заглушка).
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/db_index.py:862-878", key="F"),
    # Этап И35: адреса записи о единой точке записи отпечатка структуры.
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/cli.py:809", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/indexer.py:1161", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/admin_server.py:2206", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/db_collect.py:247", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/db_writer.py:123", key="F"),
    )
)

# Раздел контекста «Ревизия без записи в реестр» (06.11.2026): те же адреса,
# что в ловушке 54, но в документе замера. Проверяется попадание в объект.
PAIRS = PAIRS + (
    dict(doc="docs/AGENTS-context.md", anchor="\u0424\u043e\u0440\u0435\u043d\u0437\u0438\u043a\u0430 \u0440\u0435\u0432\u0438\u0437\u0438\u0439",
         path="src/ragraf/indexer.py",
         marker="\u043a\u0430\u0442\u0430\u043b\u043e\u0433 \u0441\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u044f \u043e\u0442\u0432\u0435\u0434\u0451\u043d \u043f\u0435\u0440\u0435\u043c\u0435\u043d\u043d\u043e\u0439"),
    dict(doc="docs/AGENTS-context.md", anchor="\u041f\u0440\u043e\u0433\u043e\u043d \u043f\u0440\u043e\u0432\u0435\u0440\u043e\u043a \u0434\u0430\u0451\u0442 \u043a\u0430\u0436\u0434\u043e\u043c\u0443 \u043d\u0430\u0431\u043e\u0440\u0443",
         path="tools/check_all.py", marker="def suite_state_dir"),
    dict(doc="docs/AGENTS-context.md", anchor="\u041f\u0440\u043e\u0433\u043e\u043d \u043f\u0440\u043e\u0432\u0435\u0440\u043e\u043a \u0434\u0430\u0451\u0442 \u043a\u0430\u0436\u0434\u043e\u043c\u0443 \u043d\u0430\u0431\u043e\u0440\u0443",
         path="tests/test_doc_layer_archive.py",
         marker='"-p", "config/ragraf.yaml", "query", "doc_ocr"'),
    dict(doc="docs/AGENTS-context.md", anchor="\u041f\u0440\u043e\u0433\u043e\u043d \u043f\u0440\u043e\u0432\u0435\u0440\u043e\u043a \u0434\u0430\u0451\u0442 \u043a\u0430\u0436\u0434\u043e\u043c\u0443 \u043d\u0430\u0431\u043e\u0440\u0443",
         path="tests/test_text_from_index.py", marker="config/ragraf.yaml"),
    dict(doc="docs/AGENTS-context.md", anchor="\u041f\u0440\u043e\u0433\u043e\u043d \u043f\u0440\u043e\u0432\u0435\u0440\u043e\u043a \u0434\u0430\u0451\u0442 \u043a\u0430\u0436\u0434\u043e\u043c\u0443 \u043d\u0430\u0431\u043e\u0440\u0443",
         path="src/ragraf/facade.py", marker="def _run_guard"),
)

# X3 (09.11.2026): якоря замысла. Каждая ссылка документа CONTRACT-ANCHORS.md
# проверяется попаданием: адрес обязан вести на строку с названным признаком.
PAIRS = PAIRS + (
    dict(doc="docs/03_implementation/CONTRACT-ANCHORS.md",
         anchor="При вводе `contract-drift` было записано",
         path="src/ragraf/contracts.py",
         marker="размазывается комментариями внутри кода"),
    dict(doc="docs/03_implementation/CONTRACT-ANCHORS.md",
         anchor="Модуль якоря — это тот же модуль манифеста",
         path="src/ragraf/anchors.py", marker="def load("),
    dict(doc="docs/03_implementation/CONTRACT-ANCHORS.md",
         anchor="Модуль якоря — это тот же модуль манифеста",
         path="src/ragraf/contracts.py", marker="def _merge_declared"),
    dict(doc="docs/03_implementation/CONTRACT-ANCHORS.md",
         anchor="`START_CONTRACT` может стоять и внутри блока модуля",
         path="src/ragraf/anchors.py", marker="def close_contract"),
    dict(doc="docs/03_implementation/CONTRACT-ANCHORS.md",
         anchor="Список остаётся закрытым",
         path="src/ragraf/contracts.py", marker="DRIFT_KINDS = ("),
    dict(doc="docs/03_implementation/CONTRACT-ANCHORS.md",
         anchor="Полный обход рёбер живого корпуса",
         path="src/ragraf/contracts.py", marker="def _module_edges"),
)

# X4 (09.11.2026): кейсы поведения и парный прогон. Каждая ссылка документа
# QUALITY-CASES.md проверяется попаданием: адрес обязан вести на строку с
# названным признаком.
PAIRS = PAIRS + (
    dict(doc="docs/03_implementation/QUALITY-CASES.md",
         anchor="Схема закрыта: ключ вне списка",
         path="src/ragraf/quality_cases.py", marker="def normalize_case"),
    dict(doc="docs/03_implementation/QUALITY-CASES.md",
         anchor="**Пин ревизии — часть кейса.**",
         path="src/ragraf/quality_cases.py", marker="кейс привязан к"),
    dict(doc="docs/03_implementation/QUALITY-CASES.md",
         anchor="**Трасса.** Шаги — это `op`",
         path="src/ragraf/telemetry.py", marker="CREATE TABLE IF NOT EXISTS usage_call"),
    dict(doc="docs/03_implementation/QUALITY-CASES.md",
         anchor="**Трасса.** Шаги — это `op`",
         path="src/ragraf/gate.py", marker="def note_decision"),
    dict(doc="docs/03_implementation/QUALITY-CASES.md",
         anchor="**Трасса.** Шаги — это `op`",
         path="src/ragraf/gate.py", marker="def decisions"),
    dict(doc="docs/03_implementation/QUALITY-CASES.md",
         anchor="**Блокирующее правило.**",
         path="src/ragraf/quality_cases.py", marker="elif regressions"),
    dict(doc="docs/03_implementation/QUALITY-CASES.md",
         anchor="`digest` прогона считается по измерению БЕЗ блока",
         path="src/ragraf/quality_loop.py", marker='if key not in ("digest", "journal", "cases")'),
)

# X5 (09.11.2026): подготовка корпуса и единый текст отказа. Каждая ссылка
# документа PREFLIGHT.md проверяется попаданием.
PAIRS = PAIRS + (
    dict(doc="docs/03_implementation/PREFLIGHT.md",
         anchor="`preflight(root, sample=50)`",
         path="src/ragraf/preflight.py", marker="def scan"),
    dict(doc="docs/03_implementation/PREFLIGHT.md",
         anchor="### Погрешность оценки времени",
         path="src/ragraf/preflight.py", marker="def _parse_sample"),
    dict(doc="docs/03_implementation/PREFLIGHT.md",
         anchor="`util.next_action(operation, arg, kind=..., detail=...)`",
         path="src/ragraf/util.py", marker="def next_action"),
    dict(doc="docs/03_implementation/PREFLIGHT.md",
         anchor="Подключено в отказах",
         path="src/ragraf/facade.py", marker="_no_index"),
)

# X6 (09.11.2026): пиннинг подписи операций. Каждая ссылка документа
# CONTRACT-PIN.md проверяется попаданием в названный объект.
PAIRS = PAIRS + (
    dict(doc="docs/03_implementation/CONTRACT-PIN.md",
         anchor="Описание берётся из `surface.ops_from_file`",
         path="src/ragraf/surface.py", marker="def ops_from_file"),
    dict(doc="docs/03_implementation/CONTRACT-PIN.md",
         anchor="У какого кода спрашивается описание",
         path="src/ragraf/pin.py", marker="def _facade_path"),
    dict(doc="docs/03_implementation/CONTRACT-PIN.md",
         anchor="И — только **первый** такой вызов",
         path="src/ragraf/pin.py", marker="def note_break"),
    dict(doc="docs/03_implementation/CONTRACT-PIN.md",
         anchor="| командная строка | `src/ragraf/cli.py:571` | имя операции не найдено (`unknown_name`) и разбор аргументов не удался (`bad_arguments`) |",
         path="src/ragraf/cli.py", marker="def _note_pin_break"),
    dict(doc="docs/03_implementation/CONTRACT-PIN.md",
         anchor="| MCP, неизвестный инструмент | `src/ragraf/mcp_server.py:3417` | клиент зовёт инструмент, которого у сервера больше нет |",
         path="src/ragraf/mcp_server.py", marker="def _note_pin_break"),
    dict(doc="docs/03_implementation/CONTRACT-PIN.md",
         anchor="| MCP, ошибка аргументов | `src/ragraf/mcp_server.py:2527` | `TypeError` (лишний/недостающий аргумент) и `KeyError` — `bad_arguments` |",
         path="src/ragraf/mcp_server.py", marker="def pin_break_reason"),
    dict(doc="docs/03_implementation/CONTRACT-PIN.md",
         anchor="Каноническая форма",
         path="src/ragraf/pin.py", marker="def description"),
    dict(doc="docs/03_implementation/CONTRACT-PIN.md",
         anchor="Хеш — `sha256` от канонической записи",
         path="src/ragraf/pin.py", marker="def canonical"),
    dict(doc="docs/03_implementation/CONTRACT-PIN.md",
         anchor="Обе — в `control.sqlite` корпуса",
         path="src/ragraf/pin.py", marker="PIN_DDL"),
    # X9 (10.11.2026): состояние набора и исполнение на выпуске.
    dict(doc="docs/03_implementation/CONTRACT-PIN.md",
         anchor="Четыре исхода, и они же — решение ворот выпуска",
         path="src/ragraf/pin.py", marker="def state_payload"),
    dict(doc="docs/03_implementation/CONTRACT-PIN.md",
         anchor="состояние ДО сборки",
         path="tools/release/gate_release.py", marker="def check_contract_pin"),
    dict(doc="docs/03_implementation/CONTRACT-PIN.md",
         anchor="Второй страж — после сборки",
         path="tools/release/gate_release.py", marker="def check_pins_artifact"),
    dict(doc="docs/03_implementation/CONTRACT-PIN.md",
         anchor="Второй страж — после сборки",
         path="tools/release/build_release.py", marker="def write_pins"),
)

# X1–X6 (09.11.2026): сводный документ этапов. Пары построены замером:
# признак — строка целевого файла, якорь — строка документа со ссылкой.
# Правка документа или кода рвёт пару, и это видно в прогоне.
PAIRS = PAIRS + (
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| разбор интервала действия (`с 12.03.2026`, `до 2026-06-01`, `бессрочно`) | `src/ragraf/p",
         path="src/ragraf/provenance.py", marker="def parse_interval(rel: str, raw: bytes | None = None,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| запись слоя происхождения в лексическое хранилище | `src/ragraf/provenance.py:360` |",
         path="src/ragraf/provenance.py", marker="def write_layer(conn: sqlite3.Connection, rev_id: str, documents: Iterable[Any]) -> dict[str, int]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| сборка производных фактов из документов | `src/ragraf/provenance.py:473` |",
         path="src/ragraf/provenance.py", marker="def build_derived(conn: sqlite3.Connection, rev_id: str) -> dict[str, int]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| обратный обход: на что влияет факт | `src/ragraf/provenance.py:725` |",
         path="src/ragraf/provenance.py", marker="def dependents(conn: sqlite3.Connection, rev_id: str, ref: str, *,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| операция «что было верно на дату» | `src/ragraf/facade.py:6169` |",
         path="src/ragraf/facade.py", marker="def op_doc_asof(self, query: str, date: str, limit: int = 20,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| операция «от чего зависит утверждение» | `src/ragraf/facade.py:6208` |",
         path="src/ragraf/facade.py", marker="def op_dependents(self, fact: str = \"\", locator: str = \"\", depth: int = 3,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| счёт поверхности из кода (AST, а не регулярка) | `src/ragraf/surface.py:354` |",
         path="src/ragraf/surface.py", marker="def collect(root: Path) -> dict[str, Any]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| описание операций: параметры и ключи ответа | `src/ragraf/surface.py:167` |",
         path="src/ragraf/surface.py", marker="def ops_of(root: Path) -> list[dict[str, Any]]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| отпечаток состава (без ревизии) | `src/ragraf/surface.py:64` |",
         path="src/ragraf/surface.py", marker="def digest_of(payload: Any) -> str:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| генератор подстановки чисел в документы | `tools/surface_gen.py:99` |",
         path="tools/surface_gen.py", marker="def render(template: str, counts: dict[str, int]) -> str:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| русские формы числительных выбирает код | `tools/surface_gen.py:70` |",
         path="tools/surface_gen.py", marker="def ru_plural(value: int, one: str, few: str, many: str) -> str:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| подстановочные слоты для шаблонов | `tools/surface_gen.py:88` |",
         path="tools/surface_gen.py", marker="def phrase_slots(counts: dict[str, int]) -> dict[str, str]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| выбор файлов-кандидатов для чтения якорей | `src/ragraf/anchors.py:68` |",
         path="src/ragraf/anchors.py", marker="def candidate_paths(conn, rev_id: str, limit: int = MAX_ANCHOR_FILES) -> list[str]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| разбор якорей из текста файла | `src/ragraf/anchors.py:80` |",
         path="src/ragraf/anchors.py", marker="def parse_text(rel: str, text: str) -> dict[str, Any]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| закрытие открытого якоря по номеру строки | `src/ragraf/anchors.py:126` |",
         path="src/ragraf/anchors.py", marker="def close_contract(line: int) -> None:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| загрузка якорей всего корпуса | `src/ragraf/anchors.py:277` |",
         path="src/ragraf/anchors.py", marker="def load(root: str | Path, conn, rev_id: str) -> dict[str, Any]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| закрытый список видов дрейфа (11 видов) | `src/ragraf/contracts.py:69` |",
         path="src/ragraf/contracts.py", marker="DRIFT_KINDS = ("),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| сверка манифеста, якорей и графа | `src/ragraf/contracts.py:698` |",
         path="src/ragraf/contracts.py", marker="def analyze(conn, rev_id: str, manifest: dict[str, Any], *,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| операция с полем `source` (manifest / anchors / both) | `src/ragraf/facade.py:1612` |",
         path="src/ragraf/facade.py", marker="def op_contract_drift(self, manifest_path: str | None = None,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| нормализация кейса | `src/ragraf/quality_cases.py:140` |",
         path="src/ragraf/quality_cases.py", marker="def normalize_case(item: Any, where: str, *, default_kind: str = \"index\") -> dict[str, Any]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| загрузка кейсов корпуса | `src/ragraf/quality_cases.py:174` |",
         path="src/ragraf/quality_cases.py", marker="def load_cases(root: str | Path, path: str | Path | None = None) -> dict[str, Any]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| загрузка бенчмарка (ожидания машинно-зависимы) | `src/ragraf/quality_cases.py:247` |",
         path="src/ragraf/quality_cases.py", marker="def load_bench(root: str | Path, path: str | Path | None = None) -> dict[str, Any]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| оценка одного кейса | `src/ragraf/quality_cases.py:366` |",
         path="src/ragraf/quality_cases.py", marker="def evaluate_one(case: dict[str, Any], *, graph: Any, control: Any, rev: str,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| оценка набора | `src/ragraf/quality_cases.py:451` |",
         path="src/ragraf/quality_cases.py", marker="def evaluate(cases: list[dict[str, Any]], *, graph: Any, control: Any, rev: str,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| сравнение двух прогонов | `src/ragraf/quality_cases.py:483` |",
         path="src/ragraf/quality_cases.py", marker="def pair(before: dict[str, Any], after: dict[str, Any], *,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| блок `cases` в отчёте качества | `src/ragraf/quality_loop.py:216` |",
         path="src/ragraf/quality_loop.py", marker="def _cases_block(sources: Sources, rev_id: str, session: str = \"\") -> dict[str, Any]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| парный прогон `quality_pair` | `src/ragraf/quality_loop.py:711` |",
         path="src/ragraf/quality_loop.py", marker="def pair_runs(sources: Sources, *, from_run: int = 0, to_run: int = 0) -> dict[str, Any]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| чтение решений гейта (не второй журнал) | `src/ragraf/gate.py:413` |",
         path="src/ragraf/gate.py", marker="def decisions(self, session_id: str = \"\", limit: int = 200) -> list[dict]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| запись решения гейта | `src/ragraf/gate.py:432` |",
         path="src/ragraf/gate.py", marker="def note_decision(self, decision: GateDecision, call: dict, *,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| операция `quality_pair` | `src/ragraf/facade.py:8960` |",
         path="src/ragraf/facade.py", marker="def op_quality_pair(self, from_run: int = 0, to_run: int = 0) -> dict:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| инструмент `ragraf_quality_pair` | `src/ragraf/mcp_server.py:803` |",
         path="src/ragraf/mcp_server.py", marker="\"name\": \"ragraf_quality_pair\","),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| признаки сгенерированного файла | `src/ragraf/preflight.py:47` |",
         path="src/ragraf/preflight.py", marker="GENERATED_MARKERS = ("),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| шаблоны динамических мест по языкам | `src/ragraf/preflight.py:63` |",
         path="src/ragraf/preflight.py", marker="DYNAMIC_PATTERNS: dict[str, tuple[tuple[str, str], ...]] = {"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| оценка погрешности по выборке | `src/ragraf/preflight.py:189` |",
         path="src/ragraf/preflight.py", marker="def _parse_sample(root: Path, files: list[Path], sample: int) -> dict[str, Any]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| осмотр корпуса (ничего не создаёт) | `src/ragraf/preflight.py:348` |",
         path="src/ragraf/preflight.py", marker="def scan(root: str | Path, *, sample: int = 50,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| предел чтения файлов на прогон | `src/ragraf/preflight.py:42` |",
         path="src/ragraf/preflight.py", marker="MAX_FILES_READ = 5_000"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| закрытый список видов отказа (9 видов) | `src/ragraf/util.py:540` |",
         path="src/ragraf/util.py", marker="REFUSAL_KINDS: tuple[str, ...] = ("),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| единый следующий шаг отказа | `src/ragraf/util.py:593` |",
         path="src/ragraf/util.py", marker="def next_action(operation: str, arg: str = \"\", *, kind: str = \"no_index\","),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| операция `preflight` | `src/ragraf/facade.py:1726` |",
         path="src/ragraf/facade.py", marker="def op_preflight(self, root: str = \"\", sample: int = 50) -> dict:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| подсказка «в каком конфиге дело» | `src/ragraf/facade.py:7855` |",
         path="src/ragraf/facade.py", marker="def _config_hint(self) -> str:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| отказ «индекса нет» | `src/ragraf/facade.py:7864` |",
         path="src/ragraf/facade.py", marker="def _no_index(self, env: Envelope) -> dict:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| инструмент `ragraf_preflight` | `src/ragraf/mcp_server.py:742` |",
         path="src/ragraf/mcp_server.py", marker="\"name\": \"ragraf_preflight\","),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| флаг выборки в командной строке | `src/ragraf/cli.py:2723` |",
         path="src/ragraf/cli.py", marker="pq.add_argument(\"--sample\", type=int, default=50,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| каноническая запись описания для хеша | `src/ragraf/pin.py:222` |",
         path="src/ragraf/pin.py", marker="def canonical(payload: Any) -> str:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| описание операции: параметры и ключи ответа | `src/ragraf/pin.py:261` |",
         path="src/ragraf/pin.py", marker="def description(op: dict[str, Any]) -> dict[str, Any]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| хеши всех операций (описание берётся из `surface`) | `src/ragraf/pin.py:285` |",
         path="src/ragraf/pin.py", marker="def hashes(root: str | Path) -> dict[str, str]:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| подтверждение пина человеком | `src/ragraf/pin.py:496` |",
         path="src/ragraf/pin.py", marker="def approve(conn: sqlite3.Connection, root: str | Path, *, op: str = \"\","),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| наблюдение хеша на вызове и `pin_mismatch` | `src/ragraf/pin.py:562` |",
         path="src/ragraf/pin.py", marker="def observe(conn: sqlite3.Connection, op: str, observed: str, *,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| `pin_break` — только после расхождения, и только первый вызов | `src/ragraf/pin.py:633` |",
         path="src/ragraf/pin.py", marker="def note_break(conn: sqlite3.Connection, name: str, *, reason: str,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| состояние пинов, расхождения, события | `src/ragraf/pin.py:1344` |",
         path="src/ragraf/pin.py", marker="def status(conn: sqlite3.Connection, root: str | Path, *,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| догоняющая миграция колонки `observed_hash` | `src/ragraf/telemetry.py:347` |",
         path="src/ragraf/telemetry.py", marker="\"ALTER TABLE usage_call ADD COLUMN observed_hash TEXT\")"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| операция `contract_pin_status` | `src/ragraf/facade.py:2021` |",
         path="src/ragraf/facade.py", marker="def op_contract_pin_status(self, limit: int = 50) -> dict:"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| операция `contract_pin_approve` | `src/ragraf/facade.py:2090` |",
         path="src/ragraf/facade.py", marker="def op_contract_pin_approve(self, op: str = \"\", everything: bool = False,"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| разбор новых операций в командной строке | `src/ragraf/cli.py:1077` |",
         path="src/ragraf/cli.py", marker="if op == \"contract_pin_status\":"),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| инструмент `ragraf_contract_pin_status` | `src/ragraf/mcp_server.py:612` |",
         path="src/ragraf/mcp_server.py", marker="\"name\": \"ragraf_contract_pin_status\","),
    dict(doc="docs/03_implementation/STAGES-X1-X6.md",
         anchor="| инструмент `ragraf_contract_pin_approve` | `src/ragraf/mcp_server.py:695` |",
         path="src/ragraf/mcp_server.py", marker="\"name\": \"ragraf_contract_pin_approve\","),
)

# Ловушка 54 (06.11.2026): ревизия в графе есть, записи в реестре нет.
# Проверяется ПОПАДАНИЕ: каждая ссылка ловушки обязана вести на строку, где
# названный признак действительно стоит (прогон проверок отводит каталог
# состояния копией реестра, наборы работают с собственным конфигом проекта,
# сторожа гоняет фасад, причина отказа слоя баз называет переменную).
PAIRS = PAIRS + (
    dict(doc="AGENTS-TRAPS.md", anchor="\u043f\u0440\u043e\u0433\u043e\u043d \u043f\u0440\u043e\u0432\u0435\u0440\u043e\u043a (`tools/check_all.py:396`)",
         path="tools/check_all.py", marker="RAGRAF_CATALOG_DIR=str(state_dir)"),
    dict(doc="AGENTS-TRAPS.md", anchor="\u041a\u041e\u041f\u0418\u0420\u0423\u0415\u0422\u0421\u042f \u0436\u0438\u0432\u043e\u0439 \u0440\u0435\u0435\u0441\u0442\u0440",
         path="tools/check_all.py", marker="def suite_state_dir"),
    dict(doc="AGENTS-TRAPS.md", anchor="\u043d\u0430\u0431\u043e\u0440\u044b, \u0440\u0430\u0431\u043e\u0442\u0430\u044e\u0449\u0438\u0435 \u0441 \u0441\u043e\u0431\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u043c \u043a\u043e\u043d\u0444\u0438\u0433\u043e\u043c",
         path="tests/test_doc_layer_archive.py",
         marker='"-p", "config/ragraf.yaml", "query", "doc_ocr"'),
    dict(doc="AGENTS-TRAPS.md", anchor="\u043d\u0430\u0431\u043e\u0440\u044b, \u0440\u0430\u0431\u043e\u0442\u0430\u044e\u0449\u0438\u0435 \u0441 \u0441\u043e\u0431\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u043c \u043a\u043e\u043d\u0444\u0438\u0433\u043e\u043c",
         path="tests/test_text_from_index.py", marker="config/ragraf.yaml"),
    dict(doc="AGENTS-TRAPS.md", anchor="\u0433\u043e\u043d\u044f\u0435\u0442 \u0441\u0442\u043e\u0440\u043e\u0436\u0430",
         path="src/ragraf/facade.py", marker="def _run_guard"),
    dict(doc="AGENTS-TRAPS.md", anchor="\u0414\u043e\u043a\u0430\u0437\u0430\u0442\u0435\u043b\u044c\u0441\u0442\u0432\u043e \u043b\u0435\u0436\u0438\u0442 \u0432\u043d\u0443\u0442\u0440\u0438",
         path="src/ragraf/indexer.py",
         marker="\u043a\u0430\u0442\u0430\u043b\u043e\u0433 \u0441\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u044f \u043e\u0442\u0432\u0435\u0434\u0451\u043d \u043f\u0435\u0440\u0435\u043c\u0435\u043d\u043d\u043e\u0439"),
)

# Пары этапа И41: адреса правок трёх проверок и места в коде, где записано
# исправление дефекта «записанное умолчание стало заявлением». Проверяется
# ПОПАДАНИЕ: ссылка обязана вести на строку, где ожидаемый признак есть.
PAIRS = PAIRS + (
    dict(doc="docs/AGENTS-context.md", anchor="1. `tests/test_dedupe_prompt.py",
         path="tests/test_dedupe_prompt.py", marker="DEDUPE_SETTINGS"),
    dict(doc="docs/AGENTS-context.md", anchor="2. `tests/test_dedupe_stability.py",
         path="tests/test_dedupe_stability.py", marker="FIXTURE"),
    dict(doc="docs/AGENTS-context.md", anchor="3. `tests/test_telemetry_agent.py",
         path="tests/test_telemetry_agent.py", marker="NULL_ROWS"),
    dict(doc="docs/AGENTS-context.md", anchor="Исправлено в двух местах",
         path="src/ragraf/settings.py", marker="def save("),
)


# Мастер-план внесён в SCOPE 2026-10-29: он заявляет факты о коде и версии, и эти
# адреса обязаны проверяться, а не держаться на совести автора.
PAIRS = PAIRS + (
    dict(doc="docs/00_plan/PLAN.md", anchor="«числа измерены на версии 0.4.0»",
         path="src/ragraf/__init__.py", marker="__version__"),
    dict(doc="docs/00_plan/PLAN.md", anchor="«Отсутствует Java/JVM»",
         path="config/ragraf.yaml", marker="joern_enabled"),
    dict(doc="docs/00_plan/PLAN.md", anchor="Исправление 2026-10-29",
         path="config/ragraf.yaml", marker="joern_enabled"),
    # Документ называет число, документ-объект его содержит: проверяется попадание
    # в строку, а не только адресуемость.
    # Мёртвое объявление: ссылка обязана вести на строку, где массив объявлен.
    dict(doc="docs/00_plan/PLAN.md", anchor="Вернуть сравнение в чекер",
         path="tools/Invoke-ProjectDoctrine.ps1", marker="$documents = @("),
    dict(doc="docs/00_plan/PLAN.md", anchor="Как это ловилось",
         path="tools/Invoke-ProjectDoctrine.ps1", marker="$documents = @("),
)


def _declared_present(text: str, ref: str) -> bool:
    """Есть ли ссылка в документе — полной записью или продолжением.

    Ссылки в этих документах часто записаны парой: «(`watch.py:189`, `:623`)».
    Второй адрес — продолжение того же пути, и требовать от него полной записи
    значило бы объявлять живую ссылку устаревшей.
    """
    if ref in text:
        return True
    parts = ref.split(":", 1)
    if len(parts) != 2:
        return False
    return f"`:{parts[1]}`" in text


def _read(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def collect_refs(lines: list[str]) -> list[tuple[int, str, int, int | None]]:
    """Все ссылки документа: (строка документа, путь, первая, последняя)."""
    found: list[tuple[int, str, int, int | None]] = []
    for lineno, line in enumerate(lines, 1):
        last_path = ""
        for match in REF.finditer(line):
            last_path = match.group(1)
            last = int(match.group(3)) if match.group(3) else None
            found.append((lineno, last_path, int(match.group(2)), last))
        if last_path:
            for cont in CONTINUATION.finditer(line):
                last = int(cont.group(2)) if cont.group(2) else None
                found.append((lineno, last_path, int(cont.group(1)), last))
    return found


def paragraph(lines: list[str], anchor: str) -> tuple[int, int] | None:
    """Абзац, начинающийся с якоря: от строки якоря до пустой строки.

    Строка таблицы — сама себе абзац. Иначе якорь из одной строки таблицы
    разрешал бы ссылки остальных строк, и пара проверяла бы соседа вместо
    своего объекта: в таблице деградации рядом стоят Java, Joern и PHP, и
    «одна из них ведёт куда надо» — не проверка, а совпадение.
    """
    start = None
    for index, line in enumerate(lines):
        if anchor in line:
            start = index
            break
    if start is None:
        return None
    if lines[start].lstrip().startswith("|"):
        return start + 1, start + 1  # 1-based, одна строка
    end = start
    while end < len(lines) and lines[end].strip():
        end += 1
    return start + 1, end  # 1-based включительно




# --- X7: каскад устаревания, трассы сбоев, карта поставки ----
PAIRS = PAIRS + (
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| таблица пометок `fact_stale` | `src/ragraf/provenance.py:148` |',
         path="src/ragraf/provenance.py", marker='CREATE TABLE IF NOT EXISTS fact_stale ('),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| общий обход рёбер в ширину (один на `dependents` и каскад) | `src/ragraf/provenance.py:657` |',
         path="src/ragraf/provenance.py", marker='def _walk(conn: sqlite3.Connection, rev_id: str, seed_ids: set[str],'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| отчёт «что пересчитать» без записи | `src/ragraf/provenance.py:782` |',
         path="src/ragraf/provenance.py", marker='def plan_cascade(conn: sqlite3.Connection, rev_id: str, changed: Any, *,'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| запись пометок | `src/ragraf/provenance.py:843` |',
         path="src/ragraf/provenance.py", marker='def apply_marks(conn: sqlite3.Connection, rev_id: str, plan: list[dict[str, Any]],'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| чтение поставленных пометок | `src/ragraf/provenance.py:872` |',
         path="src/ragraf/provenance.py", marker='def stale_marks(conn: sqlite3.Connection, rev_id: str, *,'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| закрытие пометок после пересчёта | `src/ragraf/provenance.py:912` |',
         path="src/ragraf/provenance.py", marker='def clear_marks(conn: sqlite3.Connection, rev_id: str) -> dict[str, int]:'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| три вида вызова одним входом | `src/ragraf/provenance.py:924` |',
         path="src/ragraf/provenance.py", marker='def cascade(conn: sqlite3.Connection, rev_id: str, changed: Any = "", *,'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| автоматические пометки при индексации | `src/ragraf/provenance.py:971` |',
         path="src/ragraf/provenance.py", marker='def auto_cascade(conn: sqlite3.Connection, rev_id: str, *,'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| хук индексатора (после сборки производных фактов) | `src/ragraf/indexer.py:679` |',
         path="src/ragraf/indexer.py", marker='cascade_stats = provenance_mod.auto_cascade(self.lexvec.conn, rev_id)'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| тот же хук в сборке документного графа | `src/ragraf/docgraph_extract.py:404` |',
         path="src/ragraf/docgraph_extract.py", marker='cascade = provenance_mod.auto_cascade(conn, rev_id)'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| операция фасада `stale_cascade` | `src/ragraf/facade.py:6260` |',
         path="src/ragraf/facade.py", marker='def op_stale_cascade(self, changed: str = "", depth: int = 3,'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| свойства | `tests/test_provenance.py:327` (раздел 5б) |',
         path="tests/test_provenance.py", marker='print("5б. STALE_CASCADE: ЧТО ПЕРЕСЧИТАТЬ, ПОМЕТКИ И ЗАКРЫТИЕ")'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='- **при индексации** (`auto_cascade`, `src/ragraf/provenance.py:971`): `sha256`',
         path="src/ragraf/provenance.py", marker='def auto_cascade(conn: sqlite3.Connection, rev_id: str, *,'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| таблица `quality_trace` (журнал управления) | `src/ragraf/quality_traces.py:53` |',
         path="src/ragraf/quality_traces.py", marker='CREATE TABLE IF NOT EXISTS quality_trace ('),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| отказ записи `TraceRefusal` | `src/ragraf/quality_traces.py:69` |',
         path="src/ragraf/quality_traces.py", marker='class TraceRefusal(ValueError):'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| канонический JSON и отпечаток | `src/ragraf/quality_traces.py:79`, `:85` |',
         path="src/ragraf/quality_traces.py", marker='def canonical(payload: Any) -> str:'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| запись сбоя | `src/ragraf/quality_traces.py:105` |',
         path="src/ragraf/quality_traces.py", marker='def save(conn: sqlite3.Connection, *, case: dict[str, Any],'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| компактный список | `src/ragraf/quality_traces.py:163` |',
         path="src/ragraf/quality_traces.py", marker='def traces(conn: sqlite3.Connection, *, case_id: str = "",'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| полная трасса | `src/ragraf/quality_traces.py:185` |',
         path="src/ragraf/quality_traces.py", marker='def load(conn: sqlite3.Connection, trace_id: int) -> dict[str, Any] | None:'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| повтор с вердиктом | `src/ragraf/quality_traces.py:203` |',
         path="src/ragraf/quality_traces.py", marker='def replay(conn: sqlite3.Connection, trace_id: int, *, rev: str,'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| операция фасада `quality_trace` | `src/ragraf/facade.py:8705` |',
         path="src/ragraf/facade.py", marker='def op_quality_trace(self, action: str = "list", case_id: str = "",'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| шаги сеанса и решения гейта (публичные помощники) | `src/ragraf/quality_cases.py:356`, `:361` |',
         path="src/ragraf/quality_cases.py", marker='def session_ops(control: Any, session: str) -> list[str]:'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| проверка одного кейса | `src/ragraf/quality_cases.py:366` |',
         path="src/ragraf/quality_cases.py", marker='def evaluate_one(case: dict[str, Any], *, graph: Any, control: Any, rev: str,'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 1 | Документный слой с интервалами действия (`valid_from` / `valid_to`, `doc_asof`, пометка заменённого) | `provenance` (Pro) | `doc_asof` | `src/ragraf/facade.py:6169` — операция; разбор интервала и запись слоя — `src/ragraf/provenance.py` | `tests/test_provenance.py` (66 свойств) |',
         path="src/ragraf/facade.py", marker='def op_doc_asof(self, query: str, date: str, limit: int = 20,'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 2 | Слой замысла: якоря `MODULE_CONTRACT` как факты и сверка «объявлено / вычислено» | `contract-drift` (Enterprise) | `contract_drift` | `src/ragraf/facade.py:1612` — операция; разбор якорей — `src/ragraf/anchors.py` | `tests/test_contracts_anchors.py` (35 свойств) |',
         path="src/ragraf/facade.py", marker='def op_contract_drift(self, manifest_path: str | None = None,'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 4 | Утверждения по трассе, а не только по ответу; подтверждённый сбой как воспроизводимая трасса | `quality-loop` (Pro) | `quality_suite`, `quality_trace` | `src/ragraf/quality_traces.py:105` — запись трассы; проверка кейсов — `src/ragraf/quality_cases.py` | `tests/test_quality_cases.py` (49), `tests/test_quality_traces.py` (42) |',
         path="src/ragraf/quality_traces.py", marker='def save(conn: sqlite3.Connection, *, case: dict[str, Any],'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 5 | Сообщение отказа называет следующее действие | дисциплина текста (не товар) | — (поле `next_action` у отказов) | `src/ragraf/util.py:593` — единая сборка отказа | `tests/test_preflight.py` (34 свойства) |',
         path="src/ragraf/util.py", marker='def next_action(operation: str, arg: str = "", *, kind: str = "no_index",'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 6 | Пиннинг подписи операций и корреляция «описание изменилось — вызов упал» | `contract-pin` (Enterprise) | `contract_pin_status`, `contract_pin_approve`, `contract_pin_history`, `contract_pin_sample`, `contract_diff` | `src/ragraf/pin.py:723` — состояние набора; хеши — `src/ragraf/pin.py`; сводка между выпусками; ворота выпуска — `tools/release/gate_release.py`; весь выпуск — `tools/release/release.py` | `tests/test_contract_pin.py` (173 проверки) |',
         path="src/ragraf/pin.py", marker='def state_payload(current: dict[str, str],'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 7 | Парные прогоны и per-case регрессия в бенчмарке | `quality-loop` (Pro) | `quality_pair` | `src/ragraf/quality_cases.py:483` — сравнение прогонов по кейсам | `tests/test_quality_cases.py` (49 свойств) |',
         path="src/ragraf/quality_cases.py", marker='def pair(before: dict[str, Any], after: dict[str, Any], *,'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 8 | Сверка чисел в документации с кодом (источник истины — код) | дисциплина сборки (не товар) | — (гейт `surface_gen`) | `src/ragraf/surface.py:167` — счёт операций из AST | `tests/test_documented_numbers.py` (76 свойств) |',
         path="src/ragraf/surface.py", marker='def ops_of(root: Path) -> list[dict[str, Any]]:'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 9 | Предварительная оценка индексации легаси до первого прохода | `preflight` (Pro) | `preflight` | `src/ragraf/preflight.py:348` — осмотр корпуса, который ничего не создаёт | `tests/test_preflight.py` (34 свойства) |',
         path="src/ragraf/preflight.py", marker='def scan(root: str | Path, *, sample: int = 50,'),
)


# --- X7: каскад устаревания, трассы сбоев, карта поставки ----
PAIRS = PAIRS + (
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| таблица пометок `fact_stale` | `src/ragraf/provenance.py:148` |',
         path="src/ragraf/provenance.py", marker='CREATE TABLE IF NOT EXISTS fact_stale ('),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| общий обход рёбер в ширину (один на `dependents` и каскад) | `src/ragraf/provenance.py:657` |',
         path="src/ragraf/provenance.py", marker='def _walk(conn: sqlite3.Connection, rev_id: str, seed_ids: set[str],'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| отчёт «что пересчитать» без записи | `src/ragraf/provenance.py:782` |',
         path="src/ragraf/provenance.py", marker='def plan_cascade(conn: sqlite3.Connection, rev_id: str, changed: Any, *,'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| запись пометок | `src/ragraf/provenance.py:843` |',
         path="src/ragraf/provenance.py", marker='def apply_marks(conn: sqlite3.Connection, rev_id: str, plan: list[dict[str, Any]],'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| чтение поставленных пометок | `src/ragraf/provenance.py:872` |',
         path="src/ragraf/provenance.py", marker='def stale_marks(conn: sqlite3.Connection, rev_id: str, *,'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| закрытие пометок после пересчёта | `src/ragraf/provenance.py:912` |',
         path="src/ragraf/provenance.py", marker='def clear_marks(conn: sqlite3.Connection, rev_id: str) -> dict[str, int]:'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| три вида вызова одним входом | `src/ragraf/provenance.py:924` |',
         path="src/ragraf/provenance.py", marker='def cascade(conn: sqlite3.Connection, rev_id: str, changed: Any = "", *,'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| автоматические пометки при индексации | `src/ragraf/provenance.py:971` |',
         path="src/ragraf/provenance.py", marker='def auto_cascade(conn: sqlite3.Connection, rev_id: str, *,'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| хук индексатора (после сборки производных фактов) | `src/ragraf/indexer.py:679` |',
         path="src/ragraf/indexer.py", marker='cascade_stats = provenance_mod.auto_cascade(self.lexvec.conn, rev_id)'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| тот же хук в сборке документного графа | `src/ragraf/docgraph_extract.py:404` |',
         path="src/ragraf/docgraph_extract.py", marker='cascade = provenance_mod.auto_cascade(conn, rev_id)'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| операция фасада `stale_cascade` | `src/ragraf/facade.py:6260` |',
         path="src/ragraf/facade.py", marker='def op_stale_cascade(self, changed: str = "", depth: int = 3,'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| свойства | `tests/test_provenance.py:327` (раздел 5б) |',
         path="tests/test_provenance.py", marker='print("5б. STALE_CASCADE: ЧТО ПЕРЕСЧИТАТЬ, ПОМЕТКИ И ЗАКРЫТИЕ")'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='- **при индексации** (`auto_cascade`, `src/ragraf/provenance.py:971`): `sha256`',
         path="src/ragraf/provenance.py", marker='def auto_cascade(conn: sqlite3.Connection, rev_id: str, *,'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| таблица `quality_trace` (журнал управления) | `src/ragraf/quality_traces.py:53` |',
         path="src/ragraf/quality_traces.py", marker='CREATE TABLE IF NOT EXISTS quality_trace ('),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| отказ записи `TraceRefusal` | `src/ragraf/quality_traces.py:69` |',
         path="src/ragraf/quality_traces.py", marker='class TraceRefusal(ValueError):'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| канонический JSON и отпечаток | `src/ragraf/quality_traces.py:79`, `:85` |',
         path="src/ragraf/quality_traces.py", marker='def canonical(payload: Any) -> str:'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| запись сбоя | `src/ragraf/quality_traces.py:105` |',
         path="src/ragraf/quality_traces.py", marker='def save(conn: sqlite3.Connection, *, case: dict[str, Any],'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| компактный список | `src/ragraf/quality_traces.py:163` |',
         path="src/ragraf/quality_traces.py", marker='def traces(conn: sqlite3.Connection, *, case_id: str = "",'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| полная трасса | `src/ragraf/quality_traces.py:185` |',
         path="src/ragraf/quality_traces.py", marker='def load(conn: sqlite3.Connection, trace_id: int) -> dict[str, Any] | None:'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| повтор с вердиктом | `src/ragraf/quality_traces.py:203` |',
         path="src/ragraf/quality_traces.py", marker='def replay(conn: sqlite3.Connection, trace_id: int, *, rev: str,'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| операция фасада `quality_trace` | `src/ragraf/facade.py:8705` |',
         path="src/ragraf/facade.py", marker='def op_quality_trace(self, action: str = "list", case_id: str = "",'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| шаги сеанса и решения гейта (публичные помощники) | `src/ragraf/quality_cases.py:356`, `:361` |',
         path="src/ragraf/quality_cases.py", marker='def session_ops(control: Any, session: str) -> list[str]:'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| проверка одного кейса | `src/ragraf/quality_cases.py:366` |',
         path="src/ragraf/quality_cases.py", marker='def evaluate_one(case: dict[str, Any], *, graph: Any, control: Any, rev: str,'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 1 | Документный слой с интервалами действия (`valid_from` / `valid_to`, `doc_asof`, пометка заменённого) | `provenance` (Pro) | `doc_asof` | `src/ragraf/facade.py:6169` — операция; разбор интервала и запись слоя — `src/ragraf/provenance.py` | `tests/test_provenance.py` (66 свойств) |',
         path="src/ragraf/facade.py", marker='def op_doc_asof(self, query: str, date: str, limit: int = 20,'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 2 | Слой замысла: якоря `MODULE_CONTRACT` как факты и сверка «объявлено / вычислено» | `contract-drift` (Enterprise) | `contract_drift` | `src/ragraf/facade.py:1612` — операция; разбор якорей — `src/ragraf/anchors.py` | `tests/test_contracts_anchors.py` (35 свойств) |',
         path="src/ragraf/facade.py", marker='def op_contract_drift(self, manifest_path: str | None = None,'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 3 | Рёбра деривации и транзитивное устаревание: «что пересчитать» по цепочке выводов | `provenance` (Pro) | `dependents`, `stale_cascade` | `src/ragraf/provenance.py:924` — каскад; обратный обход и таблица пометок — тот же модуль | `tests/test_provenance.py` (66 свойств) |',
         path="src/ragraf/provenance.py", marker='def cascade(conn: sqlite3.Connection, rev_id: str, changed: Any = "", *,'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 4 | Утверждения по трассе, а не только по ответу; подтверждённый сбой как воспроизводимая трасса | `quality-loop` (Pro) | `quality_suite`, `quality_trace` | `src/ragraf/quality_traces.py:105` — запись трассы; проверка кейсов — `src/ragraf/quality_cases.py` | `tests/test_quality_cases.py` (49), `tests/test_quality_traces.py` (42) |',
         path="src/ragraf/quality_traces.py", marker='def save(conn: sqlite3.Connection, *, case: dict[str, Any],'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 5 | Сообщение отказа называет следующее действие | дисциплина текста (не товар) | — (поле `next_action` у отказов) | `src/ragraf/util.py:593` — единая сборка отказа | `tests/test_preflight.py` (34 свойства) |',
         path="src/ragraf/util.py", marker='def next_action(operation: str, arg: str = "", *, kind: str = "no_index",'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 6 | Пиннинг подписи операций и корреляция «описание изменилось — вызов упал» | `contract-pin` (Enterprise) | `contract_pin_status`, `contract_pin_approve`, `contract_pin_history`, `contract_pin_sample`, `contract_diff` | `src/ragraf/pin.py:723` — состояние набора; хеши — `src/ragraf/pin.py`; сводка между выпусками; ворота выпуска — `tools/release/gate_release.py`; весь выпуск — `tools/release/release.py` | `tests/test_contract_pin.py` (173 проверки) |',
         path="src/ragraf/pin.py", marker='def state_payload(current: dict[str, str],'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 7 | Парные прогоны и per-case регрессия в бенчмарке | `quality-loop` (Pro) | `quality_pair` | `src/ragraf/quality_cases.py:483` — сравнение прогонов по кейсам | `tests/test_quality_cases.py` (49 свойств) |',
         path="src/ragraf/quality_cases.py", marker='def pair(before: dict[str, Any], after: dict[str, Any], *,'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 8 | Сверка чисел в документации с кодом (источник истины — код) | дисциплина сборки (не товар) | — (гейт `surface_gen`) | `src/ragraf/surface.py:167` — счёт операций из AST | `tests/test_documented_numbers.py` (76 свойств) |',
         path="src/ragraf/surface.py", marker='def ops_of(root: Path) -> list[dict[str, Any]]:'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 9 | Предварительная оценка индексации легаси до первого прохода | `preflight` (Pro) | `preflight` | `src/ragraf/preflight.py:348` — осмотр корпуса, который ничего не создаёт | `tests/test_preflight.py` (34 свойства) |',
         path="src/ragraf/preflight.py", marker='def scan(root: str | Path, *, sample: int = 50,'),
)


# --- X7: каскад устаревания, трассы сбоев, карта поставки ----
PAIRS = PAIRS + (
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| таблица пометок `fact_stale` | `src/ragraf/provenance.py:148` |',
         path="src/ragraf/provenance.py", marker='CREATE TABLE IF NOT EXISTS fact_stale ('),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| общий обход рёбер в ширину (один на `dependents` и каскад) | `src/ragraf/provenance.py:657` |',
         path="src/ragraf/provenance.py", marker='def _walk(conn: sqlite3.Connection, rev_id: str, seed_ids: set[str],'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| отчёт «что пересчитать» без записи | `src/ragraf/provenance.py:782` |',
         path="src/ragraf/provenance.py", marker='def plan_cascade(conn: sqlite3.Connection, rev_id: str, changed: Any, *,'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| запись пометок | `src/ragraf/provenance.py:843` |',
         path="src/ragraf/provenance.py", marker='def apply_marks(conn: sqlite3.Connection, rev_id: str, plan: list[dict[str, Any]],'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| чтение поставленных пометок | `src/ragraf/provenance.py:872` |',
         path="src/ragraf/provenance.py", marker='def stale_marks(conn: sqlite3.Connection, rev_id: str, *,'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| закрытие пометок после пересчёта | `src/ragraf/provenance.py:912` |',
         path="src/ragraf/provenance.py", marker='def clear_marks(conn: sqlite3.Connection, rev_id: str) -> dict[str, int]:'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| три вида вызова одним входом | `src/ragraf/provenance.py:924` |',
         path="src/ragraf/provenance.py", marker='def cascade(conn: sqlite3.Connection, rev_id: str, changed: Any = "", *,'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| автоматические пометки при индексации | `src/ragraf/provenance.py:971` |',
         path="src/ragraf/provenance.py", marker='def auto_cascade(conn: sqlite3.Connection, rev_id: str, *,'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| хук индексатора (после сборки производных фактов) | `src/ragraf/indexer.py:679` |',
         path="src/ragraf/indexer.py", marker='cascade_stats = provenance_mod.auto_cascade(self.lexvec.conn, rev_id)'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| тот же хук в сборке документного графа | `src/ragraf/docgraph_extract.py:404` |',
         path="src/ragraf/docgraph_extract.py", marker='cascade = provenance_mod.auto_cascade(conn, rev_id)'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| операция фасада `stale_cascade` | `src/ragraf/facade.py:6260` |',
         path="src/ragraf/facade.py", marker='def op_stale_cascade(self, changed: str = "", depth: int = 3,'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='| свойства | `tests/test_provenance.py:327` (раздел 5б) |',
         path="tests/test_provenance.py", marker='print("5б. STALE_CASCADE: ЧТО ПЕРЕСЧИТАТЬ, ПОМЕТКИ И ЗАКРЫТИЕ")'),
    dict(doc="docs/03_implementation/STALE-CASCADE.md",
         anchor='- **при индексации** (`auto_cascade`, `src/ragraf/provenance.py:971`): `sha256`',
         path="src/ragraf/provenance.py", marker='def auto_cascade(conn: sqlite3.Connection, rev_id: str, *,'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| таблица `quality_trace` (журнал управления) | `src/ragraf/quality_traces.py:53` |',
         path="src/ragraf/quality_traces.py", marker='CREATE TABLE IF NOT EXISTS quality_trace ('),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| отказ записи `TraceRefusal` | `src/ragraf/quality_traces.py:69` |',
         path="src/ragraf/quality_traces.py", marker='class TraceRefusal(ValueError):'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| канонический JSON и отпечаток | `src/ragraf/quality_traces.py:79`, `:85` |',
         path="src/ragraf/quality_traces.py", marker='def canonical(payload: Any) -> str:'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| запись сбоя | `src/ragraf/quality_traces.py:105` |',
         path="src/ragraf/quality_traces.py", marker='def save(conn: sqlite3.Connection, *, case: dict[str, Any],'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| компактный список | `src/ragraf/quality_traces.py:163` |',
         path="src/ragraf/quality_traces.py", marker='def traces(conn: sqlite3.Connection, *, case_id: str = "",'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| полная трасса | `src/ragraf/quality_traces.py:185` |',
         path="src/ragraf/quality_traces.py", marker='def load(conn: sqlite3.Connection, trace_id: int) -> dict[str, Any] | None:'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| повтор с вердиктом | `src/ragraf/quality_traces.py:203` |',
         path="src/ragraf/quality_traces.py", marker='def replay(conn: sqlite3.Connection, trace_id: int, *, rev: str,'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| операция фасада `quality_trace` | `src/ragraf/facade.py:8705` |',
         path="src/ragraf/facade.py", marker='def op_quality_trace(self, action: str = "list", case_id: str = "",'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| шаги сеанса и решения гейта (публичные помощники) | `src/ragraf/quality_cases.py:356`, `:361` |',
         path="src/ragraf/quality_cases.py", marker='def session_ops(control: Any, session: str) -> list[str]:'),
    dict(doc="docs/03_implementation/QUALITY-TRACES.md",
         anchor='| проверка одного кейса | `src/ragraf/quality_cases.py:366` |',
         path="src/ragraf/quality_cases.py", marker='def evaluate_one(case: dict[str, Any], *, graph: Any, control: Any, rev: str,'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 1 | Документный слой с интервалами действия (`valid_from` / `valid_to`, `doc_asof`, пометка заменённого) | `provenance` (Pro) | `doc_asof` | `src/ragraf/facade.py:6169` — операция; разбор интервала и запись слоя — `src/ragraf/provenance.py` | `tests/test_provenance.py` (66 свойств) |',
         path="src/ragraf/facade.py", marker='def op_doc_asof(self, query: str, date: str, limit: int = 20,'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 2 | Слой замысла: якоря `MODULE_CONTRACT` как факты и сверка «объявлено / вычислено» | `contract-drift` (Enterprise) | `contract_drift` | `src/ragraf/facade.py:1612` — операция; разбор якорей — `src/ragraf/anchors.py` | `tests/test_contracts_anchors.py` (35 свойств) |',
         path="src/ragraf/facade.py", marker='def op_contract_drift(self, manifest_path: str | None = None,'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 3 | Рёбра деривации и транзитивное устаревание: «что пересчитать» по цепочке выводов | `provenance` (Pro) | `dependents`, `stale_cascade` | `src/ragraf/provenance.py:924` — каскад; обратный обход и таблица пометок — тот же модуль | `tests/test_provenance.py` (66 свойств) |',
         path="src/ragraf/provenance.py", marker='def cascade(conn: sqlite3.Connection, rev_id: str, changed: Any = "", *,'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 4 | Утверждения по трассе, а не только по ответу; подтверждённый сбой как воспроизводимая трасса | `quality-loop` (Pro) | `quality_suite`, `quality_trace` | `src/ragraf/quality_traces.py:105` — запись трассы; проверка кейсов — `src/ragraf/quality_cases.py` | `tests/test_quality_cases.py` (49), `tests/test_quality_traces.py` (42) |',
         path="src/ragraf/quality_traces.py", marker='def save(conn: sqlite3.Connection, *, case: dict[str, Any],'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 5 | Сообщение отказа называет следующее действие | дисциплина текста (не товар) | — (поле `next_action` у отказов) | `src/ragraf/util.py:593` — единая сборка отказа | `tests/test_preflight.py` (34 свойства) |',
         path="src/ragraf/util.py", marker='def next_action(operation: str, arg: str = "", *, kind: str = "no_index",'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 6 | Пиннинг подписи операций и корреляция «описание изменилось — вызов упал» | `contract-pin` (Enterprise) | `contract_pin_status`, `contract_pin_approve`, `contract_pin_history`, `contract_pin_sample`, `contract_diff` | `src/ragraf/pin.py:723` — состояние набора; хеши — `src/ragraf/pin.py`; сводка между выпусками; ворота выпуска — `tools/release/gate_release.py`; весь выпуск — `tools/release/release.py` | `tests/test_contract_pin.py` (173 проверки) |',
         path="src/ragraf/pin.py", marker='def state_payload(current: dict[str, str],'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 7 | Парные прогоны и per-case регрессия в бенчмарке | `quality-loop` (Pro) | `quality_pair` | `src/ragraf/quality_cases.py:483` — сравнение прогонов по кейсам | `tests/test_quality_cases.py` (49 свойств) |',
         path="src/ragraf/quality_cases.py", marker='def pair(before: dict[str, Any], after: dict[str, Any], *,'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 8 | Сверка чисел в документации с кодом (источник истины — код) | дисциплина сборки (не товар) | — (гейт `surface_gen`) | `src/ragraf/surface.py:167` — счёт операций из AST | `tests/test_documented_numbers.py` (76 свойств) |',
         path="src/ragraf/surface.py", marker='def ops_of(root: Path) -> list[dict[str, Any]]:'),
    dict(doc="docs/03_implementation/DELIVERY-MAP.md",
         anchor='| 9 | Предварительная оценка индексации легаси до первого прохода | `preflight` (Pro) | `preflight` | `src/ragraf/preflight.py:348` — осмотр корпуса, который ничего не создаёт | `tests/test_preflight.py` (34 свойства) |',
         path="src/ragraf/preflight.py", marker='def scan(root: str | Path, *, sample: int = 50,'),
    dict(doc="docs/03_implementation/STAGES-X7.md",
         anchor='| 3. Рёбра деривации | обратного обхода нет | обход есть (`src/ragraf/provenance.py:725`), нет каскадной пометки и отчёта «что пересчитать» |',
         path="src/ragraf/provenance.py", marker='def dependents(conn: sqlite3.Connection, rev_id: str, ref: str, *,'),
    dict(doc="docs/03_implementation/STAGES-X7.md",
         anchor='| 4. Утверждения по трассе | шаги и обход гейта не проверяются | проверяются (`src/ragraf/quality_cases.py:366`), нет сохранения сбоя как трассы |',
         path="src/ragraf/quality_cases.py", marker='def evaluate_one(case: dict[str, Any], *, graph: Any, control: Any, rev: str,'),
    dict(doc="docs/03_implementation/STAGES-X7.md",
         anchor='| 6. Пиннинг | нет корреляции «описание изменилось — вызов упал» | для `unknown_name` есть (`src/ragraf/pin.py:562`), для ошибок аргументов не вызывалось |',
         path="src/ragraf/pin.py", marker='def observe(conn: sqlite3.Connection, op: str, observed: str, *,'),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="проверка ссылок «файл:строка»")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="корень проекта")
    parser.add_argument("--json", action="store_true", help="машинный ответ")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    failures: list[dict] = []
    refs_total = 0
    docs_seen = 0
    illustrative_seen = 0
    cache: dict[Path, list[str]] = {}

    for doc in SCOPE:
        path = root / doc
        if not path.exists():
            failures.append(dict(doc=doc, line=0, ref="", kind="missing_document",
                                 detail="документ из списка отсутствует"))
            continue
        docs_seen += 1
        lines = _read(path)
        for lineno, rel, first, last in collect_refs(lines):
            refs_total += 1
            ref_text = f"{rel}:{first}" + (f"-{last}" if last else "")
            if any(item["doc"] == doc and item["ref"] == ref_text for item in ILLUSTRATIVE):
                illustrative_seen += 1
                continue
            target = root / rel.replace("\\", "/")
            if not target.exists():
                failures.append(dict(doc=doc, line=lineno, ref=ref_text, kind="missing_file",
                                     detail="файла нет по указанному пути"))
                continue
            body = cache.get(target)
            if body is None:
                body = _read(target)
                cache[target] = body
            lastn = last if last else first
            if first < 1 or lastn < first or lastn > len(body):
                failures.append(dict(doc=doc, line=lineno, ref=ref_text, kind="out_of_range",
                                     detail=f"строк в файле {len(body)}"))
                continue

    # --- покрытие: пара, объявленная причина, и третья группа — ничего ---
    # Ссылка без пары и без причины раньше проходила молча: проверялась только
    # адресуемость, а она верна и тогда, когда номер ведёт в чужой объект.
    # Поэтому необъявленная ссылка — находка, а не «остальное».
    pair_lines: dict[str, set[int]] = {}
    for pair in PAIRS:
        path = root / pair["doc"]
        if not path.exists():
            continue
        lines = _read(path)
        span = paragraph(lines, pair["anchor"])
        if span is None:
            continue
        lo, hi = span
        for lineno, rel, _first, _last in collect_refs(lines[lo - 1:hi]):
            if rel.replace("\\", "/") == pair["path"]:
                pair_lines.setdefault(pair["doc"], set()).add(lo + lineno - 1)

    declared_keys = {(item["doc"], item["ref"]) for item in DECLARED}
    paired_refs = declared_refs = 0
    by_document: dict[str, dict[str, int]] = {}
    for doc in SCOPE:
        path = root / doc
        if not path.exists():
            continue
        lines = _read(path)
        for lineno, rel, first, last in collect_refs(lines):
            ref_text = f"{rel}:{first}" + (f"-{last}" if last else "")
            bucket = by_document.setdefault(doc, {"refs": 0, "paired": 0, "declared": 0})
            bucket["refs"] += 1
            if lineno in pair_lines.get(doc, set()):
                bucket["paired"] += 1
                paired_refs += 1
            elif (doc, ref_text) in declared_keys:
                bucket["declared"] += 1
                declared_refs += 1
            elif not any(item["doc"] == doc and item["ref"] == ref_text for item in ILLUSTRATIVE):
                failures.append(dict(doc=doc, line=lineno, ref=ref_text, kind="undeclared_ref",
                                     detail=("ни пары, ни причины: попадание в объект не "
                                             "проверяется и не объявлено")))

    pairs_checked = 0
    pairs_failed = 0
    for pair in PAIRS:
        path = root / pair["doc"]
        if not path.exists():
            continue
        lines = _read(path)
        span = paragraph(lines, pair["anchor"])
        if span is None:
            pairs_checked += 1
            pairs_failed += 1
            failures.append(dict(doc=pair["doc"], line=0, ref=pair["path"], kind="stale_pair",
                                 detail=f"утверждение «{pair['anchor']}» в документе не найдено"))
            continue
        lo, hi = span
        wanted = pair["path"].replace("/", "\\")
        ranges = [(first, last if last else first)
                  for lineno, rel, first, last in collect_refs(lines[lo - 1:hi])
                  if rel.replace("/", "\\") == wanted
                  or rel.replace("\\", "/") == pair["path"]]
        pairs_checked += 1
        if not ranges:
            pairs_failed += 1
            failures.append(dict(doc=pair["doc"], line=lo, ref=pair["path"], kind="pair_unresolved",
                                 detail=f"утверждение «{pair['anchor']}» не ссылается на {pair['path']}"))
            continue
        target = root / pair["path"]
        body = cache.get(target)
        if body is None:
            body = _read(target) if target.exists() else []
            cache[target] = body
        hit = False
        for first, last in ranges:
            if first < 1 or last > len(body):
                continue
            if any(pair["marker"] in line for line in body[first - 1:last]):
                hit = True
                break
        if not hit:
            pairs_failed += 1
            failures.append(dict(doc=pair["doc"], line=lo, ref=pair["path"], kind="wrong_object",
                                 detail=(f"утверждение «{pair['anchor']}» ведёт в {pair['path']}, "
                                         f"но признака «{pair['marker']}» в указанных строках нет")))

    stale_declarations = 0
    for item in ILLUSTRATIVE:
        path = root / item["doc"]
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if item["ref"] not in text:
            stale_declarations += 1
            failures.append(dict(doc=item["doc"], line=0, ref=item["ref"], kind="stale_declaration",
                                 detail=(f"объявленная иллюстративной ссылка исчезла из документа: "
                                         f"объявление устарело ({item['reason']})")))
    for item in DECLARED:
        path = root / item["doc"]
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not _declared_present(text, item["ref"]):
            stale_declarations += 1
            failures.append(dict(doc=item["doc"], line=0, ref=item["ref"], kind="stale_declaration",
                                 detail=(f"объявленная ссылка исчезла из документа: "
                                         f"объявление устарело ({item['reason']})")))

    undeclared_refs = sum(1 for item in failures if item["kind"] == "undeclared_ref")
    failed = len(failures)
    ok = failed == 0 and refs_total > 0
    payload = dict(ok=ok, documents=docs_seen, refs=refs_total, failed=failed,
                   pairs=pairs_checked, pairs_failed=pairs_failed,
                   refs_paired=paired_refs, refs_declared=declared_refs,
                   refs_undeclared=undeclared_refs,
                   illustrative=illustrative_seen, stale_declarations=stale_declarations,
                   by_document=by_document, failures=failures)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        share = (100.0 * paired_refs / refs_total) if refs_total else 0.0
        print(f"[check_line_refs] документов: {docs_seen}, ссылок: {refs_total}, "
              f"неверных: {failed}")
        print(f"  с объявленным объектом (пара «утверждение → объект»): {paired_refs} "
              f"({share:.0f}% ссылок), провалено пар: {pairs_failed} из {pairs_checked}")
        print(f"  объявлено с причиной, без авто-проверки объекта: {declared_refs}")
        print(f"  не объявлено ничем: {undeclared_refs}")
        print(f"  иллюстративных объявлено: {illustrative_seen}, "
              f"устаревших объявлений: {stale_declarations}")
        for item in failures:
            print(f"  - {item['doc']}:{item['line']}  {item['ref']}"
                  f"  [{item['kind']}]  {item['detail']}")

    if refs_total == 0:
        print("НЕ ИЗМЕРЕНО: ни одной ссылки не найдено — обход разошёлся с документами",
              file=sys.stderr)
        return 2
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())




