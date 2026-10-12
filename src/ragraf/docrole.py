"""Определение роли документа: ТЗ, правила агента, описание, отчёт, план.

Зачем это нужно
    Документация уже попадала в индекс, но безлико: чанк из ТЗ выглядел так же,
    как чанк из журнала правок. Агенту, который ищет «где смотреть требования»,
    приходилось читать угадыванием, хотя признак роли лежит прямо в имени файла.

    Разведка по двум каталогам разработки (C:\\AutoClaw — 1550 документов после
    отсева мусора, C:\\<C#-проект>\\Service) показала, какие конвенции там реально
    есть. Наблюдения, на которых построены правила ниже:

      * `AGENTS.md` — 21 файл, включая корни проектов; это правила агента.
      * `README*` — 122 файла; описание проекта.
      * `CHANGELOG*` — 20 файлов, все с разным содержимым; история версий.
      * `*_REPORT.md` / `*отчёт*` — 38 файлов; отчёты.
      * `ARCHITECTURE` / `overview` / `SPEC` — описание устройства.
      * `PLAN*` / `Road_Map` / `ROADMAP` — планы.
      * `TZ.md` / `ТЗ-*.md` — всего 2 файла, но признак однозначный.
      * `docs\\NN_<имя>` и `docs\\NN-name.md` — иерархия документации проекта.

Чего здесь НЕ делается
    * Роль не угадывается по содержанию файла. Единственное исключение —
      заголовок первых строк, и только для ТЗ: замер показал, что имя `TZ.md`
      есть лишь у 2 файлов из 1550, а заголовок «Техническое задание» — у 4.
      Без проверки заголовка 3 из 4 технических заданий остались бы без метки.
    * Роль не отменяет поиск: документ по-прежнему ищется целиком. Пометка
      только сообщает агенту, что это за документ.
    * Полнотекстового чтения всего файла нет: читаются первые строки (не больше
      восьми) — этого достаточно для заголовка и дёшево по времени.

Про честность пометки
    Роль `documentation` ставится, когда файл документацией и является (`docs/`,
    `.md` вне каталогов кода). Роль `other_md` — когда это .md, но ни на один
    признак он не похож. Разница важна: `documentation` — «это документ,
    смотри сюда», `other_md` — «это markdown, но непонятно что».
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

# Роли документов. Порядок в кортеже — порядок проверки: побеждает первое
# совпадение, поэтому более точные признаки идут раньше.
ROLES = (
    "agent_rules",      # правила работы агента в проекте
    "tech_spec",        # техническое задание
    "acceptance",       # критерии приёмки
    "architecture",     # описание устройства
    "plan",             # план работ
    "report",           # отчёт о выполненном
    "changelog",        # история изменений
    "readme",           # описание проекта
    "reference",        # справочные материалы, карты, реестры
    "documentation",    # прочая документация в каталоге документов
    "other_md",         # markdown без опознанных признаков
)

# Человеческие названия ролей: нужны в ответе, чтобы агент понимал пометку.
ROLE_TITLES = {
    "agent_rules": "правила агента",
    "tech_spec": "техническое задание",
    "acceptance": "критерии приёмки",
    "architecture": "описание устройства",
    "plan": "план работ",
    "report": "отчёт",
    "changelog": "история изменений",
    "readme": "описание проекта",
    "reference": "справочный материал",
    "documentation": "документация",
    "other_md": "markdown без опознанной роли",
}

# Правила по имени файла: (роль, образец имени без учёта регистра).
# Образец применяется к имени файла целиком (с расширением).
NAME_RULES: tuple[tuple[str, str], ...] = (
    # Правила агента. Замер: 21 файл AGENTS.md, плюс иные конвенции —
    # SYSTEM_PROMPT.md (7 копий), AGENTS-context.md (8), agent-instructions (9).
    ("agent_rules", r"^agents?\.md$"),
    ("agent_rules", r"^agents?[-_].*\.md$"),
    ("agent_rules", r"^agent-instructions.*\.md$"),
    ("agent_rules", r"^system_prompt\.md$"),
    ("agent_rules", r"^system-prompt\.md$"),
    ("agent_rules", r".*agent[-_]?context.*\.md$"),
    ("agent_rules", r"^skill\.md$"),
    ("agent_rules", r"^instructions?\.md$"),
    ("agent_rules", r"^claude\.md$"),
    ("agent_rules", r"^\.cursorrules$"),
    # Техническое задание.
    ("tech_spec", r"^tz\.md$"),
    ("tech_spec", r"^tz[-_].*\.md$"),
    ("tech_spec", r"^тз[-_].*\.md$"),
    ("tech_spec", r"^тз\.md$"),
    ("tech_spec", r".*техзадание.*\.md$"),
    ("tech_spec", r".*technical[-_]specification.*\.md$"),
    ("tech_spec", r"^spec\.md$"),
    ("tech_spec", r"^spec[-_].*\.md$"),
    ("tech_spec", r".*requirements?\.md$"),
    # Критерии приёмки.
    ("acceptance", r"^acceptance.*\.md$"),
    ("acceptance", r".*acceptance.*\.md$"),
    ("acceptance", r"^критерии.*\.md$"),
    # Описание устройства.
    ("architecture", r"^architecture.*\.md$"),
    ("architecture", r"^adr[-_].*\.md$"),
    ("architecture", r".*архитектур.*\.md$"),
    ("architecture", r"^overview.*\.md$"),
    ("architecture", r"^.*-overview\.md$"),
    ("architecture", r"^data[-_]model.*\.md$"),
    # План.
    ("plan", r"^plan.*\.md$"),
    ("plan", r"^road[_-]?map.*\.md$"),
    # Дорожная карта часто идёт с номером или префиксом каталога:
    # замер на корпусе Service дал `docs/80-roadmap.md`, который первая
    # версия правил пропускала, потому что требовала слова в начале имени.
    ("plan", r"^.*[-_]road[_-]?map.*\.md$"),
    ("plan", r"^.*road[_-]?map.*\.md$"),
    ("plan", r"^план[-_].*\.md$"),
    ("plan", r"^.*-план.*\.md$"),
    # Отчёт.
    ("report", r".*_report.*\.md$"),
    ("report", r".*[-_]report.*\.md$"),
    ("report", r"^report.*\.md$"),
    ("report", r".*отч[её]т.*\.md$"),
    ("report", r"^bugs?\.md$"),
    ("report", r"^.*_status.*\.md$"),
    # Сводка пишется и слитно (TestSummary.md), и через разделитель.
    ("report", r"^.*[-_]?summary.*\.md$"),
    # Сводки по проверкам: TestReport, TestSummary, TestResults.
    ("report", r"^test[-_]?(report|summary|results).*\.md$"),
    ("report", r"^.*[-_]test.*\.md$"),
    # История изменений.
    ("changelog", r"^changelog.*\.md$"),
    ("changelog", r"^changes?\.md$"),
    ("changelog", r"^история.*\.md$"),
    # Описание проекта.
    ("readme", r"^readme.*\.md$"),
    ("readme", r"^read[-_]?me.*\.md$"),
    ("readme", r"^прочти.*\.md$"),
    # Справочные материалы: карты, реестры, списки, руководства.
    ("reference", r"^project_map.*\.md$"),
    ("reference", r".*[-_]map\.md$"),
    ("reference", r"^db[_-]?links?\.md$"),
    ("reference", r".*[-_]links?\.md$"),
    ("reference", r"^.*guide.*\.md$"),
    ("reference", r".*руководство.*\.md$"),
    ("reference", r".*licenses?\.md$"),
    ("reference", r".*registry.*\.md$"),
    ("reference", r".*реестр.*\.md$"),
)

# Образцы заголовков в первых строках. Нужны только для ТЗ и приёмки:
# замер показал, что по имени они находятся плохо (2 файла ТЗ из 1550),
# а по заголовку — заметно лучше (4 «Техническое задание»).
TITLE_RULES: tuple[tuple[str, str], ...] = (
    ("tech_spec", r"техническ(ое|ого)\s+задани"),
    ("tech_spec", r"^\s*тз\b"),
    ("tech_spec", r"\btechnical\s+specification\b"),
    ("tech_spec", r"^\s*#\s*сжатое\s+тз"),
    ("acceptance", r"критерии\s+при[её]мки"),
    ("agent_rules", r"agent\s+instructions"),
    ("agent_rules", r"правила\s+(работы\s+)?агента"),
)

# Каталоги, наличие которых в пути само делает файл документацией.
DOC_DIR_MARKERS = (
    "docs/", "doc/", "documentation/", "документация/",
    "po-vault/", "spec/", "manuals/",
)

# Каталоги, где markdown — это сопровождение кода, а не документация проекта.
# Замер: так лежат tests/fixtures/README.md, node_modules/*/readme.md и подобные.
CODE_SIDE_MARKERS = (
    "node_modules/", "vendor/", "tests/", "test/", "fixtures/",
    "__pycache__/", ".git/", "dist/", "build/",
)

_compiled_name = tuple(
    (role, re.compile(pattern, re.IGNORECASE)) for role, pattern in NAME_RULES
)
_compiled_title = tuple(
    (role, re.compile(pattern, re.IGNORECASE | re.MULTILINE))
    for role, pattern in TITLE_RULES
)

# Сколько строк файла читать для проверки заголовка. Восьми достаточно:
# заголовок и шапка с версией всегда выше.
HEAD_LINES = 8


class DocRole:
    """Роль документа: имя, название для человека, чем определено."""

    __slots__ = ("role", "evidence")

    def __init__(self, role: str, evidence: str) -> None:
        self.role = role
        self.evidence = evidence

    @property
    def title(self) -> str:
        return ROLE_TITLES.get(self.role, self.role)

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "title": self.title, "evidence": self.evidence}

    def __repr__(self) -> str:  # для отладки
        return f"DocRole({self.role!r}, {self.evidence!r})"


def classify(path: str, head_text: str | None = None) -> DocRole:
    """Определить роль документа.

    path — путь файла внутри корпуса (в прямой форме, через «/»).
    head_text — первые строки файла (необязательно). Если переданы, они
    используются для распознавания ТЗ по заголовку.

    Порядок проверки:
      1. имя файла (самый надёжный и дешёвый признак);
      2. заголовок в первых строках (для ТЗ и приёмки);
      3. расположение в каталоге документации;
      4. иначе — markdown без опознанной роли.

    Порядок намеренно такой: имя важнее расположения. `AGENTS.md` остаётся
    правилами агента даже если лежит в `docs/`, а `README.md` в `tests/fixtures`
    так и остаётся описанием, но помечается как сопровождение кода.
    """
    low = path.lower().replace("\\", "/")
    name = PurePosixPath(low).name
    parts = PurePosixPath(low).parts
    # Каталог без имени файла: нужен для проверки расположения.
    dirs = "/".join(parts[:-1]) + "/" if len(parts) > 1 else ""

    in_code_side = any(marker in low for marker in CODE_SIDE_MARKERS)

    # 1. Имя файла.
    for role, pattern in _compiled_name:
        if pattern.match(name):
            where = "имя файла"
            if in_code_side:
                where += " (в каталоге сопровождения кода)"
            return DocRole(role, where)

    # 2. Заголовок первых строк — только если имя ничего не дало.
    if head_text:
        head = "\n".join(head_text.splitlines()[:HEAD_LINES])
        for role, pattern in _compiled_title:
            if pattern.search(head):
                return DocRole(role, "заголовок в первых строках файла")

    # 3. Расположение в каталоге документации.
    if in_code_side:
        return DocRole("other_md", "markdown в каталоге сопровождения кода")
    if any(marker in dirs for marker in DOC_DIR_MARKERS):
        return DocRole("documentation", "лежит в каталоге документации")

    # 4. Опознать не удалось.
    if any(marker in low for marker in DOC_DIR_MARKERS):
        return DocRole("documentation", "путь содержит признак каталога документов")
    return DocRole("other_md", "признаков роли не найдено")


def is_documentation(role: str) -> bool:
    """Считать ли роль документом проекта в широком смысле."""
    return role in (
        "agent_rules", "tech_spec", "acceptance", "architecture", "plan",
        "report", "changelog", "readme", "reference", "documentation",
    )
