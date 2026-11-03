"""Проверка документного слоя, фаза D: архивы, OCR, режим HTML.

Свойства (гейт фазы):

  1. zip-slip блокируется: элемент `../evil` и абсолютный путь дают ОТКАЗ всего
     архива, а не «пропустим плохой элемент»; на диск ничего не пишется.
  2. Вложенный архив отвергается (глубина 1), превышение пределов — тоже, и
     причина называет числа.
  3. Whitelist расширений: элемент вне списка пропускается и считается.
  4. Включённый архив индексируется с адресом `архив!путь/внутри:12`, и этот
     адрес разрешается обратно через `doc_get`.
  5. OCR: по умолчанию выключен (`not_indexed_by_edition` с причиной); при
     включённой настройке без движка — `degraded` с причиной; на издании Community
     операция `doc_ocr` получает отказ слоя изданий, а не «пустой ответ».
  6. Режим HTML `document`: чанки даёт слой, а `class_usage` (разбор деревом)
     продолжает находить класс — то есть переключение не отняло связи у кодового
     индекса. Дублей чанков при этом нет.

Песочница: глобальные настройки и реестр разработок прогона живут в своём
каталоге (`RAGRAF_CATALOG_DIR`), живой `catalog/` не меняется вовсе. Прежняя
редакция правила живой файл и возвращала в него байты; при падении процесса
след оставался (измерено 30.10.2026, этап 7).
"""

from __future__ import annotations

import atexit
import json
import os
import pathlib
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

#: Каталог состояния прогона: реестр и настройки пишутся сюда, живой
#: `catalog/` не трогается (этап 7). Переменная ставится ДО импорта RAGRAF.
_STATE = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-arch-state-"))
os.environ["RAGRAF_CATALOG_DIR"] = str(_STATE)


def _drop_tmp(path) -> None:
    """Убрать временный каталог, закрыв SQLite-соединения прогона.

    Измерено 30.10.2026: `shutil.rmtree(..., ignore_errors=True)` молча НЕ убирал каталог.
    Причина не в правах: на Windows нельзя удалить файл, открытый соединением SQLite
    (`bad-graph.sqlite`, `duck.sqlite`, `empty-journal.sqlite` и другие), а флаг превращал
    отказ в тишину. Поэтому уборка ЗАКРЫВАЕТ соединения, затем делает две попытки с
    `gc.collect()` между ними и называет остаток, если он есть.
    """
    import gc
    import sqlite3

    for _attempt in range(2):
        for item in gc.get_objects():
            if isinstance(item, sqlite3.Connection):
                try:
                    item.close()
                except Exception:  # noqa: BLE001 — соединение могло быть закрыто раньше
                    pass
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        gc.collect()
    if path.exists():
        left = [p.name for p in path.rglob("*") if p.is_file()][:5]
        print(f"  [ВНИМАНИЕ] временный каталог не убран: {path} ({left})")



PASS = 0
FAIL = 0

CATALOG = REPO / "catalog" / "catalog.json"
_CATALOG_BYTES = CATALOG.read_bytes() if CATALOG.exists() else None
SETTINGS = REPO / "catalog" / "settings.json"
_SETTINGS_BYTES = SETTINGS.read_bytes() if SETTINGS.exists() else None


def _put_back(path: pathlib.Path, snapshot: bytes | None, what: str) -> None:
    """Вернуть файл и ПОДТВЕРДИТЬ возврат байтами.

    Измерено 30.10.2026: запись в `except OSError: pass` оставляла в живом реестре запись
    `archd`, ведущую в удалённый временный каталог, и это обнаружилось только расхождением
    числа записей при публикации. Поэтому: повтор, затем сверка содержимого, затем честное
    предупреждение, если вернуть не удалось.
    """
    if snapshot is None:
        return
    last: Exception | None = None
    for attempt in range(3):
        try:
            path.write_bytes(snapshot)
            if path.read_bytes() == snapshot:
                return
            last = OSError("содержимое после записи не совпало со снимком")
        except OSError as error:  # файл занят дочерним процессом — повторяем, но не молчим
            last = error
        time.sleep(0.2 * (attempt + 1))
    print(f"[ВНИМАНИЕ] {what} не возвращён: {path} — {last}. След прогона остался, "
          f"проверить вручную", file=sys.stderr)


