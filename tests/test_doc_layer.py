"""Проверка документного слоя (этап «документы», фаза A).

Проверяется ПОВЕДЕНИЕ, а не наличие файлов. Свойства:

  1. у каждого чанка документа есть адрес, и он валиден по грамматике;
  2. адрес разрешается обратно: `doc_get` возвращает текст, в котором есть
     то, что искали (иначе адрес — украшение);
  3. длинный раздел режется с сохранением `heading_chain` и связи `part_of`;
  4. неподдержанный формат помечен `not_indexed_by_edition` с причиной и не
     исчез из отчёта;
  5. валидатор НЕ пустой: подсаженный чанк с битым адресом он ловит, а после
     удаления строки снова пропускает — иначе «ok» ничего не значит;
  6. `.html` не забран у разбора деревом: чанков документного слоя по html нет,
     потому что этот путь питает `class_usage` и `routes`;
  7. кодовый индекс не регрессировал: поиск по коду находит маркер как прежде.

Песочница, а не живой корпус: каталог во временном хранилище, живой индекс не
меняется ни на байт.
"""

from __future__ import annotations

import atexit
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

PASS = 0
FAIL = 0

#: Реестр разработок на время прогона восстанавливается.
#:
#: Измерено 28.10.2026: `index` песочного корпуса пишет его в реестр
#: (`catalog/catalog.json`), и после удаления каталога запись остаётся. Три таких
#: прогона подняли реестр с 43 до 45, строка реестра в README перестала совпадать
#: с реестром, и гейт публикации отказал. Проверка не должна менять состояние
#: рабочего дерева — поэтому реестр снимается и возвращается.
CATALOG = REPO / "catalog" / "catalog.json"
_CATALOG_BYTES = CATALOG.read_bytes() if CATALOG.exists() else None


def _restore_registry() -> None:
    if _CATALOG_BYTES is None:
        return
    try:
        CATALOG.write_bytes(_CATALOG_BYTES)
    except OSError:
        pass


atexit.register(_restore_registry)


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "tools/ragraf.py", *args],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def as_json(result: subprocess.CompletedProcess) -> dict:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


LONG_LINE = ("Длинный абзац нормативного текста. " * 12).strip()
GUIDE = f"""# Руководство по доставке

Вводный абзац с маркером MARKER-MD-ALPHA.

## Раздел первый

{chr(10).join([LONG_LINE + f" Часть {n}." for n in range(1, 40)])}

## Раздел второй

Абзац с маркером MARKER-MD-SECOND.
"""

NOTES = """Строка без заголовка номер один с маркером MARKER-TXT-BETA.
Строка без заголовка номер два, продолжение того же абзаца.

Второй абзац файла без единого заголовка.
"""

LOG = """2026-10-28 10:00:01 INFO старт обработки MARKER-LOG-GAMMA
2026-10-28 10:00:02 INFO продолжение записи без новой даты
2026-10-28 10:00:03 ERROR отказ: пример для проверки
"""

HTML = """<html><head><title>Страница</title></head><body>
<nav><a href="/">Меню</a><a href="/x">Пункт</a></nav>
<script>var x = 1;</script>
<article><h1>Заголовок статьи</h1>
<p>Текст статьи с маркером MARKER-HTML-DELTA.</p>
<table><tr><th>Колонка</th></tr><tr><td>Значение</td></tr></table>
</article>
<footer>Подвал сайта</footer>
</body></html>
"""

CODE = "def doc_layer_marker():\n    return 'MARKER-CODE-EPSILON'\n"

