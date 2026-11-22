"""Замечания внешнего разбора (проект «Михалыч», 22.11.2026): проверка по свойству.

Каждое утверждение клиента воспроизводилось на его проекте; здесь — на своём
собственном маленьком корпусе, чтобы дефект нельзя было вернуть правкой.

Что проверяется:
  1. стрелочные функции и псевдонимы типов TypeScript попадают в индекс,
     а объявление переменной без функции — нет;
  2. вызов из другого файла связывается с определением по строке импорта;
  3. `dead_code` не объявляет мёртвым файл, который импортируют;
  4. `.astro` индексируется: символы из frontmatter и `<script>`, номера строк
     совпадают с исходником;
  5. `index_status` различает «библиотека есть» и «векторы в индексе есть»;
  6. `db.enabled: false` в конфиге корпуса действительно выключает слой баз;
  7. журнал расхождений пишет вид изменения, а не всегда «modify»;
  8. `neighbors` не падает, когда зарегистрирован второй корпус;
  9. повреждённый реестр — громкая ошибка, а не пустой список корпусов.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

PASS = 0
FAIL = 0
PY = sys.executable
TOOL = str(REPO / "tools" / "ragraf.py")


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def run(args: list[str], env: dict | None = None) -> tuple[int, str]:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    completed = subprocess.run([PY, TOOL, *args], cwd=str(REPO), capture_output=True,
                               text=True, encoding="utf-8", errors="replace", env=merged)
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def envelope(text: str) -> dict:
    """Разобрать JSON-конверт из текстового ответа CLI."""
    start = text.find("{")
    while start != -1:
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
    return {}


WORK = pathlib.Path(tempfile.mkdtemp(prefix="ragraf-client-"))
STATE = WORK / "state"
CORPUS = WORK / "site"
OTHER = WORK / "other"
ENV = {"RAGRAF_CATALOG_DIR": str(STATE)}

try:
    print("=" * 78)
    print("ПОСТРОЕНИЕ ПРОБНОГО КОРПУСА (TypeScript + Astro)")
    print("=" * 78)
    CORPUS.mkdir(parents=True)
    (CORPUS / "lib.ts").write_text(
        "export const greet = (name: string): string => `hi ${name}`;\n"
        "export const sum = (a: number, b: number) => a + b;\n"
        "export function legacy(n: number) { return n * 2; }\n"
        "export const port = 8080;\n"
        "export type Id = string | number;\n",
        encoding="utf-8")
    (CORPUS / "app.ts").write_text(
        'import { greet, sum } from "./lib";\n'
        'export const run = () => greet("x") + sum(1, 2);\n',
        encoding="utf-8")
    (CORPUS / "index.ts").write_text(
        'export { greet } from "./lib";\n', encoding="utf-8")
    (CORPUS / "page.astro").write_text(
        "---\n"
        'import { legacy } from "./lib";\n'
        'const title = "Hello";\n'
        "export const page = () => legacy(21) + title;\n"
        "---\n"
        "<html><body><h1>{title}</h1>\n"
        "<script>\n"
        '  import { sum } from "./lib";\n'
        "  export const total = () => sum(1, 2);\n"
        "</script>\n"
        "</body></html>\n",
        encoding="utf-8")

    code, out = run(["init", str(CORPUS)], ENV)
    check(code == 0, "корпус создан", out.strip()[-200:])
    config = pathlib.Path(out.strip().splitlines()[-2].split("-p ")[-1].strip()) \
        if "-p " in out else None
    configs = sorted((REPO / "config").glob("site.yaml"))
    config = configs[0] if configs else None
    check(config is not None, "конфиг корпуса найден", str(config))

    # `init` пишет шаблоны по найденным языкам; `.astro` и `.ts` должны быть в них.
    text = config.read_text(encoding="utf-8")
    check('"**/*.astro"' in text, "init включил шаблон .astro в include",
          "в конфиге нет **/*.astro")
    check("astro" in text.split("languages:")[1].split("include:")[0],
          "init назвал язык astro в списке языков")
    check("db:" in text, "раздел db присутствует в конфиге")
    code, out = run(["-p", str(config), "index", "--no-vectors"], ENV)
    check(code == 0, "корпус проиндексирован", out[-300:])

    print()
    print("=" * 78)
    print("1. СИМВОЛЫ TYPESCRIPT: СТРЕЛОЧНЫЕ ФУНКЦИИ И ПСЕВДОНИМЫ")
    print("=" * 78)
    code, out = run(["--json", "-p", str(config), "query", "symbol", "--name", "greet"], ENV)
    payload = envelope(out)
    names = [row.get("name") for row in (payload.get("result") or [])]
    check("greet" in names, "символ greet найден (стрелочная функция)", str(names))

    code, out = run(["--json", "-p", str(config), "query", "symbol", "--name", "port"], ENV)
    payload = envelope(out)
    kinds = [row.get("kind") for row in (payload.get("result") or [])]
    check("function" not in kinds,
          "объявление переменной без функции функцией не считается", str(kinds))

    code, out = run(["--json", "-p", str(config), "query", "symbol", "--name", "Id"], ENV)
    payload = envelope(out)
    kinds = [row.get("kind") for row in (payload.get("result") or [])]
    check("type_alias" in kinds, "псевдоним типа распознан", str(kinds))

    print()
    print("=" * 78)
    print("2. ВЫЗОВ ИЗ ДРУГОГО ФАЙЛА СВЯЗАН С ОПРЕДЕЛЕНИЕМ")
    print("=" * 78)
    code, out = run(["--json", "-p", str(config), "query", "callers", "--name", "greet"], ENV)
    payload = envelope(out)
    callers = payload.get("result") or {}
    rows = callers.get("items") or []
    check(bool(rows), "у greet есть вызывающий", json.dumps(callers, ensure_ascii=False)[:200])
    kinds = {str(row.get("evidence_kind") or "") for row in rows}
    check("import" in kinds, "связь получена из строки импорта", str(kinds))
    addresses = [str(row.get("evidence") or "") for row in rows]
    check(all(a.split(":")[-1].isdigit() for a in addresses if a),
          "у связи есть адрес path:line, а не path:None", str(addresses))

    print()
    print("=" * 78)
    print("3. DEAD_CODE НЕ ПРИГОВАРИВАЕТ ИМПОРТИРУЕМЫЙ ФАЙЛ")
    print("=" * 78)
    code, out = run(["--json", "-p", str(config), "query", "dead_code"], ENV)
    payload = envelope(out)
    result = payload.get("result") or {}
    dead_paths = {str(item.get("path")) for item in result.get("files") or []}
    check("lib.ts" not in dead_paths,
          "lib.ts (его импортируют) не назван мёртвым", str(sorted(dead_paths)))
    check("unproven" in result, "ответ называет недоказанные случаи отдельно")

    print()
    print("=" * 78)
    print("4. ASTRO: СИМВОЛЫ И НОМЕРА СТРОК")
    print("=" * 78)
    code, out = run(["--json", "-p", str(config), "query", "symbol", "--name", "page"], ENV)
    payload = envelope(out)
    rows = [row for row in (payload.get("result") or [])
            if str(row.get("path", "")).endswith("page.astro")]
    check(bool(rows), "символ из frontmatter Astro найден", json.dumps(payload)[:200])
    if rows:
        line = rows[0].get("start_line") or rows[0].get("lines") or 0
        check(int(line or 0) == 4, "номер строки совпадает с исходником",
              f"получено {line}, ожидалось 4")
    code, out = run(["--json", "-p", str(config), "query", "symbol", "--name", "total"], ENV)
    payload = envelope(out)
    rows = [row for row in (payload.get("result") or [])
            if str(row.get("path", "")).endswith("page.astro")]
    check(bool(rows), "символ из <script> Astro найден")

    print()
    print("=" * 78)
    print("5. INDEX_STATUS: БИБЛИОТЕКА И ВЕКТОРЫ — РАЗНЫЕ ФАКТЫ")
    print("=" * 78)
    code, out = run(["--json", "-p", str(config), "query", "index_status"], ENV)
    payload = envelope(out)
    result = payload.get("result") or {}
    check("vector_library" in result and "vectors_indexed" in result,
          "поля разделены", str(sorted(result))[:200])
    check(result.get("vectors_indexed") == 0,
          "векторов в индексе нет (собирали с --no-vectors)",
          str(result.get("vectors_indexed")))
    check(result.get("vector_available") is False,
          "vector_available не обещает недоступное", str(result.get("vector_available")))
    check(bool(result.get("vector_note")),
          "причина названа текстом", str(result.get("vector_note"))[:80])

    print()
    print("=" * 78)
    print("6. DB.ENABLED: НАСТРОЙКА КОРПУСА ВЫКЛЮЧАЕТ СЛОЙ БАЗ")
    print("=" * 78)
    code, out = run(["--json", "-p", str(config), "query", "db_schema"], ENV)
    payload = envelope(out)
    check("db.enabled" in str(payload.get("error") or ""),
          "операция отказала с причиной из конфига", str(payload.get("error"))[:120])

    print()
    print("=" * 78)
    print("7. ЖУРНАЛ РАСХОЖДЕНИЙ ПИШЕТ ВИД ИЗМЕНЕНИЯ")
    print("=" * 78)
    (CORPUS / "index.ts").unlink()
    # Журнал расхождений наполняет отдельная операция `drift` (cli.cmd_drift),
    # и вызывать её надо ПОКА файл ещё в индексе: после переиндексации
    # удалять нечего, и проверка ничего не осмотрит.
    code, out = run(["-p", str(config), "drift"], ENV)
    check(code == 0, "операция drift выполнилась", out[-200:])
    code, out = run(["-p", str(config), "index", "--no-vectors"], ENV)
    check(code == 0, "переиндексация после удаления файла", out[-200:])
    conn = sqlite3.connect(f"file:{CORPUS / '.ragraf/control.sqlite'}?mode=ro", uri=True)
    kinds = [row[0] for row in conn.execute(
        "SELECT kind FROM drift_entry ORDER BY id DESC LIMIT 5")]
    conn.close()
    check("delete" in kinds, "удаление записано как delete, а не modify", str(kinds))

    print()
    print("=" * 78)
    print("8. NEIGHBORS: ВТОРОЙ КОРПУС НЕ РОНЯЕТ ОПЕРАЦИЮ")
    print("=" * 78)
    OTHER.mkdir(parents=True)
    for name, body in (("a.ts", "export const one = () => 1;\n"),
                       ("b.ts", "export const two = () => 2;\n"),
                       ("c.ts", "export const three = () => 3;\n")):
        (OTHER / name).write_text(body, encoding="utf-8")
    code, out = run(["init", str(OTHER)], ENV)
    other_configs = sorted((REPO / "config").glob("other.yaml"))
    if code == 0 and other_configs:
        run(["-p", str(other_configs[0]), "register"], ENV)
        run(["-p", str(other_configs[0]), "index", "--no-vectors"], ENV)
        code, out = run(["--json", "-p", str(config), "query", "neighbors", "--name", "greet"], ENV)
        check("UnboundLocalError" not in out,
              "операция не падает с UnboundLocalError", out[-200:])
        check(code == 0, "операция завершилась кодом 0", out[-200:])
    else:
        check(False, "второй корпус создан", out[-200:])

    print()
    print("=" * 78)
    print("9. ПОВРЕЖДЁННЫЙ РЕЕСТР — ОШИБКА, А НЕ ПУСТОЙ СПИСОК")
    print("=" * 78)
    STATE.mkdir(parents=True, exist_ok=True)
    broken = STATE / "catalog.json"
    broken.write_text(json.dumps({"version": 1, "entries": ["a", "b"]}),
                      encoding="utf-8")
    code, out = run(["catalog"], ENV)
    check(code == 2, "команда вернула код 2", f"код {code}")
    check("повреждён" in out, "причина названа", out.strip()[:200])
    check((STATE / "catalog.json").exists(),
          "файл реестра не перезаписан пустым")

finally:
    print()
    print("=" * 78)
    for config in list((REPO / "config").glob("site.yaml")) + \
            list((REPO / "config").glob("other.yaml")):
        config.unlink(missing_ok=True)
    shutil.rmtree(WORK, ignore_errors=True)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    raise SystemExit(0 if FAIL == 0 else 1)
