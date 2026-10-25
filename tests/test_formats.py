"""Реестр форматов и настройка `index_formats`: покрытие, отбор, проверка.

Что закрепляется и почему именно это
    1. Реестр форматов (`formats.py`) и карта расширений (`config.LANG_BY_EXT`)
       покрывают друг друга. Расхождение здесь не даёт исключения: формат
       просто перестаёт управляться из панели, и выключить его невозможно.
       Измерено на первом прогоне: `.rmd` был в карте языков, но ни в одном
       формате — файл индексировался, а галочки для него не существовало.
    2. `*` означает «все», пустой список — ошибка. Иначе выключение последней
       галочки молча оставило бы корпус без индекса.
    3. Имя файла важнее расширения. Измерено: `CMakeLists.txt` определялся как
       «простой текст», потому что `.txt` проверялся раньше имени.
    4. Отбор по форматам действительно влияет на скан и считает пропуски
       отдельным счётчиком: «в корпусе нет PDF» и «PDF выключен» — разные
       состояния, и они не должны выглядеть одинаково.

Запуск: python tests/test_formats.py
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import config, formats, settings  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


print("=" * 78)
print("1. РЕЕСТР И КАРТА РАСШИРЕНИЙ ПОКРЫВАЮТ ДРУГ ДРУГА")
print("=" * 78)

unassigned = formats.unassigned_exts(config.LANG_BY_EXT)
check(not unassigned, "каждое расширение из карты языков отнесено к формату",
      f"без формата: {unassigned}")

orphans = formats.orphan_formats(config.LANG_BY_EXT)
check(not orphans, "у каждого формата есть известное расширение",
      f"пустые форматы: {orphans}")

ids = [f.id for f in formats.FORMATS]
check(len(ids) == len(set(ids)), "идентификаторы форматов уникальны",
      f"дубли: {sorted({i for i in ids if ids.count(i) > 1})}")

bad_group = [f.id for f in formats.FORMATS if f.group not in formats.GROUP_TITLES]
check(not bad_group, "у каждого формата известная группа", f"проблемные: {bad_group}")

bad_struct = [f.id for f in formats.FORMATS
              if f.structure not in formats.STRUCTURE_TITLES]
check(not bad_struct, "у каждого формата назван способ разбора",
      f"проблемные: {bad_struct}")

check(len(formats.FORMATS) >= 80, "форматов в реестре не меньше 80",
      f"получено {len(formats.FORMATS)}")
code_count = sum(1 for f in formats.FORMATS if f.group == "code")
check(code_count >= 55, "языков программирования не меньше 55",
      f"получено {code_count}")

print()
print("=" * 78)
print("2. ЗНАЧЕНИЕ НАСТРОЙКИ: «ВСЕ» И ОШИБКИ")
print("=" * 78)

all_ids = set(formats.ALL_IDS)
check(formats.normalize(None) == all_ids, "None → все форматы")
check(formats.normalize("*") == all_ids, "«*» → все форматы")
check(formats.normalize([]) == all_ids, "пустой список → все форматы (не «ничего»)")
check(formats.normalize(["word", "pdf"]) == {"word", "pdf"}, "список сохраняется")
check(formats.normalize(["word", "нет-такого"]) == {"word"},
      "неизвестный идентификатор отбрасывается")
check(formats.unknown_ids(["word", "опечатка"]) == ["опечатка"],
      "unknown_ids называет опечатку")
check(formats.unknown_ids("*") == [], "«*» не считается опечаткой")

print()
print("=" * 78)
print("3. ИМЯ ФАЙЛА ВАЖНЕЕ РАСШИРЕНИЯ")
print("=" * 78)

CASES = [
    ("Dockerfile", "dockerfile"),
    ("deploy/Dockerfile", "dockerfile"),
    ("Dockerfile.prod", "dockerfile"),
    ("Makefile", "make"),
    ("Makefile.am", "make"),
    ("CMakeLists.txt", "cmake"),
    ("src/CMakeLists.txt", "cmake"),
    ("a.py", "python"),
    ("a.kt", "kotlin"),
    ("a.docm", "word"),
    ("a.doc", "word"),
    ("a.xls", "excel"),
    ("a.pptm", "powerpoint"),
    ("a.epub", "epub"),
    ("a.pages", "pages"),
    ("a.неизвестное", None),
]
for path, want in CASES:
    got = formats.format_of_path(path)
    check(got == want, f"{path}: формат {want}", f"получено {got!r}")

check(config.ProjectConfig.language_of_static("CMakeLists.txt") == "cmake",
      "CMakeLists.txt определяется как cmake")
check(config.ProjectConfig.language_of_static("Dockerfile") == "dockerfile",
      "Dockerfile определяется как dockerfile")
check(config.ProjectConfig.language_of_static("x.rmd") == "markdown",
      ".rmd — markdown, а не R (разбор грамматикой R дал бы ошибки)")
check(config.ProjectConfig.language_of_static("x.pro") == "text",
      ".pro — текст, а не Prolog (расширение занято проектами Qt)")

print()
print("=" * 78)
print("4. КАТАЛОГ ДЛЯ ПАНЕЛИ")
print("=" * 78)

catalog = formats.catalog(["word", "python"])
by_id = {item["id"]: item for item in catalog}
check(by_id["word"]["enabled"] is True, "включённый формат помечен enabled")
check(by_id["pdf"]["enabled"] is False, "выключенный формат помечен enabled=false")
check(by_id["word"]["group_title"] == "Документы", "группа названа по-человечески")
check(all("structure_title" in item for item in catalog),
      "у каждого элемента каталога есть способ разбора")
check(len(catalog) == len(formats.FORMATS), "каталог содержит все форматы")

summary = formats.summary("*")
check(summary["enabled"] == summary["total"], "при «*» включено всё")
check(set(summary["groups"]) == {"code", "document", "data", "unsupported"},
      "сводка содержит все группы", f"получено {sorted(summary['groups'])}")

print()
print("=" * 78)
print("5. ОТБОР ФАЙЛОВ: is_enabled И СКАН КОРПУСА")
print("=" * 78)

check(formats.is_enabled("a.pdf", ["word"]) is False, "выключенный PDF не проходит")
check(formats.is_enabled("a.pdf", ["*"]) is True, "при «*» PDF проходит")
check(formats.is_enabled("a.неизвестное", ["word"]) is True,
      "файл неизвестного формата не выбрасывается молча")

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    (root / "a.py").write_text("print('x')\n", encoding="utf-8")
    (root / "b.md").write_text("# заголовок\n", encoding="utf-8")
    (root / "c.docx").write_bytes(b"PK\x03\x04" + b"0" * 64)
    (root / "Dockerfile").write_text("FROM python:3.13\n", encoding="utf-8")

    def make_config(name: str, extra: str) -> pathlib.Path:
        cfg_path = root / name
        cfg_path.write_text(
            "project: tmp\n"
            f"root: {root.as_posix()}\n"
            "languages:\n  - python\n  - markdown\n  - document\n  - dockerfile\n"
            'include:\n  - "**/*"\n'
            + extra,
            encoding="utf-8",
        )
        return cfg_path

    full = config.load(make_config("full.yaml", ""))
    scan_full = __import__("ragraf.corpus", fromlist=["scan"]).scan(full)
    names_full = {f.path for f in scan_full.files}
    check({"a.py", "b.md", "c.docx", "Dockerfile"} <= names_full,
          "без ограничений скан берёт все четыре файла", f"получено {sorted(names_full)}")
    check(scan_full.skipped_formats == 0,
          "без ограничений пропусков по формату нет",
          f"получено {scan_full.skipped_formats}")

    limited = config.load(make_config(
        "limited.yaml", "formats:\n  - python\n  - dockerfile\n"
    ))
    scan_limited = __import__("ragraf.corpus", fromlist=["scan"]).scan(limited)
    names_limited = {f.path for f in scan_limited.files}
    check(names_limited == {"a.py", "Dockerfile"},
          "ограничение форматов оставляет только python и dockerfile",
          f"получено {sorted(names_limited)}")
    check(scan_limited.skipped_formats == 2,
          "пропуски по формату посчитаны отдельно",
          f"получено {scan_limited.skipped_formats}")
    check(scan_limited.summary().get("skipped_formats") == 2,
          "счётчик пропусков попал в сводку")

    # Второй способ задать то же самое — секция index (так пишет панель).
    in_index = config.load(make_config(
        "index.yaml", "index:\n  index_formats:\n    - markdown\n"
    ))
    check(in_index.formats == {"markdown"},
          "index.index_formats тоже действует", f"получено {in_index.formats}")

print()
print("=" * 78)
print("6. ПРОВЕРКА НАСТРОЙКИ (settings.validate)")
print("=" * 78)

check(settings.validate({"index_formats": ["*"]}) == [], "«*» принимается")
check(settings.validate({"index_formats": ["word", "pdf", "kotlin"]}) == [],
      "список известных форматов принимается")
typo = settings.validate({"index_formats": ["wind"]})
check(bool(typo) and "wind" in typo[0], "опечатка отклоняется с названием",
      f"получено {typo}")
empty = settings.validate({"index_formats": []})
check(bool(empty), "пустой список отклоняется", f"получено {empty}")
bad_type = settings.validate({"index_formats": 5})
check(bool(bad_type), "число вместо списка отклоняется", f"получено {bad_type}")
check(settings.validate({"index_formats": "word,pdf"}) == [],
      "строка с перечислением через запятую тоже принимается")

state = settings.state({})
check(len(state.formats) == len(formats.FORMATS),
      "состояние панели содержит каталог форматов")
check(state.formats_summary.get("total") == len(formats.FORMATS),
      "состояние панели содержит сводку")
check("index_formats" not in state.editable,
      "index_formats не показывается в таблице разработки (это не число)")
check("index_formats" in settings.STORE_KEYS,
      "index_formats читается и проверяется хранилищем")

print()
print(f"ИТОГ: {PASS} проверок пройдено, {FAIL} провалено")
sys.exit(1 if FAIL else 0)