with tempfile.TemporaryDirectory(prefix="ragraf-doclayer-",
                                 ignore_cleanup_errors=True) as tmp:
    base = pathlib.Path(tmp)
    (base / "guide.md").write_text(GUIDE, encoding="utf-8")
    (base / "notes.txt").write_text(NOTES, encoding="utf-8")
    (base / "app.log").write_text(LOG, encoding="utf-8")
    (base / "page.html").write_text(HTML, encoding="utf-8")
    (base / "mod.py").write_text(CODE, encoding="utf-8")
    # Формат, который документный слой знает, но в этой фазе не разбирает.
    (base / "report.docx").write_bytes(b"PK\x03\x04not-a-real-docx")
    config = base / "corpus.yaml"

    print("=" * 78)
    print("1. INIT И INDEX")
    print("=" * 78)
    init = run(["init", str(base), "--name", "doclayer", "--config", str(config)])
    check(init.returncode == 0, "init выполнен", init.stderr[-300:])
    if not config.exists():
        raise SystemExit(1)
    index = run(["-p", str(config), "index", "--no-vectors"])
    check(index.returncode == 0, "index выполнен",
          (index.stderr or index.stdout)[-500:])

    print()
    print("=" * 78)
    print("2. DOC_STATUS: СТАТУСЫ И ПРИЧИНЫ")
    print("=" * 78)
    status = as_json(run(["--json", "-p", str(config), "query", "doc_status"]))
    result = status.get("result") or {}
    check(result.get("documents", 0) >= 3,
          "документы попали в слой", str(result.get("documents")))
    by_status = result.get("by_status") or {}
    check(by_status.get("ok", 0) >= 3, "статус ok у разобранных", str(by_status))
    # Формат, который слой знает, но в этой фазе не разбирает, обязан отвечать
    # `not_indexed_by_edition` С ПРИЧИНОЙ, а не «файла нет». Проверяется на
    # уровне читателя: при индексации такие файлы ещё идут прежним путём
    # (`documents.extract`), и объявлять их «не проиндексированными» в отчёте
    # было бы неправдой — этот путь работает и находит их поиском.
    from ragraf import docread  # noqa: E402
    # Формат, чей читатель ещё не написан, обязан отвечать `not_indexed_by_edition`
    # с причиной. DOCX сюда больше не годится: с фазы B он разбирается
    # (`docread.supported("report.docx")` истинно), и проверять на нём «не умеем»
    # значило бы проверять ложное утверждение.
    check(docread.supported("report.docx"), "docx разбирается слоем (фаза B)")
    # CSV с фазы C разбирается слоем; «не умеем» проверяется на формате, который
    # действительно ещё не разобран (архивы — фаза D).
    check(docread.supported("table.csv"), "csv разбирается слоем (фаза C)")
    planned = docread.read("bundle.zip", b"PK\x03\x04")
    check(planned.status == "not_indexed_by_edition",
          "zip: статус not_indexed_by_edition на уровне читателя", planned.status)
    check(bool(planned.reason.strip()), "причина названа", planned.reason)
    unknown = docread.read("notes.pages", b"whatever")
    check(unknown.status == "not_indexed_by_edition",
          "pages: закрытый формат назван, а не проглочен", unknown.status)
    forms = result.get("locator_forms") or []
    check("line" in forms, "в ревизии есть адреса-строки", str(forms))

    print()
    print("=" * 78)
    print("3. DOC_VALIDATE: АДРЕС У КАЖДОГО ЧАНКА")
    print("=" * 78)
    verdict = as_json(run(["--json", "-p", str(config), "query", "doc_validate"]))
    data = verdict.get("result") or {}
    problems = data.get("problems") or []
    check(data.get("ok") is True, "валидатор прошёл", str(problems[:2]))
    check(not problems, "нарушений нет", str(problems[:2]))
    check(result.get("chunks", 0) > 0, "чанки есть", str(result.get("chunks")))

    print()
    print("=" * 78)
    print("4. DOC_SEARCH И DOC_GET: АДРЕС РАЗРЕШАЕТСЯ")
    print("=" * 78)
    found = as_json(run(["--json", "-p", str(config), "query", "doc_search",
                         "MARKER-MD-ALPHA", "--limit", "5"]))
    hits = (found.get("result") or {}).get("hits") or []
    check(bool(hits), "поиск по документу нашёл маркер", str(found)[:200])
    locator = str(hits[0].get("locator")) if hits else ""
    check(locator.startswith("guide.md:"), "адрес указывает в файл и строки", locator)
    check(str((hits[0].get("provenance") or {}).get("reader")) == "text",
          "провенанс называет читателя",
          str(hits[0].get("provenance")))
    got = as_json(run(["--json", "-p", str(config), "query", "doc_get",
                       "--locator", locator]))
    text = str((got.get("result") or {}).get("text") or "")
    check(got.get("found") is True and "MARKER-MD-ALPHA" in text,
          "doc_get вернул текст по адресу", text[:120])
    check((got.get("result") or {}).get("source") == "index",
          "источник назван: index", str((got.get("result") or {}).get("source")))

    print()
    print("=" * 78)
    print("5. БЮДЖЕТЫ: ДЛИННЫЙ РАЗДЕЛ РЕЖЕТСЯ С СОХРАНЕНИЕМ СТРУКТУРЫ")
    print("=" * 78)
    check(int(result.get("tokens_max") or 0) <= 1200,
          "ни один чанк не выше максимума бюджета", str(result.get("tokens_max")))
    # Хранилище выбирается ПО ИМЕНИ: в каталоге несколько баз (control, graph,
    # lexvec), и `glob("*.sqlite")` отдавал control.sqlite, где документных таблиц нет.
    db = (base / ".ragraf" / "index" / "lex" / "lexvec.sqlite")
    if not db.exists():
        db = next(iter(base.glob(".ragraf/**/lexvec.sqlite")), None)
    check(db is not None, "хранилище найдено", str(db))
    parts = []
    if db is not None:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rev = str(status.get("index_rev") or "")
        parts = [dict(row) for row in conn.execute(
            "SELECT locator, part_of, structure_json, tokens FROM document_chunk"
            " WHERE rev_id = ? AND part_of <> ''", (rev,)).fetchall()]
        conn.close()
    check(bool(parts), "есть части разрезанного узла", str(len(parts)))
    if parts:
        structure = json.loads(parts[0]["structure_json"] or "{}")
        check(bool(structure.get("heading_chain")),
              "у части сохранена цепочка заголовков", str(structure)[:160])
        check(structure.get("part_of") == parts[0]["part_of"],
              "связь части с узлом записана", str(structure.get("part_of")))
        check(structure.get("part_of", "").startswith("guide.md:"),
              "связь указывает на адрес узла", str(structure.get("part_of")))

    print()
    print("=" * 78)
    print("6. ВАЛИДАТОР НЕ ПУСТОЙ: БИТЫЙ АДРЕС ЛОВИТСЯ")
    print("=" * 78)
    if db is not None:
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT OR REPLACE INTO document_chunk(rev_id, chunk_id, doc_id, path,"
            " locator, kind, text, tokens, part_of, structure_json,"
            " provenance_json, extraction_status, vector_chars)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rev, "sandbox-bad-chunk", "sandbox-doc", "guide.md", "guide.md",
             "paragraph", "текст без адреса", 4, "", "{}", "{}", "ok", 4),
        )
        conn.commit()
        conn.close()
        broken = as_json(run(["--json", "-p", str(config), "query", "doc_validate"]))
        bad = (broken.get("result") or {}).get("problems") or []
        check(any(int(item.get("check", 0)) == 1 for item in bad),
              "битый адрес поймана проверкой 1", str(bad[:1]))
        check((broken.get("result") or {}).get("ok") is False,
              "вердикт стал отрицательным", str((broken.get("result") or {}).get("ok")))
        conn = sqlite3.connect(str(db))
        conn.execute("DELETE FROM document_chunk WHERE chunk_id = 'sandbox-bad-chunk'")
        conn.commit()
        conn.close()
        clean = as_json(run(["--json", "-p", str(config), "query", "doc_validate"]))
        check((clean.get("result") or {}).get("ok") is True,
              "после удаления строки вердикт снова положительный",
              str((clean.get("result") or {}).get("problems")))
    else:
        check(False, "проверка валидатора пропущена: нет хранилища")

    print()
    print("=" * 78)
    print("7. HTML ОСТАЛСЯ ЗА РАЗБОРОМ ДЕРЕВОМ")
    print("=" * 78)
    html_rows = []
    if db is not None:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        html_rows = [dict(row) for row in conn.execute(
            "SELECT path FROM document_chunk WHERE rev_id = ? AND path LIKE '%.html'",
            (rev,)).fetchall()]
        conn.close()
    check(not html_rows, "чанков документного слоя по html нет", str(html_rows[:3]))
    from ragraf import docread  # noqa: E402 — проверка признака маршрутизации
    check(docread.supported("page.html") and not docread.handled_in_index("page.html"),
          "html умеем разобрать, но не забираем у дерева")
    check(docread.handled_in_index("guide.md"), "markdown забран документным слоем")

    print()
    print("=" * 78)
    print("8. КОДОВЫЙ ИНДЕКС НЕ РЕГРЕССИРОВАЛ")
    print("=" * 78)
    code_hit = as_json(run(["--json", "-p", str(config), "query", "search",
                            "doc_layer_marker", "--limit", "5"]))
    code_hits = (code_hit.get("result") or [])
    check(any("mod.py" in str(item.get("path")) for item in code_hits),
          "поиск по коду находит символ", str([i.get("path") for i in code_hits][:4]))
    check(status.get("capability") is not None,
          "оболочка ответа сохранена (capability)",
          str(status.get("capability")))

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
