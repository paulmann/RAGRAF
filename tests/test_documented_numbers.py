"""ПРОМТ-И14 и ПРОМТ-И33: три исхода утверждения «сколько и когда».

Измеренный дефект (25.10.2026). Документ текущего состояния называет размеры
собственного индекса, и до этапа И14 любое расхождение с активной ревизией
объявлялось одной ошибкой. Следствие: живой документ, отставший на один
пересбор, валил прогон точно так же, как выдуманные числа, — а различие
«устаревший замер» и «состояние, которого не было» не проверялось вовсе.
Лечение мягким предупреждением без второй половины хуже дефекта: предупреждение
превращается в вечное оправдание, если публикация его не замечает.

Измеренный дефект (28.10.2026, ПРОМТ-И33). Второе утверждение того же класса —
строка реестра: «по реестру на ДД.ММ.ГГГГ: N корпусов, M проиндексированы» в
README и пяти переводах. Её держали ТОЧНЫМ равенством с датой обновления
`catalog/catalog.json`, а эта дата двигается при индексации ЛЮБОГО корпуса.
Живой документ с вчерашней датой валил прогон, и «починили» это переписыванием
даты руками в шести файлах — до следующего пересбора.

Ещё один измеренный дефект, найденный при подготовке этого файла: сама проверка
не работала. Образец размеров индекса был записан по-русски, а README с 27.10.2026
написан по-английски, поэтому прогон останавливался на «подменять нечего» — то
есть свойства трёх исходов не проверялось вовсе, а гейт при этом был зелёным по
другим проверкам. Поэтому образец берётся ИЗ ТОГО ЖЕ ИСТОЧНИКА, что у
потребителей (чекер и ворота публикации), и сверяется с фактической формой.

Проверяется СВОЙСТВО в трёх состояниях и на ДВУХ потребителях, для ДВУХ
утверждений:

  1) числа/дата активного состояния      -> чекер молчит, ворота пропускают;
  2) числа/дата ПРОШЛОГО состояния       -> чекер предупреждает (не ошибка),
                                            ворота публикации ОТКАЗЫВАЮТ;
  3) числа/дата, которых не было никогда -> чекер ошибается, ворота отказывают.

Плюс генератор строки реестра: он обязан привести все шесть документов к
состоянию реестра, быть идемпотентным и не трогать файлы, которых нет в его
списке.

Песочница, а не живое дерево: копия тех файлов, которые читают проверки, в
каталоге временного хранения. Живой README не меняется ни на байт, индекс не
пересобирается. Приманки в песочнице: каталог с посторонним README рядом
(проверка обязана смотреть в свой корень) и файл с такой же строкой реестра вне
списка шести (генератор обязан его не трогать).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

import registry_line  # noqa: E402 — инструмент строки реестра, его шаблоны и есть контракт

PASS = 0
FAIL = 0

# Форма записи размеров индекса — ТА ЖЕ, что у потребителей: чекер
# (tools/Invoke-ProjectDoctrine.ps1, Test-DocumentedNumbers) и ворота
# публикации (tools/release/gate_release.py) ищут одно и то же предложение.
NUMBERS_RE = re.compile(
    r"the RAGRAF index itself — ([\d ]+?)\s*files,\s*([\d ]+?)\s*nodes\s+and\s+([\d ]+?)\s*edges"
)

REGISTRY_DOCS = ("README.md",) + tuple(doc.path for doc in registry_line.DOCUMENTS
                                       if doc.path != "README.md")

# Что читают проверки. Список явный: если проверка начнёт читать новый файл,
# песочница перестанет её удовлетворять и тест это покажет.
SANDBOX_FILES = (
    "README.md",
    "AGENTS.md",
    "docs/AGENTS-context.md",
    "docs/03_implementation/INTEGRATION.md",
    "docs/03_implementation/HARNESS_INTEGRATION.md",
    "docs/03_implementation/AUTOCLAW_INTEGRATION.md",
    "docs/03_implementation/AGENTS_SETUP_AND_CAPABILITIES.md",
    "docs/05_article/RAGRAF_ARTICLE.md",
    # Объявление состояния переводов: без него шаг ворот публикации «числа
    # переводов» отказывает сам, и до проверки строки реестра дело не доходит.
    # Измерено 28.10.2026: песочница без этого файла давала ложный провал
    # «ворота не назвали строку реестра», хотя отказ был не по её адресу.
    "docs/03_implementation/translations.json",
    # Таблица источников: генератор пересчитывает в ней хэши переводов, потому
    # что правка строки реестра меняет их sha256. Без файла в песочнице прогон
    # `--write` отказывает — и это было бы отказом песочницы, а не продукта.
    "docs/03_implementation/RELEASE-SOURCES.md",
    "community/README.ru.md",
    "community/README.de.md",
    "community/README.es.md",
    "community/README.hi.md",
    "community/README.zh.md",
    "catalog/catalog.json",
    "config/ragraf.yaml",
    "tools/Invoke-ProjectDoctrine.ps1",
    "tools/rev_snapshot.py",
    "tools/registry_line.py",
    "tools/check_translations.py",
    "tools/release/manifest.toml",
    "tools/release/gate_release.py",
    "tools/release/relcommon.py",
)

#: Файлы, которых может не быть в живом дереве: журнал прежних состояний
#: реестра появляется при первой смене состава.
SANDBOX_OPTIONAL = ("catalog/registry_history.jsonl",)


def check(ok: bool, message: str, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok   {message}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL {message}" + (f"  [{detail}]" if detail else ""))


def history_states() -> list[tuple[str, int, int, int, str]]:
    """Состояния корпуса из rev_history — только чтение."""
    from ragraf import store
    from ragraf.config import load

    cfg = load(REPO / "config" / "ragraf.yaml")
    conn = store.connect(cfg.control_db, readonly=True)
    try:
        rows = conn.execute(
            """SELECT rev_id, files, nodes, edges, created_at
               FROM rev_history
               WHERE project = ? AND files IS NOT NULL
                 AND state IN ('ready', 'rotated')
               ORDER BY rev_id DESC""",
            (cfg.project,),
        ).fetchall()
    finally:
        conn.close()
    return [(str(r["rev_id"]), int(r["files"]), int(r["nodes"]), int(r["edges"]),
             str(r["created_at"])) for r in rows]


def replace_numbers(text: str, files: int, nodes: int, edges: int) -> str:
    """Подменить три числа в предложении о размерах индекса, сохранив фразу."""
    match = NUMBERS_RE.search(text)
    if match is None:
        raise SystemExit("ОТКАЗ: README не называет размеры индекса — подменять нечего")
    pieces: list[str] = []
    # Счёт от НАЧАЛА текста: начав с позиции совпадения, функция теряла всё,
    # что стоит перед ним, и песочница получала обрывок документа.
    last = 0
    for group, value in ((1, files), (2, nodes), (3, edges)):
        start, end = match.span(group)
        pieces.append(text[last:start])
        pieces.append(f"{value:,}".replace(",", " "))
        last = end
    pieces.append(text[last:])
    return "".join(pieces)


def replace_registry(text: str, doc: registry_line.Document,
                     date_iso: str, corpora: int, indexed: int) -> str:
    """Подменить три заменяемых куска строки реестра, сохранив формулировку."""
    found = list(doc.pattern.finditer(text))
    if len(found) != 1:
        raise SystemExit(f"ОТКАЗ: шаблон {doc.path} совпал {len(found)} раз — подменять нечего")
    match = found[0]
    pieces: list[str] = []
    last = 0
    values = (("date", registry_line._iso_to_display(date_iso, doc.date_format)),
              ("corpora", str(corpora)), ("indexed", str(indexed)))
    for group, value in values:
        start, end = match.span(group)
        pieces.append(text[last:start])
        pieces.append(value)
        last = end
    pieces.append(text[last:])
    return "".join(pieces)


# Пакет копируется целиком: `tools/rev_snapshot.py` берёт корень дерева от
# своего расположения, поэтому в песочнице он обязан найти `src/ragraf`, иначе
# конфиг не читается и проверка объявляет «активная ревизия не прочитана».
# Это условие проверки, а не удобство: копия трёх модулей её ломала.
SANDBOX_TREES = ("src/ragraf",)


def build_sandbox(root: Path) -> None:
    """Копия нужных файлов и пакета с сохранением времени изменения."""
    for name in SANDBOX_TREES:
        shutil.copytree(REPO / name, root / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in SANDBOX_FILES:
        source = REPO / name
        if not source.exists():
            raise SystemExit(f"ОТКАЗ: нет {source} — песочница неполна")
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for name in SANDBOX_OPTIONAL:
        source = REPO / name
        if source.exists():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    # Приманка: посторонний каталог с поддельным README рядом с песочницей.
    decoy = root.parent / (root.name + "-decoy")
    decoy.mkdir(exist_ok=True)
    (decoy / "README.md").write_text("индекс RAGRAF — 1 файл, 1 узел и 1 ребро\n",
                                     encoding="utf-8")
    # Приманка для генератора: та же строка реестра в файле, которого нет в его
    # списке. Генератор обязан писать только в объявленные шесть документов.
    (root / "docs" / "registry-line-decoy.md").write_text(
        "**by the registry on 01.01.2000: 1 corpora, 1 indexed**\n", encoding="utf-8")


def run_checker(root: Path) -> tuple[int, str]:
    completed = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
         str(root / "tools" / "Invoke-ProjectDoctrine.ps1"),
         "-Only", "DocumentedNumbers", "-ProjectRoot", str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def run_registry_tool(root: Path, *extra: str) -> tuple[int, dict, str]:
    completed = subprocess.run(
        [sys.executable, str(root / "tools" / "registry_line.py"),
         "--root", str(root), "--json", *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    try:
        payload = json.loads(completed.stdout or "")
    except json.JSONDecodeError:
        payload = {}
    return completed.returncode, payload, output


def run_publication_gate(root: Path) -> tuple[int, str]:
    completed = subprocess.run(
        [sys.executable, str(root / "tools" / "release" / "gate_release.py"),
         "--check-numbers", "--source", str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def summary_line(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("checks:"):
            return line.strip()
    return "(итоговой строки нет)"


def registry_docs_texts(root: Path) -> dict[str, str]:
    return {name: (root / name).read_text(encoding="utf-8") for name in REGISTRY_DOCS}


def restore_registry_docs(root: Path, texts: dict[str, str]) -> None:
    for name, text in texts.items():
        (root / name).write_text(text, encoding="utf-8", newline="\n")


def set_registry_all(root: Path, originals: dict[str, str],
                     date_iso: str, corpora: int, indexed: int) -> None:
    for doc in registry_line.DOCUMENTS:
        text = replace_registry(originals[doc.path], doc, date_iso, corpora, indexed)
        (root / doc.path).write_text(text, encoding="utf-8", newline="\n")


def sources_row(text: str, path: str) -> list[str] | None:
    """Строка таблицы источников поставки для указанного файла."""
    for line in text.splitlines():
        body = line.rstrip()
        if not body.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in body.strip().strip("|").split("|")]
        if len(cells) == 6 and cells[0] == path:
            return cells
    return None


def registry_values(payload: dict) -> set[tuple[str, int, int]]:
    return {(str(item["date"]), int(item["corpora"]), int(item["indexed"]))
            for item in (payload.get("stated") or [])}


def main() -> int:
    states = history_states()
    if len(states) < 2:
        raise SystemExit("ОТКАЗ: в rev_history меньше двух состояний — три исхода не показать")
    active_id, active_files, active_nodes, active_edges, active_at = states[0]
    lagged = next((row for row in states[1:]
                   if (row[1], row[2], row[3]) != (active_files, active_nodes, active_edges)),
                  None)
    if lagged is None:
        raise SystemExit("ОТКАЗ: все ревизии имеют одинаковые числа — «отстал» не показать")

    print("=" * 78)
    print("0. ЧТО СВЕРЯЕТСЯ: активная ревизия и состояние прошлой ревизии")
    print("=" * 78)
    print(f"  активная: {active_id} {active_at} — {active_files} / {active_nodes} / {active_edges}")
    print(f"  прошлая : {lagged[0]} {lagged[4]} — {lagged[1]} / {lagged[2]} / {lagged[3]}")
    print(f"  выдуманное: 999 / 9 999 / 99 999")
    check(active_id != lagged[0], "активная и прошлая ревизии различаются")

    # Форма образца: если README переписан в другую форму, проверка обязана
    # упасть здесь, а не «отказаться подменять» через десять строк.
    readme_live = (REPO / "README.md").read_text(encoding="utf-8").replace("\u00a0", " ")
    check(NUMBERS_RE.search(readme_live) is not None,
          "образец размеров индекса совпадает с формой README")
    check(len(registry_line.DOCUMENTS) == 6,
          "инструмент строки реестра знает ровно шесть документов",
          str(len(registry_line.DOCUMENTS)))

    root = Path(tempfile.mkdtemp(prefix="ragraf-i33-sb-"))
    build_sandbox(root)
    readme = root / "README.md"
    original = readme.read_text(encoding="utf-8")
    variants = (
        ("активно", active_files, active_nodes, active_edges),
        ("отстал", lagged[1], lagged[2], lagged[3]),
        ("врёт", 999, 9999, 99999),
    )

    results: dict[str, tuple[int, str]] = {}
    gates: dict[str, tuple[int, str]] = {}
    try:
        for label, files, nodes, edges in variants:
            readme.write_text(replace_numbers(original, files, nodes, edges),
                              encoding="utf-8", newline="\n")
            results[label] = run_checker(root)
            gates[label] = run_publication_gate(root)
    finally:
        readme.write_text(original, encoding="utf-8", newline="\n")

    print()
    print("=" * 78)
    print("1. ЧЕКЕР: три исхода одного и того же прогона (размеры индекса)")
    print("=" * 78)
    for label, _files, _nodes, _edges in variants:
        code, output = results[label]
        print(f"  [{label}] код {code}: {summary_line(output)}")

    code, output = results["активно"]
    check(code == 0 and "errors: 0, warnings: 0" in output,
          "активные числа: ошибок и предупреждений нет")
    check("Result: PASS" in output, "активные числа: итог PASS")

    code, output = results["отстал"]
    check(code == 0, "отставание не валит прогон (код 0)")
    check("errors: 0, warnings: 1" in output, "отставание — предупреждение, а не ошибка")
    check("отставание" in output, "сообщение называет состояние словом «отставание»")
    check(lagged[0] in output, f"сообщение называет ревизию {lagged[0]}")
    check(lagged[4][:10] in output, "сообщение называет дату прошлой ревизии")
    check(active_id in output, f"сообщение называет активную ревизию {active_id}")
    check("Result: PASS with warnings" in output, "итог: PASS с предупреждениями")

    code, output = results["врёт"]
    check(code == 1, "выдуманные числа валят прогон (код 1)")
    check("errors: 1" in output, "выдуманные числа — ошибка, а не предупреждение")
    check("такого состояния не было" in output,
          "сообщение прямо называет вымысел вымыслом")
    check("Result: FAIL" in output, "итог: FAIL")

    for label, _files, _nodes, _edges in variants:
        code, output = results[label]
        check("фильтр -Only" in output and "checks: 1" in output,
              f"[{label}] прогон ограничен одной проверкой (-Only)")

    print()
    print("=" * 78)
    print("2. ВОРОТА ПУБЛИКАЦИИ: отставание — отказ, а не предупреждение")
    print("=" * 78)
    for label, _files, _nodes, _edges in variants:
        code, output = gates[label]
        print(f"  [{label}] код {code}: {output.strip().splitlines()[-1] if output.strip() else '—'}")

    # ГРАНИЦА: полный прогон ворот в песочнице недостижим — шаг «числа
    # переводов» требует ВСЕЙ поставки (ссылки переводов обязаны указывать на
    # файлы манифеста), а песочница копирует только нужные проверкам файлы.
    # Поэтому здесь проверяются два шага, чей предмет от поставки не зависит:
    # числа README и строка реестра.
    code, output = gates["активно"]
    check("OK: числа README совпадают с активной ревизией" in output,
          "активные числа: шаг чисел публикации пройден", f"код {code}")
    check("OK: строка реестра сверена" in output,
          "активная строка реестра: шаг реестра публикации пройден", f"код {code}")

    code, output = gates["отстал"]
    check(code == 1, "отставание: публикация остановлена (код 1)")
    check("отставание" in output, "отказ объясняет, что это отставание")
    check(lagged[0] in output, f"отказ называет ревизию прошлого состояния {lagged[0]}")

    code, output = gates["врёт"]
    check(code == 1, "выдуманные числа: публикация остановлена (код 1)")
    check("не было ни в одной ревизии" in output, "отказ называет вымысел вымыслом")

    print()
    print("=" * 78)
    print("3. ПРИМАНКА: посторонний каталог рядом с песочницей")
    print("=" * 78)
    decoy = root.parent / (root.name + "-decoy")
    check(decoy.exists(), "приманка лежит рядом с песочницей")
    code, output = results["активно"]
    check("1 файл, 1 узел" not in output, "проверка не читает README соседнего каталога")

    # ---------------------------------------------------------------- строка реестра
    print()
    print("=" * 78)
    print("4. СТРОКА РЕЕСТРА: совпал / отстал / врёт — на двух потребителях")
    print("=" * 78)
    originals = registry_docs_texts(root)
    catalog_path = root / "catalog" / "catalog.json"
    catalog_original = catalog_path.read_bytes()
    history_path = root / "catalog" / "registry_history.jsonl"

    code, payload, output = run_registry_tool(root, "--verify")
    if not payload:
        raise SystemExit(f"ОТКАЗ: инструмент не дал машинного ответа: {output[:400]}")
    current = payload["registry"]
    current_date = str(current["date"])
    print(f"  реестр: {current_date} — {current['corpora']} / {current['indexed']}; "
          f"осмотрено {payload.get('examined')}")
    check(code == 0 and not payload.get("problems") and not payload.get("lags"),
          "совпало: инструмент молчит (ошибок 0, предупреждений 0)",
          f"код {code}")
    check(int(payload.get("examined") or 0) >= 7,
          "осмотрены шесть документов, строка реестра и снимок",
          str(payload.get("examined")))
    check(len(registry_values(payload)) == 1,
          "все шесть документов называют одни и те же число и дату реестра",
          str(sorted(registry_values(payload))))

    # «Отстал»: реестр обновлён позже, состав тот же. Дату реестра двигает
    # индексация ЛЮБОГО корпуса — ровно тот случай, из-за которого этап начат.
    moved = json.loads(catalog_original.decode("utf-8"))
    moved_date = (datetime.fromisoformat(str(moved["updated_at"])) + timedelta(days=1))
    moved["updated_at"] = moved_date.isoformat()
    catalog_path.write_text(json.dumps(moved, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        code, payload, output = run_registry_tool(root, "--verify")
        check(code == 0 and not payload.get("problems") and len(payload.get("lags") or []) == 6,
              "отставшая дата: ошибок 0, предупреждений шесть (по документу)",
              f"код {code}, предупреждений {len(payload.get('lags') or [])}")
        check(any("отстала дата" in str(item) for item in (payload.get("lags") or [])),
              "предупреждение называет отставание датой")
        check(moved_date.date().isoformat() in json.dumps(payload, ensure_ascii=False),
              "предупреждение называет дату реестра", moved_date.date().isoformat())

        strict_code, strict_payload, _ = run_registry_tool(root, "--verify", "--strict")
        check(strict_code == 1 and not strict_payload.get("problems"),
              "публикационный режим: отставание становится находкой (код 1)")

        checker_code, checker_out = run_checker(root)
        check(checker_code == 0 and "errors: 0, warnings: 1" in checker_out,
              "чекер: отставание — предупреждение, а не ошибка",
              summary_line(checker_out))
        check("Result: PASS with warnings" in checker_out,
              "чекер: итог PASS с предупреждениями")

        gate_code, gate_out = run_publication_gate(root)
        check(gate_code == 1, "ворота публикации: отставшая строка реестра — отказ", f"код {gate_code}")
        check("строка реестра" in gate_out, "отказ ворот называет строку реестра",
              gate_out.strip().splitlines()[-1][:160] if gate_out.strip() else "—")
    finally:
        catalog_path.write_bytes(catalog_original)

    # «Отстал по составу»: состояние есть в журнале прежних состояний реестра.
    old_date = (datetime.fromisoformat(current["updated_at"]) - timedelta(days=7)).isoformat()
    history_path.write_text(
        json.dumps({"recorded_at": old_date,
                    "corpora": int(current["corpora"]) - 1,
                    "indexed": int(current["indexed"]) - 1},
                   ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        set_registry_all(root, originals, current_date,
                         int(current["corpora"]) - 1, int(current["indexed"]) - 1)
        code, payload, output = run_registry_tool(root, "--verify")
        check(code == 0 and not payload.get("problems") and len(payload.get("lags") or []) == 6,
              "состав из журнала: ошибок 0, предупреждений шесть",
              f"код {code}, ошибок {len(payload.get('problems') or [])}")
        check(old_date[:10] in json.dumps(payload.get("lags"), ensure_ascii=False),
              "предупреждение называет дату записи журнала", old_date[:10])
        check(any("отстал состав" in str(item) for item in (payload.get("lags") or [])),
              "предупреждение отличает отставший состав от отставшей даты")
    finally:
        history_path.unlink(missing_ok=True)
        restore_registry_docs(root, originals)

    # «Врёт»: состава, которого не было ни в одной записи журнала.
    set_registry_all(root, originals, current_date, 999, 998)
    code, payload, output = run_registry_tool(root, "--verify")
    check(code == 1 and len(payload.get("problems") or []) >= 6,
          "выдуманный состав: ошибки по каждому документу", f"код {code}")
    check(any("вымысел" in str(item) for item in (payload.get("problems") or [])),
          "сообщение прямо называет вымысел вымыслом")
    checker_code, checker_out = run_checker(root)
    check(checker_code == 1 and "Result: FAIL" in checker_out,
          "чекер: выдуманная строка реестра валит прогон", summary_line(checker_out))
    gate_code, gate_out = run_publication_gate(root)
    check(gate_code == 1, "ворота публикации: выдуманные числа — отказ", f"код {gate_code}")

    # Расхождение переводов между собой: числа совпадают с реестром только в
    # одном документе, у остальных свои. Это ошибка, а не «вольный перевод».
    restore_registry_docs(root, originals)
    (root / "community" / "README.ru.md").write_text(
        replace_registry(originals["community/README.ru.md"],
                         next(doc for doc in registry_line.DOCUMENTS
                              if doc.path == "community/README.ru.md"),
                         current_date, int(current["corpora"]) - 1, int(current["indexed"]) - 1),
        encoding="utf-8", newline="\n")
    code, payload, output = run_registry_tool(root, "--verify")
    check(any("расходятся между собой" in str(item) for item in (payload.get("problems") or [])),
          "перевод с другим числом назван расхождением, а не вольностью")
    restore_registry_docs(root, originals)

    print()
    print("=" * 78)
    print("5. ГЕНЕРАТОР: пишет во все шесть, идемпотентен, приманку не трогает")
    print("=" * 78)
    stale_date = (datetime.fromisoformat(current["updated_at"]) - timedelta(days=3)).date().isoformat()
    set_registry_all(root, originals, stale_date, 1, 0)
    decoy_text = (root / "docs" / "registry-line-decoy.md").read_text(encoding="utf-8")
    before = {name: (root / name).read_bytes() for name in REGISTRY_DOCS}

    write_code, write_payload, write_out = run_registry_tool(root, "--write")
    check(write_code == 0, "генератор отработал без отказа", f"код {write_code}")
    check(len(write_payload.get("changed") or []) == 6,
          "генератор записал все шесть документов",
          str(len(write_payload.get("changed") or [])))
    check(not write_payload.get("problems"),
          "генератор не отказал ни по одному документу",
          json.dumps(write_payload.get("problems") or [], ensure_ascii=False)[:200])

    code, payload, output = run_registry_tool(root, "--verify")
    check(code == 0 and not payload.get("problems") and not payload.get("lags"),
          "после генерации строка реестра совпадает во всех шести документах")
    check(registry_values(payload) == {(current_date, int(current["corpora"]), int(current["indexed"]))},
          "все шесть называют дату и числа реестра",
          str(sorted(registry_values(payload))))

    after = {name: (root / name).read_bytes() for name in REGISTRY_DOCS}
    check(all(before[name] != after[name] for name in REGISTRY_DOCS),
          "строка реестра изменилась в каждом из шести документов")
    code, payload, _ = run_registry_tool(root, "--write")
    check(code == 0 and not (payload.get("changed") or []),
          "повторный прогон ничего не переписывает (идемпотентность)")
    same_again = {name: (root / name).read_bytes() for name in REGISTRY_DOCS}
    check(same_again == after, "байты после повторного прогона не изменились")

    check((root / "docs" / "registry-line-decoy.md").read_text(encoding="utf-8") == decoy_text,
          "файл вне списка шести генератор не тронул")

    # Таблица источников поставки объявляет sha256 каждого перевода. Правка
    # строки реестра меняет их, и пересчёт обязан делать генератор: ручной
    # пересчёт шестидесятизначных хэшей — источник следующего расхождения.
    sources_text = (root / "docs" / "03_implementation" / "RELEASE-SOURCES.md").read_text(
        encoding="utf-8")
    ru_row = sources_row(sources_text, "community/README.ru.md")
    ru_actual = hashlib.sha256((root / "community" / "README.ru.md").read_bytes()).hexdigest()
    check(ru_row is not None and ru_row[3] == ru_actual,
          "генератор пересчитал объявленный sha256 перевода в таблице источников")
    published_row = sources_row(
        (REPO / "docs" / "03_implementation" / "RELEASE-SOURCES.md").read_text(encoding="utf-8"),
        "community/README.ru.md")
    check(ru_row is not None and published_row is not None
          and ru_row[2] == published_row[2],
          "столбец «sha256 в публичном main» генератор не выдумывает: он его не трогает")
    check(ru_row is not None and ru_row[4] == "нет",
          "несовпадение с опубликованной копией объявлено признаком «нет»",
          ru_row[4] if ru_row else "нет строки")
    readme_after = (root / "README.md").read_text(encoding="utf-8")
    check("Published figures (checked on 26.10.2026" in readme_after
          and "**54 operations** on the facade" in readme_after,
          "остальной текст README генератор не переписал: числа поверхности не тронуты")

    # Числа индекса пишутся тем же прогоном и берутся из ЗАПИСИ РЕВИЗИИ: они
    # сдвигаются от добавления одного файла, и два сторожа (ворота поставки и
    # слой изданий) требуют равенства с активной ревизией.
    published = NUMBERS_RE.search(readme_after)
    check(published is not None
          and tuple(int(part.replace(" ", "")) for part in published.groups())
          == (active_files, active_nodes, active_edges),
          "генератор записал размеры активной ревизии, а не литерал",
          str(published.groups()) if published else "не найдено")

    shutil.rmtree(decoy, ignore_errors=True)
    shutil.rmtree(root, ignore_errors=True)

    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
