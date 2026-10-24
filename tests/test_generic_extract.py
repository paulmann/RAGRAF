"""Проверка обобщённого извлекателя на реальных файлах.

Проверяется каждый язык из PROFILES на настоящих файлах каталогов разработки:
  1. файл разбирается;
  2. находятся объявления (функции, классы, методы);
  3. находятся вызовы;
  4. строятся те же виды узлов и рёбер, что у PHP-извлекателя.

Особое внимание: виды узлов обязаны совпадать с известными системе, иначе
операции фасада (callers, impact, slice) не увидят новые языки.
"""

from __future__ import annotations

import pathlib
import sys
from collections import defaultdict

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.extract.generic import PROFILES, GenericExtractor, chunks_for_language  # noqa: E402

REPOS = [pathlib.Path(r"C:\AutoClaw"), pathlib.Path(r"C:\1st_RdpMON\Service")]
SKIP_DIRS = {
    ".git", "node_modules", "vendor", "dist", "build", "target", ".venv",
    "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".idea", ".vscode",
    "coverage", ".ragraf", "site-packages", "joern-cli", "jdk", "obj",
    "packages", "TestResults", "publish", ".tools",
}

# Расширения по языкам для поиска образцов.
EXTS: dict[str, tuple[str, ...]] = {
    "javascript": (".js", ".mjs"),
    "typescript": (".ts",),
    "python": (".py",),
    "csharp": (".cs",),
    "perl": (".pl",),
    "bash": (".sh",),
    "powershell": (".ps1",),
    "ruby": (".rb",),
    "lua": (".lua",),
    "go": (".go",),
    "java": (".java",),
    "c": (".c",),
    "cpp": (".cpp",),
}

# Известные системе виды узлов и рёбер: новые языки не должны выходить за них.
# Список обязан пополняться ВМЕСТЕ с извлекателем: вид, появившийся без записи
# здесь, означает новый класс фактов, который никто не осмотрел.
KNOWN_NODE_KINDS = {
    "file", "function", "method", "class", "interface", "enum", "property",
    "constant", "module", "struct", "external", "namespace",
    # P1-3: пакетная переменная Perl (`our %hFORMAT`) и точка входа/ключ,
    # общие для корпуса (узел без своего файла — помечен props.shared).
    "variable", "route_node", "placeholder",
}
KNOWN_EDGE_KINDS = {
    "defines", "contains", "calls", "calls_heuristic", "calls_builtin",
    "imports", "imports_heuristic", "instantiates", "extends", "implements",
    "queries", "uses_class", "uses_var", "defines_class",
    # P1-3: объявление и чтение переменной Perl, ключ перевода, маршрут.
    "declares", "reads_var", "uses_key", "routes",
}

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
print("СБОР ОБРАЗЦОВ ФАЙЛОВ")
print("=" * 78)

samples: dict[str, list[pathlib.Path]] = defaultdict(list)
for repo in REPOS:
    if not repo.exists():
        continue
    stack = [repo]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir():
                    if entry.name in SKIP_DIRS:
                        continue
                    stack.append(entry)
                    continue
                if not entry.is_file():
                    continue
                if entry.stat().st_size > 200_000 or entry.stat().st_size == 0:
                    continue
            except OSError:
                continue
            ext = entry.suffix.lower()
            for lang, exts in EXTS.items():
                if ext in exts and len(samples[lang]) < 3:
                    samples[lang].append(entry)

for lang in sorted(samples):
    print(f"  {lang}: {len(samples[lang])} образцов")
missing = [l for l in EXTS if l not in samples]
if missing:
    print(f"  образцов нет для: {', '.join(missing)}")

print()
print("=" * 78)
print("РАЗБОР И ПОСТРОЕНИЕ ФАКТОВ")
print("=" * 78)

print(f"\n  {'язык':<12} {'файл':<28} {'симв':>5} {'выз':>5} {'рёб':>5}  разбор")
print("  " + "-" * 72)

