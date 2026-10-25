"""ПРОМТ-И14: три исхода проверки чисел — «активно», «отстал», «врёт».

Измеренный дефект (25.10.2026). Документ текущего состояния называет размеры
собственного индекса, и до этого этапа любое расхождение с активной ревизией
объявлялось одной ошибкой. Следствие: живой документ, отставший на один
пересбор, валил прогон точно так же, как выдуманные числа, — а различие
«устаревший замер» и «состояние, которого не было» не проверялось вовсе.
Лечение мягким предупреждением без второй половины хуже дефекта: предупреждение
превращается в вечное оправдание, если публикация его не замечает.

Поэтому проверяется СВОЙСТВО в трёх состояниях и на ДВУХ потребителях:

  1) числа активной ревизии            -> чекер молчит, ворот публикации пропускает;
  2) числа ПРОШЛОЙ ревизии из истории  -> чекер предупреждает (не ошибка),
                                          ворот публикации ОТКАЗЫВАЕТ;
  3) числа, которых не было ни разу    -> чекер ошибается,
                                          ворот публикации отказывает.

Песочница, а не живое дерево: копия тех файлов, которые читает проверка, в
каталоге временного хранения. Живой README не меняется ни на байт, индекс не
пересобирается. Приманка в песочнице: каталог с посторонним README рядом —
проверка обязана смотреть в свой корень, а не в соседа.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

PASS = 0
FAIL = 0

# Форма записи размеров индекса — та же, что у потребителей.
NUMBERS_RE = re.compile(
    r"индекс RAGRAF — ([\d ]+?)\s*файл\w*,\s*([\d ]+?)\s*уз(?:ел|л)\w*\s+и\s+([\d ]+?)\s*р[её]б\w*"
)

# Что читает проверка Test-DocumentedNumbers. Список явный: если проверка начнёт
# читать новый файл, песочница перестанет её удовлетворять и тест это покажет.
SANDBOX_FILES = (
    "README.md",
    "AGENTS.md",
    "docs/AGENTS-context.md",
    "docs/03_implementation/INTEGRATION.md",
    "docs/03_implementation/HARNESS_INTEGRATION.md",
    "docs/03_implementation/AUTOCLAW_INTEGRATION.md",
    "docs/03_implementation/AGENTS_SETUP_AND_CAPABILITIES.md",
    "docs/05_article/RAGRAF_ARTICLE.md",
    "catalog/catalog.json",
    "config/ragraf.yaml",
    "tools/Invoke-ProjectDoctrine.ps1",
    "tools/rev_snapshot.py",
    "tools/release/gate_release.py",
    "tools/release/relcommon.py",
)


def check(ok: bool, message: str) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok   {message}")
    else:
        FAIL += 1
        print(f"  FAIL {message}")


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
    # Приманка: посторонний каталог с поддельным README рядом с песочницей.
    decoy = root.parent / (root.name + "-decoy")
    decoy.mkdir(exist_ok=True)
    (decoy / "README.md").write_text("индекс RAGRAF — 1 файл, 1 узел и 1 ребро\n",
                                     encoding="utf-8")


def run_checker(root: Path) -> tuple[int, str]:
    completed = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
         str(root / "tools" / "Invoke-ProjectDoctrine.ps1"),
         "-Only", "DocumentedNumbers", "-ProjectRoot", str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


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

    root = Path(tempfile.mkdtemp(prefix="ragraf-i14-sb-"))
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
    print("1. ЧЕКЕР: три исхода одного и того же прогона")
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
    print("2. ВОРОТ ПУБЛИКАЦИИ: отставание — отказ, а не предупреждение")
    print("=" * 78)
    for label, _files, _nodes, _edges in variants:
        code, output = gates[label]
        print(f"  [{label}] код {code}: {output.strip().splitlines()[-1] if output.strip() else '—'}")

    code, output = gates["активно"]
    check(code == 0, "активные числа: публикация пропущена (код 0)")
    check("совпадают с активной ревизией" in output, "пропуск назван причиной")

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
    shutil.rmtree(decoy, ignore_errors=True)
    shutil.rmtree(root, ignore_errors=True)

    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