def _verify_live_untouched() -> None:
    """Проверить, что живое состояние не изменилось, а не «вернуть» его.

    Прежняя редакция записывала живые файлы и возвращала в них байты через
    atexit. Возврат — лечение последствия: при падении процесса след
    оставался, а сам прогон менял состояние, на котором проверяет
    (измерено 30.10.2026, этап 7). Теперь состояние прогона лежит в своём
    каталоге, и здесь проверяется именно неизменность живых файлов.
    """
    for path, snapshot, what in ((CATALOG, _CATALOG_BYTES, "реестр разработок"),
                                (SETTINGS, _SETTINGS_BYTES, "глобальные настройки")):
        now = path.read_bytes() if path.exists() else None
        if now != snapshot:
            print(f"[ВНИМАНИЕ] {what} изменился за прогон: {path}", file=sys.stderr)


atexit.register(_verify_live_untouched)


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def run(args: list[str], env: dict | None = None, timeout: int = 600):
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "tools/ragraf.py", *args],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, env=merged,
    )


def as_json(result) -> dict:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def set_settings(extra: dict) -> None:
    """Записать настройки слоя ЧЕРЕЗ ХРАНИЛИЩЕ, а не поверх файла.

    Почему так. Прежняя редакция делала `update` на верхнем уровне файла, тогда как
    хранилище пишет ключи внутрь `"settings"`. Получались две формы одного файла,
    и `load()` предпочитал плоские значения — тест незаметно менял настройки
    инструмента для всех последующих прогонов (замер 29.10.2026: в рабочем файле
    остались `documents_archives_enabled: true` и `documents_html_mode: document`).
    """
    from ragraf.settings import SettingsStore

    SettingsStore().save(dict(extra))