all_node_kinds: set[str] = set()
all_edge_kinds: set[str] = set()
results: dict[str, dict] = {}

for lang in sorted(samples):
    profile = PROFILES.get(lang)
    if profile is None:
        print(f"  {lang:<12} профиля нет")
        continue
    extractor = GenericExtractor(profile)
    lang_stats = {"symbols": 0, "calls": 0, "edges": 0, "parsed": 0,
                  "files": 0, "partial": 0, "chunks": 0}
    for path in samples[lang]:
        try:
            data = path.read_bytes()
            res = extractor.extract(str(path), data)
        except Exception as exc:
            print(f"  {lang:<12} ОШИБКА: {type(exc).__name__}: {str(exc)[:80]}")
            continue
        results[str(path)] = res
        lang_stats["files"] += 1
        if res.parse_status == "ok":
            lang_stats["parsed"] += 1
        else:
            lang_stats["partial"] += 1
        syms = [s for s in res.symbols if s.kind not in ("file",)]
        calls = [e for e in res.edges if e.kind.startswith("calls")]
        lang_stats["symbols"] += len(syms)
        lang_stats["calls"] += len(calls)
        lang_stats["edges"] += len(res.edges)
        try:
            ch = chunks_for_language(res, data, max_lines=120, min_lines=3)
            lang_stats["chunks"] += len(ch)
        except Exception as exc:
            print(f"  {lang:<12} чанки: ошибка {type(exc).__name__}")

        for s in res.symbols:
            all_node_kinds.add(s.kind)
        for e in res.edges:
            all_edge_kinds.add(e.kind)

        name = path.name[:26]
        status = res.parse_status
        # Показывается первый символ: видно, что именно найдено.
        first = syms[0].name if syms else "—"
        print(f"  {lang:<12} {name:<28} {len(syms):>5} {len(calls):>5} "
              f"{len(res.edges):>5}  {status} ({first[:16]})")

    print(f"  {'':12} ИТОГО: файлов {lang_stats['files']}, "
          f"разобрано полностью {lang_stats['parsed']}, частично "
          f"{lang_stats['partial']}, символов {lang_stats['symbols']}, "
          f"вызовов {lang_stats['calls']}, чанков {lang_stats['chunks']}")
    print()

print("=" * 78)
print("ПРОВЕРКИ")
print("=" * 78)

# 1. Каждый язык дал символы.
for lang in sorted(samples):
    profile = PROFILES.get(lang)
    if profile is None:
        continue
    has = any(
        r.symbols for p, r in results.items()
        if p.lower().endswith(tuple(EXTS.get(lang, ())))
    )
    check(has, f"{lang}: объявления найдены")

# 2. Виды узлов не выходят за известные системе.
unknown_nodes = all_node_kinds - KNOWN_NODE_KINDS
check(not unknown_nodes, "виды узлов известны системе",
      f"неизвестные: {sorted(unknown_nodes)}")
print(f"    виды узлов: {sorted(all_node_kinds)}")

# 3. Виды рёбер тоже.
unknown_edges = all_edge_kinds - KNOWN_EDGE_KINDS
check(not unknown_edges, "виды рёбер известны системе",
      f"неизвестные: {sorted(unknown_edges)}")
print(f"    виды рёбер: {sorted(all_edge_kinds)}")

# 4. Ребра calls непусты: иначе структура бесполезна.
check(any("calls" in k or "calls_heuristic" in k for k in all_edge_kinds),
      "вызовы извлекаются хотя бы для одного языка")

# 5. Частичный разбор помечается честно.
print()
print("=== Файлы с частичным разбором (структура неполная, но файл в индексе) ===")
partial_files = [(p, r.parse_note) for p, r in results.items()
                 if r.parse_status != "ok"]
print(f"  всего: {len(partial_files)} из {len(results)}")
for p, note in partial_files[:8]:
    name = pathlib.Path(p).name
    print(f"    {name}: {str(note)[:70]}")

print()
print("=== ИТОГ ===")
print(f"пройдено: {PASS}, провалено: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)
