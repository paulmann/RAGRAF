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
    "docs/skills/ragraf-core/SKILL.md",
    "docs/skills/ragraf-core/reference/repair.md",
    "docs/skills/ragraf-core/reference/operations.md",
    "docs/skills/ragraf-secrets/SKILL.md",
    "harness/skills/ragraf-core/SKILL.md",
    "harness/skills/ragraf-core/reference/repair.md",
    "harness/skills/ragraf-core/reference/operations.md",
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
    dict(doc="AGENTS.md", anchor="7. **Активная ревизия выбирается", path="src/ragraf/watch.py",
         marker="rev_id DESC"),
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
    dict(doc="docs/AGENTS-context.md", anchor="При понижении покрытия CPG", path="src/ragraf/indexer.py",
         marker='"cpg_downgrade"'),
    dict(doc="docs/AGENTS-context.md", anchor="прежний текст «CPG отключена", path="src/ragraf/indexer.py",
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
)

# Иллюстративные адреса: пример доказательства из чужого проекта, а не адрес
# этого. Объявлены с причиной, иначе проверка либо падала бы на них вечно,
# либо (хуже) молча их пропускала.
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
    dict(doc="docs/AGENTS-context.md", ref="tools/ragraf.py:15-19", key="E"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/cli.py:1128", key="D"),
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
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/indexer.py:1868-1872", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/config.py:580-617", key="A"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/lexvec.py:187-196", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/revisions.py:17", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/mcp_server.py:575-670", key="D"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/facade.py:852", key="F"),
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
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/admin_server.py:157", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/admin_server.py:209", key="F"),
    dict(doc="docs/AGENTS-context.md", ref="src/ragraf/admin_server.py:629", key="F"),
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
    dict(doc="docs/skills/ragraf-core/reference/repair.md", ref="src/ragraf/indexer.py:1868-1872", key="F"),
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
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/indexer.py:1868-1872", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/util.py:162-175", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/config.py:541", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/indexer.py:781-789", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/dedupe.py:165-223", key="E"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/config.py:276-301", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/compute.py:141-146", key="F"),
    dict(doc="harness/skills/ragraf-core/reference/repair.md", ref="src/ragraf/settings.py:327-386", key="A"),
    )
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
