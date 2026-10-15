"""Создание конфигурации корпуса для нового проекта.

Зачем этот модуль
    До него процесс подключения нового проекта был **замкнутым кругом**:

      * команда `register` требует существующий конфиг (аргумент `--project`);
      * команды, которая бы этот конфиг создала, не было;
      * значит подключить новый проект через RAGRAF было нельзя без ручного
        копирования чужого конфига и правки путей.

    На этом и споткнулся агент MD.Viewer: он получил указание «зарегистрировать
    разработку», но выполнить его было нечем.

Что делает
    Осматривает каталог проекта и собирает конфиг по факту:
      * определяет языки по расширениям файлов (PHP, JS, CSS, HTML, Markdown, SQL);
      * находит каталоги, которые надо исключить: зависимости, сборка, кеши,
        каталоги с секретами;
      * ставит пороги авто-реиндексации в зависимости от размера корпуса;
      * проверяет, что каталог существует и что в нём есть файлы нужных языков.

Почему осмотр, а не шаблон
    Шаблон пришлось бы править в каждом проекте руками. Осмотр даёт конфиг,
    который сразу описывает реальный проект, и его остаётся только проверить.

Чего НЕ делает
    Не индексирует: создание конфига и индексация разделены. Пользователь должен
    увидеть, что за корпус получился, прежде чем по нему построится индекс.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any

from . import util

# Карта расширений → язык берётся ИЗ ОСНОВНОГО МОДУЛЯ, а не дублируется.
# Это важно: раньше здесь была своя копия, и она разошлась
# с основной — в ней не оказалось JavaScript, из-за чего 4 файла .js
# проекта MD.Viewer не попали в конфигурацию. Одна точка правды.
from .config import LANG_BY_EXT as EXT_TO_LANG  # noqa: F401

# Каталоги, которые почти всегда надо исключать. Список собран из практики
# на четырёх проектах: это зависимости, сборка, кеши и служебные каталоги.
EXCLUDE_DIRS: dict[str, str] = {
    ".ragraf": "служебный каталог RAGRAF: внутри индекс и ревизии",
    ".git": "история версий",
    ".zwork": "служебный каталог агента",
    "node_modules": "зависимости JavaScript",
    "vendor": "зависимости PHP и других языков",
    "bower_components": "зависимости JavaScript",
    "dist": "собранные файлы",
    "build": "собранные файлы",
    "target": "собранные файлы Java и Rust",
    ".venv": "виртуальное окружение Python",
    "venv": "виртуальное окружение Python",
    "__pycache__": "кеш байткода Python",
    ".pytest_cache": "кеш проверок",
    ".mypy_cache": "кеш проверки типов",
    "coverage": "отчёты покрытия",
    "uploads": "загруженные пользователями файлы: часто содержат персональные данные",
    "cache": "кеш приложения",
    "logs": "журналы работы",
    ".idea": "настройки среды разработки",
    ".vscode": "настройки среды разработки",
    # --- Сборка и зависимости .NET ---
    # Добавлено после разбора корпуса C#-проекта C:\1st_RdpMON\Service.
    #
    # Про publish — замер, а не догадка. В publish 406 файлов, 389,5 МБ.
    # При проверке оказалось, что там есть и файлы кода (.html, .css, .js) —
    # исключать такое по имени было бы рискованно. Проверено по имени
    # и содержимому (tools/check_publish_sources.py): у всех трёх нашёлся
    # источник в src\RdpAudit.Web\wwwroot, причём содержимое отличается —
    # поэтому сборка — это копия, а не единственный экземпляр:
    #   publish\...\rdpaudit-web.css 7137 б  ←  src\...\rdpaudit-web.css 6769 б
    #   publish\...\rdpaudit-web.js  17561 б ←  src\...\rdpaudit-web.js  17056 б
    #   publish\...\index.html       4731 б  ←  src\...\index.html       4618 б
    # Значит, исключение безопасно: источник попадёт в индекс, и поиск будет
    # выдавать исходник, а не сборку.
    "publish": "готовая сборка .NET: копии исходников и двоичные файлы",
    "obj": "промежуточные файлы сборки .NET",
    "bin": "двоичные файлы сборки .NET",
    "packages": "зависимости NuGet",
    "TestResults": "результаты прогонов проверок",
    ".nuget": "кеш пакетов NuGet",
    # Про obj отдельно: там есть файлы .cs (168 штук в 15 каталогах), и по
    # имени можно было бы решить, что это исходники. Проверено: все они
    # СГЕНЕРИРОВАНЫ сборкой — AssemblyInfo.cs, GlobalUsings.g.cs,
    # .NETCoreApp,Version=v8.0.AssemblyAttributes.cs. В индексе они только
    # зашумили бы поиск по классам. Пути вида obj\Debug\...\x64\Debug\...
    # подтверждают, что это выход сборки.
    #
    # --- Служебные каталоги агента ---
    # Замер: в C:\1st_RdpMON\Service\.dev_agent 2376 файлов, из них 1911 —
    # резервные копии .bak (80%). Это история работы агента, а не исходники.
    # Остальное — журналы и рабочие заметки.
    ".dev_agent": "служебный каталог агента: резервные копии и журналы",
    ".pi": "служебный каталог среды агента",
    # --- согласовано с DEFAULT_EXCLUDES (config.py) ---
    # Эти каталоги должны быть в списке init, иначе `init` не исключит
    # их при первом осмотре, хотя базовый список их уже закрывает.
    ".agents": "служебный каталог",
    "var": "служебный каталог",
}
# Каталоги, которые НЕ исключаются по имени, хотя название намекает на
# временность. Решение принято по замеру, а не по названию:
#
#   .staging в C:\1st_RdpMON\Service — там лежит ЕДИНСТВЕННЫЙ файл .sh
#   проекта (.staging\port-diff\reference.sh, 23 176 б) и портированные
#   скрипты .ps1, включая .staging\ps-port\clean-bom-senior.ps1 (38 938 б).
#   Исключить этот каталог — значит прямо саботировать задачу
#   «индексировать ps1, sh, bat»: в корпусе не окажется ни одного .sh.
#
# Вместо каталога исключаются РЕЗЕРВНЫЕ КОПИИ по шаблонам имён (ниже):
# так история правок не попадёт в индекс, а рабочие файлы останутся.

# Шаблоны ФАЙЛОВ, которые почти всегда содержат секреты.
SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("**/*.local.php", "локальные настройки с реквизитами доступа"),
    ("**/config.local.*", "локальные настройки"),
    ("**/.env", "файл окружения с секретами"),
    ("**/.env.*", "файл окружения с секретами"),
    ("**/*.pem", "ключи и сертификаты"),
    ("**/*.key", "ключи"),
    ("**/id_rsa*", "закрытые ключи SSH"),
    ("**/*.pfx", "хранилище сертификатов"),
    ("**/*credentials*.json", "файлы с реквизитами"),
)

# Шаблоны РЕЗЕРВНЫХ КОПИЙ и временных файлов.
#
# Зачем отдельно от каталогов. Замер на C#-проекте: в нём 1911 файлов .bak,
# и они лежат не в одном каталоге, а разбросаны по дереву. Исключать ради них
# целый каталог (.dev_agent) — терять вместе с ними и живые файлы. Поэтому
# история правок отсекается по имени файла, где бы он ни лежал.
BACKUP_PATTERNS: tuple[tuple[str, str], ...] = (
    ("**/*.bak", "резервная копия файла"),
    ("**/*.bak.*", "резервная копия с расширением исходного файла"),
    ("**/*.orig", "копия до слияния"),
    ("**/*.old", "прежняя версия файла"),
    ("**/*.save", "сохранённая копия"),
    ("**/*.tmp", "временный файл"),
    ("**/~$*", "временный файл офисного пакета"),
    ("**/*.rej", "отклонённая часть заплатки"),
    # Копии, которые агент делает перед каждой правкой: имя вида
    # file.php.20261004-120000.bak или file.php.before
    ("**/*.before", "копия перед правкой"),
    ("**/*.after", "копия результата правки"),
)

# Минимальное число файлов, чтобы корпус считался осмысленным.
MIN_FILES = 3


@dataclass
class CorpusScan:
    """Что найдено в каталоге проекта."""

    root: pathlib.Path
    languages: dict[str, int] = field(default_factory=dict)
    total_files: int = 0
    total_bytes: int = 0
    exclude_dirs: list[str] = field(default_factory=list)
    secret_patterns: list[tuple[str, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def inspect_project(root: pathlib.Path) -> CorpusScan:
    """Осмотреть каталог проекта и собрать сведения для конфига.

    Обход идёт вручную, а не через `rglob`: исключённые каталоги надо пропускать
    ЦЕЛИКОМ, иначе обход заходит в `node_modules` с десятками тысяч файлов и
    занимает минуты. Проверено на проекте с зависимостями.
    """
    scan = CorpusScan(root=root)

    if not root.exists():
        scan.problems.append(f"каталог не существует: {root}")
        return scan

    # Обход с отсечением исключённых каталогов.
    stack = [root]
    seen_dirs: set[str] = set()
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir():
                    name = entry.name
                    if name in EXCLUDE_DIRS:
                        if name not in seen_dirs:
                            scan.exclude_dirs.append(name)
                            seen_dirs.add(name)
                        continue
                    stack.append(entry)
                    continue
                if not entry.is_file():
                    continue
            except OSError:
                continue

            ext = entry.suffix.lower()
            lang = EXT_TO_LANG.get(ext)
            if lang is None:
                continue
            scan.languages[lang] = scan.languages.get(lang, 0) + 1
            scan.total_files += 1
            try:
                scan.total_bytes += entry.stat().st_size
            except OSError:
                pass

    # Секретные файлы: проверяется наличие по шаблонам.
    for pattern, reason in SECRET_PATTERNS:
        try:
            if any(root.glob(pattern)):
                scan.secret_patterns.append((pattern, reason))
        except Exception:
            continue

    if scan.total_files < MIN_FILES:
        scan.problems.append(
            f"найдено файлов с кодом: {scan.total_files} — слишком мало "
            f"(нужно хотя бы {MIN_FILES}); проверьте каталог проекта"
        )
    return scan


def render_config(
    scan: CorpusScan,
    *,
    project: str,
    corpus_config_path: pathlib.Path | None = None,
) -> str:
    """Собрать текст конфигурации по результатам осмотра.

    Значения по умолчанию не выдумываются: пороги авто-реиндексации зависят от
    размера корпуса, и для крупного проекта берутся другие числа.
    """
    langs = sorted(scan.languages)
    lines: list[str] = [
        f"# RAGRAF — конфигурация корпуса «{project}»",
        "#",
        "# Файл собран командой init по фактическому содержимому каталога.",
        "# Проверьте списки include и exclude: от них зависит, что попадёт в индекс.",
        "",
        f"project: {project}",
        f"root: {util.to_posix(scan.root)}",
        "",
        "languages:",
    ]
    for lang in langs:
        count = scan.languages[lang]
        lines.append(f"  - {lang}    # файлов: {count}")

    lines += ["", "include:"]
    # Шаблоны включения собираются по найденным языкам — только нужные.
    ext_by_lang: dict[str, list[str]] = {}
    for ext, lang in EXT_TO_LANG.items():
        ext_by_lang.setdefault(lang, []).append(ext)
    for lang in langs:
        for ext in sorted(ext_by_lang.get(lang, [])):
            lines.append(f'  - "**/*{ext}"')

    lines += ["", "exclude:"]
    if scan.exclude_dirs:
        lines.append("  # Каталоги, исключённые при осмотре проекта.")
        for name in sorted(scan.exclude_dirs):
            reason = EXCLUDE_DIRS.get(name, "")
            comment = f"    # {reason}" if reason else ""
            lines.append(f'  - "**/{name}/**"{comment}')
    else:
        lines.append("  # Исключений не найдено: каталог проекта небольшой.")
    # Резервные копии исключаются ВСЕГДА и по имени файла, а не по каталогу.
    #
    # Причина: замер на C#-проекте показал 1911 файлов .bak, разбросанных по
    # дереву. Выборочное исключение каталогов не помогает — копии лежат и
    # рядом с рабочими файлами. Без этих шаблонов индекс наполняется историей
    # правок, и поиск находит устаревшую версию файла как актуальную.
    lines.append("")
    lines.append("  # Резервные копии и временные файлы: история правок, не исходники.")
    for pattern, reason in BACKUP_PATTERNS:
        lines.append(f'  - "{pattern}"    # {reason}')
    # Служебный каталог RAGRAF исключается всегда: иначе индекс попадёт в себя.
    if ".ragraf" not in scan.exclude_dirs:
        lines.append('  - "**/.ragraf/**"    # служебный каталог RAGRAF')

    lines += ["", "secrets_exclude:"]
    if scan.secret_patterns:
        lines.append("  # Файлы с секретами: исключаются из индекса (НФТ-3).")
        for pattern, reason in scan.secret_patterns:
            lines.append(f'  - "{pattern}"    # {reason}')
    else:
        lines.append("  # Файлов с секретами по типовым шаблонам не найдено.")
        lines.append('  - "**/*.local.php"')

    # Пороги зависят от размера корпуса: чем больше файлов, тем дороже пересбор.
    small = scan.total_files < 200
    lines += [
        "",
        "db:",
        "  enabled: false    # схема БД читается из миграций, если они есть",
        "",
        "embedding:",
        "  model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "  dim: 384",
        "  batch_size: 64",
        "  # Вычислитель: видеокарта включается только когда работы больше порога.",
        "  use_gpu: true",
        "  gpu_min_texts: 1800",
        "  gpu_batch_size: 128",
        "  device: auto",
        "",
        "chunking:",
        "  by_symbol: true",
        "  max_chunk_lines: 120",
        "  min_chunk_lines: 3",
        "",
        "index:",
        "  retain_revs: 3",
        "  batch_size: 2000",
        "  joern_enabled: true",
        "  joern_server: true",
        "  joern_server_port: 9500",
        "  joern_heap_gb: 8",
        "  graph_gpu: true",
        "  graph_gpu_min_nodes: 5000",
    ]
    if small:
        lines += [
            f"  # Корпус небольшой ({scan.total_files} файлов): пороги базовые.",
            "  reindex_after_changes: 5",
            "  full_reindex_after_changes: 20",
            "  reindex_interval_minutes: 15",
        ]
    else:
        lines += [
            f"  # Корпус крупный ({scan.total_files} файлов): полный пересбор дорог,",
            "  # поэтому он выполняется реже — по накоплению, а не после каждой правки.",
            "  reindex_after_changes: 10",
            "  full_reindex_after_changes: 40",
            "  reindex_interval_minutes: 30",
        ]
    lines.append("")
    return "\n".join(lines)


def create_config(
    root: pathlib.Path,
    *,
    project: str | None = None,
    config_path: pathlib.Path | None = None,
    overwrite: bool = False,
) -> tuple[pathlib.Path, CorpusScan]:
    """Создать конфигурацию корпуса для проекта.

    Возвращает (путь к конфигу, результаты осмотра). Если конфиг уже есть и
    перезапись не разрешена — поднимается исключение: молча затирать настройки
    корпуса нельзя, в них могут быть ручные правки.
    """
    root = pathlib.Path(util.to_posix(str(root))).resolve()
    name = project or root.name
    target = config_path or (pathlib.Path(__file__).resolve().parents[2]
                             / "config" / f"{_slug(name)}.yaml")

    if target.exists() and not overwrite:
        raise FileExistsError(
            f"конфигурация уже существует: {target}; "
            f"для перезаписи укажите overwrite=True"
        )

    scan = inspect_project(root)
    if not scan.ok:
        raise ValueError("; ".join(scan.problems))

    target.parent.mkdir(parents=True, exist_ok=True)
    text = render_config(scan, project=name)
    target.write_text(text, encoding="utf-8", newline="\n")
    return target, scan


def _slug(name: str) -> str:
    """Имя файла из имени проекта: только безопасные символы."""
    import re

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower()
    return slug or "corpus"
