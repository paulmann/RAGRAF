"""nav_probe — измерение навигации по коду на РЕАЛЬНОМ корпусе.

Зачем отдельный измеритель. Ключевая цифра продукта («сколько обращений и
кандидатов нужно, чтобы найти место в коде») до сих пор жила только текстом в
README, а сырые артефакты прогона не публиковались (R-REL-1 в ROADMAP.md).
Жюри и читатель не могут проверить число, у которого нет артефакта, — значит
замер обязан быть воспроизводимым одной командой и лежать в репозитории.

Что измеряется, и почему именно это.

  * эталон (ground truth) берётся из ТЕКСТА исходников регулярным выражением,
    а не из индекса: иначе измерение проверяло бы индекс индексом;
  * выборка вопросов детерминированная — имена определений сортируются и
    берутся с постоянным шагом. Никакого отбора «удобных» имён: иначе цифра
    перестаёт быть замером;
  * базовая линия — `rg -l` по тому же корпусу: столько файлов агент увидит
    без индекса, и столько байт он прочитает, если откроет их все;
  * ответ индекса — одна операция `symbol`, у факта есть `path:line`;
  * считается, что дороже всего: сколько файлов-кандидатов и сколько БАЙТ
    контекста тратится на то, чтобы узнать, где лежит определение.

Ограничения, которые не прячутся:
  * «до индекса» моделируется `rg -l`, а не вторым прогоном агента: реальный
    агент читает не все найденные файлы, поэтому базовая линия — верхняя
    граница, а не измерение конкретного агента;
  * корпус с зеркалами (php83/php85/src-8.5) даёт кандидатов в обоих плечах,
    поэтому сужение по файлам там меньше, чем по байтам. Оба числа печатаются;
  * динамика (значения во время выполнения) не покрыта: это статический индекс.

Запуск:
    python bench/nav_probe.py --project config/htmlphp-src.yaml --limit 14 \
        --out bench/results/nav_probe_20261021.json

Код возврата: 0 — замер выполнен (даже если hit@1 низкий), 2 — замер не
выполнен (нет индекса, нет rg, пустой корпус).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

# Каталоги, которые не являются исходниками: зеркала зависимостей и сборки.
SKIP_DIRS = {".git", ".ragraf", ".zwork", "node_modules", "vendor", "dist", "build"}

# Определения по расширениям. Регулярка ищет ТОЛЬКО текст, индекс не участвует.
DEF_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    ".php": [
        ("function", re.compile(r"^[ \t]*(?:public|private|protected|static|final|abstract|\s)*function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")),
        ("class", re.compile(r"^[ \t]*(?:final|abstract)?\s*class\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ],
    ".py": [
        ("function", re.compile(r"^[ \t]*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")),
        ("class", re.compile(r"^[ \t]*class\s+([A-Za-z_][A-Za-z0-9_]*)")),
    ],
    ".ps1": [
        ("function", re.compile(r"^[ \t]*function\s+([A-Za-z_][A-Za-z0-9_-]*)", re.IGNORECASE)),
    ],
    ".js": [
        ("function", re.compile(r"^[ \t]*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")),
        ("class", re.compile(r"^[ \t]*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)")),
    ],
    ".ts": [
        ("function", re.compile(r"^[ \t]*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")),
        ("class", re.compile(r"^[ \t]*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)")),
    ],
}

# Служебные имена: конструкторы и магические методы дают десятки определений
# на корпус и меряют не навигацию, а распространённость шаблона.
SKIP_NAMES = {
    "__construct", "__destruct", "__toString", "__get", "__set", "__call",
    "__invoke", "__clone", "__wakeup", "__sleep", "main", "init", "setUp",
    "tearDown", "test", "run", "get", "set", "render", "toString",
}


@dataclass
class Question:
    """Один вопрос навигации и его измеренный результат."""

    name: str
    kind: str
    ground_truth_files: list[str]
    baseline_files: int
    baseline_bytes: int
    index_found: bool
    index_calls: int
    index_files: int
    index_bytes: int
    first_hit_rank: int  # 1 — первый же факт указывает на эталонный файл; 0 — не найден
    index_ms: int


def iter_sources(root: Path, exts: set[str]):
    """Обход корпуса без зеркал зависимостей и служебных каталогов."""
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in exts:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def collect_definitions(root: Path, exts: set[str]) -> dict[str, set[str]]:
    """Имя определения → файлы, где оно объявлено (по тексту, не по индексу)."""
    found: dict[str, set[str]] = {}
    for path in iter_sources(root, exts):
        patterns = DEF_PATTERNS.get(path.suffix.lower(), [])
        if not patterns:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for _kind, pattern in patterns:
            for line in text.splitlines():
                match = pattern.match(line)
                if not match:
                    continue
                name = match.group(1)
                if name.lower() in SKIP_NAMES or len(name) < 4:
                    continue
                found.setdefault(name, set()).add(rel)
    return found


def sampled(names: list[str], limit: int) -> list[str]:
    """Детерминированная выборка: сортировка + постоянный шаг, без случайности."""
    ordered = sorted(names)
    if not ordered:
        return []
    if len(ordered) <= limit:
        return ordered
    stride = max(1, len(ordered) // limit)
    picked = ordered[::stride][:limit]
    return picked


def rg_baseline(root: Path, name: str) -> tuple[int, int]:
    """Сколько файлов и байт увидит агент без индекса (rg -l по тому же корпусу)."""
    cmd = [
        "rg", "--files-with-matches", "--no-messages", "--fixed-strings",
        "--glob", "!{.git,.ragraf,.zwork,node_modules,vendor,dist,build}/**",
        "--", name, ".",
    ]
    try:
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return 0, 0
    files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    total = 0
    for rel in files:
        try:
            total += (root / rel).stat().st_size
        except OSError:
            pass
    return len(files), total


def answer_paths(env: dict) -> list[str]:
    """Пути из конверта ответа в порядке выдачи."""
    rows = env.get("result") or []
    paths: list[str] = []
    for row in rows:
        path = row.get("path")
        if isinstance(path, str) and path:
            paths.append(path.replace("\\", "/"))
    return paths


def measure(project: Path, limit: int, exts: set[str]) -> dict:
    cfg = load_config(str(project))
    root = Path(cfg.root)
    if not root.is_dir():
        raise SystemExit(f"корень корпуса не найден: {root}")
    if shutil.which("rg") is None:
        raise SystemExit("rg не найден в PATH: базовая линия не измерима")

    definitions = collect_definitions(root, exts)
    if not definitions:
        raise SystemExit("в корпусе не найдено ни одного определения")

    facade = Facade(cfg, tool="dev", telemetry=False, watch=False, agent="nav_probe")
    names = sampled(list(definitions), limit)
    questions: list[Question] = []

    for name in names:
        gt = sorted(definitions[name])
        gt_set = set(gt)
        base_files, base_bytes = rg_baseline(root, name)

        started = time.perf_counter()
        env = facade.op_symbol(name, limit=20)
        elapsed = int((time.perf_counter() - started) * 1000)

        paths = answer_paths(env)
        rank = 0
        for position, path in enumerate(paths, start=1):
            if path in gt_set:
                rank = position
                break
        index_bytes = len(json.dumps(env, ensure_ascii=False).encode("utf-8"))

        questions.append(Question(
            name=name,
            kind="symbol",
            ground_truth_files=gt,
            baseline_files=base_files,
            baseline_bytes=base_bytes,
            index_found=bool(env.get("found")),
            index_calls=1,
            index_files=len(set(paths)),
            index_bytes=index_bytes,
            first_hit_rank=rank,
            index_ms=elapsed,
        ))

    total = len(questions)
    found = sum(1 for q in questions if q.index_found)
    hit_first = sum(1 for q in questions if q.first_hit_rank == 1)
    hit_any = sum(1 for q in questions if q.first_hit_rank > 0)
    base_files_total = sum(q.baseline_files for q in questions)
    base_bytes_total = sum(q.baseline_bytes for q in questions)
    index_files_total = sum(q.index_files for q in questions)
    index_bytes_total = sum(q.index_bytes for q in questions)

    def median(values: list[int]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return float(ordered[mid])
        return (ordered[mid - 1] + ordered[mid]) / 2.0

    summary = {
        "questions": total,
        "sampling": "имена определений отсортированы, взяты с постоянным шагом (без случайности)",
        "index_calls_total": sum(q.index_calls for q in questions),
        "index_found": found,
        "hit_at_1": hit_first,
        "hit_any_rank": hit_any,
        "hit_at_1_rate": round(hit_first / total, 4) if total else 0.0,
        "hit_any_rate": round(hit_any / total, 4) if total else 0.0,
        "baseline_files_total": base_files_total,
        "index_files_total": index_files_total,
        "files_narrowing": round(base_files_total / index_files_total, 2) if index_files_total else 0.0,
        "baseline_bytes_total": base_bytes_total,
        "index_bytes_total": index_bytes_total,
        "bytes_narrowing": round(base_bytes_total / index_bytes_total, 1) if index_bytes_total else 0.0,
        "median_baseline_files": median([q.baseline_files for q in questions]),
        "median_index_files": median([q.index_files for q in questions]),
        "median_index_ms": median([q.index_ms for q in questions]),
        "worst_case": max(((q.name, q.baseline_files, q.index_files) for q in questions),
                          default=("", 0, 0), key=lambda item: item[1]),
    }

    return {
        "probe": "nav_probe",
        "measured_on": date.today().isoformat(),
        # Только имена, без абсолютных путей: артефакт публикуется, и локальная
        # раскладка машины автора к замеру отношения не имеет. Абсолютный путь
        # делал бы артефакт похожим на машинно-зависимый и раскрывал структуру
        # каталогов без пользы для проверяющего.
        "corpus": {
            "name": Path(project).stem,
            "root_name": root.name,
            "config_name": Path(project).name,
            **_revision(cfg),
            "extensions": sorted(exts),
            "definitions_found": len(definitions),
        },
        "method": {
            "ground_truth": "регулярное выражение по тексту исходников (индекс не участвует)",
            "baseline": "rg -l по тому же корпусу; байты — сумма размеров найденных файлов",
            "index": "одна операция facade.op_symbol(name, limit=20) на вопрос",
            "hit_at_1": "первый же факт ответа указывает на файл с определением",
            "caveats": [
                "rg -l — верхняя граница для агента, а не измерение конкретного агента",
                "корпус содержит зеркала (php83/php85/src-8.5): сужение по файлам меньше, чем по байтам",
                "динамика во время выполнения не покрыта (статический индекс)",
            ],
        },
        "summary": summary,
        "questions": [asdict(q) for q in questions],
    }


def _revision(cfg) -> dict:
    """Паспорт ревизии: номер, режим и время запечатывания — из самого индекса.

    Поле называется `rev_id` (замерено на rev.json), а не `rev`: догадка о имени
    поля даёт «unknown» и лишает артефакт привязки к ревизии.
    """
    try:
        data = json.loads(Path(cfg.rev_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"rev": "unknown", "capability": "unknown", "sealed_at": "unknown"}
    return {
        "rev": str(data.get("rev_id") or data.get("rev") or "unknown"),
        "capability": str(data.get("capability") or "unknown"),
        "sealed_at": str(data.get("sealed_at") or "unknown"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Замер навигации: индекс против rg")
    parser.add_argument("--project", required=True, help="конфигурация корпуса (yaml)")
    parser.add_argument("--limit", type=int, default=14, help="сколько вопросов взять")
    parser.add_argument("--ext", action="append", default=None,
                        help="расширение исходников, можно несколько (по умолчанию .php)")
    parser.add_argument("--out", default=None, help="куда положить JSON-артефакт")
    args = parser.parse_args(argv)

    exts = {ext.lower() if ext.startswith(".") else "." + ext.lower()
            for ext in (args.ext or [".php"])}

    try:
        report = measure(Path(args.project), args.limit, exts)
    except SystemExit as exc:
        print(f"[nav_probe] замер не выполнен: {exc}", file=sys.stderr)
        return 2

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # newline="\n" обязателен: на Windows открытие в текстовом режиме
        # превращает перевод строки в CRLF, и артефакт перестаёт совпадать с
        # остальным деревом (там LF, см. .gitattributes).
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8", newline="\n")
        print(f"[nav_probe] артефакт: {out_path.as_posix()}")

    summary = report["summary"]
    print(f"[nav_probe] корпус: {report['corpus']['root_name']}  ревизия: {report['corpus']['rev']}"
          f"  режим: {report['corpus']['capability']}")
    print(f"[nav_probe] вопросов: {summary['questions']} (выборка детерминированная)")
    print()
    print(f"  {'имя':<26}{'rg, файлов':>12}{'индекс':>8}{'байт rg':>12}{'байт индекс':>13}{'хит':>6}")
    for question in report["questions"]:
        print(f"  {question['name']:<26}{question['baseline_files']:>12}{question['index_files']:>8}"
              f"{question['baseline_bytes']:>12}{question['index_bytes']:>13}"
              f"{('#' + str(question['first_hit_rank'])) if question['first_hit_rank'] else '—':>6}")
    print()
    print(f"  обращений к индексу: {summary['index_calls_total']} на {summary['questions']} вопросов")
    print(f"  попадание с первого факта: {summary['hit_at_1']}/{summary['questions']}"
          f" ({summary['hit_at_1_rate'] * 100:.0f}%)")
    print(f"  попадание в любом месте ответа: {summary['hit_any_rank']}/{summary['questions']}")
    print(f"  кандидатов: {summary['baseline_files_total']} (rg) → {summary['index_files_total']} (индекс),"
          f" сужение ×{summary['files_narrowing']}")
    print(f"  контекста: {summary['baseline_bytes_total']} Б → {summary['index_bytes_total']} Б,"
          f" сужение ×{summary['bytes_narrowing']}")
    print(f"  медиана ответа: {summary['median_index_ms']:.0f} мс")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