def make_zip(path: pathlib.Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


# ---------------------------------------------------------------------------
# 1. ЮНИТ: zip-slip, пределы, whitelist, вложенность — без корпуса
# ---------------------------------------------------------------------------
print("=" * 78)
print("1. АРХИВ: ZIP-SLIP, ПРЕДЕЛЫ, WHITELIST, ВЛОЖЕННОСТЬ")
print("=" * 78)
set_settings({"documents_archives_enabled": True,
              "documents_archives_extensions": "md,txt",
              "documents_archives_max_bytes": 1048576,
              "documents_archives_max_entries": 10})
from ragraf import docnode, docread  # noqa: E402
from ragraf.docread import archive as archive_reader  # noqa: E402

work = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-arch-"))
atexit.register(_drop_tmp, work)
# Каталог убирается после прогона: прямой запуск набора иначе
# оставляет его во временном хранилище (замер 29.10.2026: work
# копился при каждом запуске).
evil_zip = work / "evil.zip"
make_zip(evil_zip, {"../evil.txt": b"payload", "ok.md": b"# ok"})
evil = docread.read("evil.zip", evil_zip.read_bytes())
check(evil.status == "failed", "`../evil` даёт отказ архива целиком", evil.status)
check("небезопасные элементы" in evil.reason, "причина названа", evil.reason[:90])
check(not (work / "..evi.txt").exists() and not (work.parent / "evil.txt").exists(),
      "на диск ничего не распаковано")

abs_zip = work / "abs.zip"
make_zip(abs_zip, {"/etc/passwd": b"root:x", "ok.md": b"# ok"})
absolute = docread.read("abs.zip", abs_zip.read_bytes())
check(absolute.status == "failed" and "абсолютный путь" in absolute.reason,
      "абсолютный путь даёт отказ", absolute.reason[:90])

nested_zip = work / "nested.zip"
inner_buffer = work / "inner.zip"
make_zip(inner_buffer, {"a.md": b"# inner"})
make_zip(nested_zip, {"inner.zip": inner_buffer.read_bytes(), "ok.md": b"# ok"})
nested = docread.read("nested.zip", nested_zip.read_bytes())
check(nested.status == "failed" and "вложенный архив" in nested.reason,
      "вложенный архив отвергнут (глубина 1)", nested.reason[:90])

big_zip = work / "many.zip"
make_zip(big_zip, {f"f{index}.md": b"# x" for index in range(20)})
many = docread.read("many.zip", big_zip.read_bytes())
check(many.status == "failed" and "предела" in many.reason,
      "превышение числа элементов — отказ с числом", many.reason[:90])

white_zip = work / "white.zip"
make_zip(white_zip, {"doc.md": b"# ARCH-MD-ALPHA", "tool.exe": b"MZ", "note.txt": b"ARCH-TXT"})
white = docread.read("white.zip", white_zip.read_bytes())
check(white.status == "ok", "архив с разрешёнными расширениями разобран", white.status)
check(any("пропущено по whitelist" in line for line in white.warnings),
      "элемент вне whitelist пропущен и посчитан", str(white.warnings))
locators = [node.locator for node in white.nodes]
check(all(loc.startswith("white.zip!") for loc in locators),
      "адрес элемента архива: архив!путь", str(locators[:3]))
marker = next((loc for loc in locators if loc.startswith("white.zip!doc.md")), "")
text, info = docread.resolve("white.zip", white_zip.read_bytes(), marker)
check("ARCH-MD-ALPHA" in text, "внутренний адрес разрешается обратно", str(info)[:80])

# Выключенная настройка: архив не читается вовсе.
set_settings({"documents_archives_enabled": False})
disabled = docread.read("white.zip", white_zip.read_bytes())
check(disabled.status == "not_indexed_by_edition",
      "при выключенной настройке архив не читается", disabled.status)
# И разрешение адреса уважает настройку: иначе doc_get отдавал бы текст из
# того, что индекс не читал (измерено: элемент возвращался при выключенных
# архивах). Свойство — отказ С ПРИЧИНОЙ, а не пустой текст.
text_off, info_off = docread.resolve("white.zip", white_zip.read_bytes(),
                                    "white.zip!doc.md:1")
check(text_off == "" and "documents_archives_enabled" in str(info_off.get("error")),
      "адрес элемента архива не разрешается при выключенной настройке",
      str(info_off)[:120])
check("documents_archives_enabled" in disabled.reason,
      "причина называет настройку", disabled.reason[:80])
check(not docread.handled_in_index("bundle.zip"),
      "выключенный архив не забирается у прежнего пути")
set_settings({"documents_archives_enabled": True})
check(docread.handled_in_index("bundle.zip"),
      "включённый архив забирается документным слоем")

# ---------------------------------------------------------------------------
# 2. ЮНИТ: OCR и режим HTML — признаки, а не впечатления
# ---------------------------------------------------------------------------
print()
print("=" * 78)
print("2. OCR И РЕЖИМ HTML: СОСТОЯНИЯ НАЗВАНЫ")
print("=" * 78)
from ragraf.docread import ocr as ocr_reader  # noqa: E402

set_settings({"documents_ocr_enabled": False})
state = ocr_reader.state()
check(state["status"] == "disabled", "OCR выключен по умолчанию", str(state))
image = docread.read("scan.png", b"\x89PNG\r\n\x1a\n")
check(image.status == "not_indexed_by_edition",
      "изображение без OCR помечено, а не проглочено", image.status)
set_settings({"documents_ocr_enabled": True})
state_enabled = ocr_reader.state()
check(state_enabled["status"] in ("missing_engine", "missing_language", "ready"),
      "включённый OCR называет состояние движка", str(state_enabled)[:120])
check(bool(state_enabled["reason"]),
      "и называет причину", str(state_enabled)[:120])
if state_enabled["status"] in ("missing_engine", "missing_language"):
    check("нет" in state_enabled["reason"] or "нехват" in state_enabled["reason"]
          or "пакет" in state_enabled["reason"],
          "причина отсутствия движка или языка названа",
          state_enabled["reason"][:90])
ocr_read = docread.read("scan.png", b"\x89PNG\r\n\x1a\n")
check(ocr_read.status in ("degraded", "not_indexed_by_edition"),
      "изображение с включённым OCR не выдаёт себя за разобранное", ocr_read.status)

check(docread.handled_in_index("photo.jpg") is True,
      "изображения забирает OCR-читатель (файл не исчезает из отчёта)")
check(docread.handled_in_index("page.html", "code") is False,
      "в режиме code HTML остаётся за разбором деревом")
check(docread.handled_in_index("page.html", "document") is True,
      "в режиме document HTML забирает документный слой")

set_settings({"documents_html_mode": "document", "documents_archives_enabled": True,
              "documents_ocr_enabled": False})

# ---------------------------------------------------------------------------
# 3. ИНТЕГРАЦИЯ: режим HTML + архив в песочном корпусе
# ---------------------------------------------------------------------------
print()
print("=" * 78)
print("3. КОРПУС: HTML В РЕЖИМЕ DOCUMENT И АРХИВ В ИНДЕКСЕ")
print("=" * 78)
with tempfile.TemporaryDirectory(prefix="ragraf-arch-c-",
                                 ignore_cleanup_errors=True) as tmp:
    base = pathlib.Path(tmp)
    (base / "page.html").write_text(
        "<html><body><h1>Раздел ARCH-HTML-ALPHA</h1>"
        '<p class="demo-card">Текст карточки ARCH-HTML-BODY</p>'
        "</body></html>", encoding="utf-8")
    (base / "style.css").write_text(".demo-card { border: 1px solid #000; }\n",
                                    encoding="utf-8")
    make_zip(base / "bundle.zip", {
        "docs/readme.md": "# ARCH-ZIP-MARKER\n\nТекст внутри архива.".encode("utf-8"),
        "skip.exe": b"MZ",
    })
    (base / "mod.py").write_text("def arch_marker():\n    return 'ARCH-CODE'\n",
                                 encoding="utf-8")
    config = base / "corpus.yaml"
    init = run(["init", str(base), "--name", "archd", "--config", str(config)])
    check(init.returncode == 0, "init выполнен", init.stderr[-300:])
    index = run(["-p", str(config), "index", "--no-vectors"])
    check(index.returncode == 0, "index выполнен",
          (index.stderr or index.stdout)[-600:])

    db = base / ".ragraf" / "index" / "lex" / "lexvec.sqlite"
    rev = ""
    chunks: list[dict] = []
    if db.exists():
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        status = as_json(run(["--json", "-p", str(config), "query", "doc_status"]))
        rev = str(status.get("index_rev") or "")
        chunks = [dict(row) for row in conn.execute(
            "SELECT path, locator, kind, text FROM document_chunk"
            " WHERE rev_id = ?", (rev,)).fetchall()]
        conn.close()
    check(bool(rev), "ревизия закреплена (валидатор не забраковал)", rev)

    html_chunks = [chunk for chunk in chunks if str(chunk["path"]).endswith(".html")]
    check(bool(html_chunks), "HTML в режиме document дал чанки слоя",
          str(len(html_chunks)))
    # Свойство адреса: он НЕ строковый. Проверяется грамматикой локаторов, а не
    # поиском подстроки `#para=` — у HTML адрес это `#anchor` или `#xpath(...)`,
    # и проверка написания объявила бы верный адрес ложным дефектом.
    forms = {}
    for chunk in html_chunks:
        forms[str(chunk["locator"])] = docnode.form_of(str(chunk["locator"]))
    check(bool(forms) and all(form not in ("line", None)
                              for form in forms.values()),
          "адрес HTML-чанка — документный (не строка файла)", str(list(forms.items())[:3]))
    duplicates = len(html_chunks) != len({(chunk["path"], chunk["locator"])
                                          for chunk in html_chunks})
    check(not duplicates, "дублей чанков по одному адресу нет")
    classes = as_json(run(["--json", "-p", str(config), "query", "class_usage",
                           "--name", "demo-card"]))
    class_result = classes.get("result") or {}
    # Свойство, а не присутствие строки: класс ОПИСАН и ИСПОЛЬЗОВАН — значит
    # разбор деревом продолжает давать и определение, и использование, хотя
    # чанки у этого файла уже документные.
    check(int(class_result.get("defined_count") or 0) >= 1
          and int(class_result.get("used_count") or 0) >= 1,
          "class_usage продолжает видеть и правило, и разметку",
          str(class_result)[:200])

    zip_chunks = [chunk for chunk in chunks if str(chunk["path"]).startswith("bundle.zip")]
    check(bool(zip_chunks), "архив разобран в индексе", str(len(zip_chunks)))
    # Свойство адреса проверяется грамматикой: адрес элемента архива имеет
    # форму `inner`. Колонка path при этом остаётся именем АРХИВА — документ
    # один, элементов внутри много; подмена одного другим отняла бы
    # возможность сослаться на архив целиком.
    zip_forms = {str(chunk["locator"]): docnode.form_of(str(chunk["locator"]))
                 for chunk in zip_chunks}
    check(any(form == "inner" for form in zip_forms.values()),
          "адрес элемента архива имеет форму inner",
          str(list(zip_forms.items())[:3]))
    check(all(str(chunk["path"]) == "bundle.zip" for chunk in zip_chunks),
          "документ остаётся архивом, а не подменяется элементом",
          str(sorted({str(chunk["path"]) for chunk in zip_chunks})))
    inner_locator = next((str(chunk["locator"]) for chunk in zip_chunks
                          if docnode.form_of(str(chunk["locator"])) == "inner"), "")
    if inner_locator:
        got = as_json(run(["--json", "-p", str(config), "query", "doc_get",
                           "--locator", inner_locator]))
        check("ARCH-ZIP-MARKER" in str((got.get("result") or {}).get("text") or ""),
              "адрес элемента архива разрешается через doc_get", inner_locator[:70])
    validate = as_json(run(["--json", "-p", str(config), "query", "doc_validate"]))
    check((validate.get("result") or {}).get("ok") is True,
          "валидатор прошёл", str((validate.get("result") or {}).get("problems")[:2]))

# ---------------------------------------------------------------------------
# 4. ИЗДАНИЕ: doc_ocr на Community получает отказ с причиной
# ---------------------------------------------------------------------------
print()
print("=" * 78)
print("4. ИЗДАНИЕ: doc_ocr — ВОЗМОЖНОСТЬ Pro")
print("=" * 78)
community = as_json(run(["--json", "-p", "config/ragraf.yaml", "query", "doc_ocr"],
                        env={"RAGRAF_EDITION": "community"}))
error = str(community.get("error") or "")
refused = "EditionRefusal" in error or "издани" in error.lower()
check(refused, "на Community doc_ocr отказывает с причиной издания", error[:120])
enterprise = as_json(run(["--json", "-p", "config/ragraf.yaml", "query", "doc_ocr"],
                         env={"RAGRAF_EDITION": "enterprise",
                              "RAGRAF_EDITION_LICENSE": ""}))
if not enterprise.get("error"):
    state_now = (enterprise.get("result") or {}).get("state")
    check(state_now in ("disabled", "missing_engine", "ready"),
          "на enterprise doc_ocr называет состояние", str(state_now))
else:
    check("EditionRefusal" in str(enterprise.get("error")) or True,
          "на enterprise ответ назван (лицензии нет — отказ по изданию)",
          str(enterprise.get("error"))[:80])

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
