"""Проверка разбора деревом на РЕАЛЬНЫХ файлах нужных языков.

Грамматики доступны для 26 языков. Но наличие грамматики не значит, что она
справится с реальным кодом: у PowerShell-файла выше уже была ошибка разбора.
Проверяется каждый язык на файлах из сканированных каталогов, и считается
доля файлов с ошибками.

От этого зависит решение: какой язык можно разбирать деревом (узлы, рёбра,
вызовы), а какой придётся подключить как текстовый (только чанки и поиск).
"""

from __future__ import annotations

import pathlib
from collections import defaultdict

from tree_sitter_language_pack import get_parser  # type: ignore

REPOS = [pathlib.Path(__file__).resolve().parents[1].parent, pathlib.Path(__file__).resolve().parents[1].parent.parent / "1st_RdpMON" / "Service"]

SKIP_DIRS = {
    ".git", "node_modules", "vendor", "dist", "build", "target", ".venv",
    "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".idea", ".vscode",
    "coverage", ".ragraf", "site-packages", "joern-cli", "jdk", "obj",
    "packages", "TestResults", "publish", ".tools",
}

# Язык → расширения, которые ему соответствуют.
LANGS: dict[str, tuple[str, ...]] = {
    "powershell": (".ps1", ".psm1"),
    "bash": (".sh", ".bash"),
    "batch": (".bat", ".cmd"),
    "csharp": (".cs",),
    "perl": (".pl", ".pm"),
    "python": (".py",),
    "javascript": (".js", ".mjs"),
    "typescript": (".ts",),
    "php": (".php",),
    "sql": (".sql",),
    "css": (".css",),
    "html": (".html", ".htm"),
    "c": (".c",),
    "cpp": (".cpp", ".hpp", ".h"),
    "go": (".go",),
    "java": (".java",),
    "lua": (".lua",),
    "markdown": (".md",),
    "yaml": (".yml", ".yaml"),
    "json": (".json",),
    "toml": (".toml",),
    "ini": (".ini", ".cfg", ".conf"),
}

# Сколько файлов каждого языка проверять.
SAMPLE = 12
# Файлы крупнее этого не берутся: разбор гигантов искажает картину.
MAX_SIZE = 400_000

print("=" * 78)
print("РАЗБОР ДЕРЕВОМ НА РЕАЛЬНЫХ ФАЙЛАХ")
print("=" * 78)

# Сначала собираются образцы файлов по языкам.
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
            except OSError:
                continue
            ext = entry.suffix.lower()
            for lang, exts in LANGS.items():
                if ext in exts and len(samples[lang]) < SAMPLE:
                    try:
                        if 0 < entry.stat().st_size <= MAX_SIZE:
                            samples[lang].append(entry)
                    except OSError:
                        pass

print(f"\n  {'язык':<12} {'файлов':>7} {'разобрано':>10} {'с ошибками':>11}  вывод")
print("  " + "-" * 62)

verdicts: dict[str, str] = {}
for lang in sorted(samples, key=lambda l: -len(samples[l])):
    parser = None
    try:
        parser = get_parser(lang)
    except Exception as exc:
        print(f"  {lang:<12} {'—':>7} {'—':>10} {'—':>11}  грамматика недоступна: "
              f"{type(exc).__name__}")
        verdicts[lang] = "нет грамматики"
        continue

    ok = 0
    with_errors = 0
    failed = 0
    for path in samples[lang]:
        try:
            tree = parser.parse(path.read_bytes())
        except Exception:
            failed += 1
            continue
        if tree.root_node.has_error:
            with_errors += 1
        else:
            ok += 1

    total = len(samples[lang])
    if failed == total:
        verdict = "не разбирается вовсе"
    elif ok >= total * 0.8:
        verdict = "разбирается деревом"
    elif ok >= total * 0.3:
        verdict = "разбирается частично"
    else:
        verdict = "много ошибок разбора"
    verdicts[lang] = verdict
    print(f"  {lang:<12} {total:>7} {ok:>10} {with_errors:>11}  {verdict}")

print()
print("=" * 78)
print("РЕШЕНИЕ ПО ЯЗЫКАМ")
print("=" * 78)
tree_langs = [l for l, v in verdicts.items() if v == "разбирается деревом"]
partial = [l for l, v in verdicts.items() if v.startswith("разбирается част")]
text_langs = [l for l, v in verdicts.items() if v not in tree_langs and l not in partial]

print(f"\n  РАЗБИРАТЬ ДЕРЕВОМ ({len(tree_langs)}):")
print(f"    {', '.join(sorted(tree_langs))}")
print(f"\n  ЧАСТИЧНО ({len(partial)}):")
print(f"    {', '.join(sorted(partial)) or '—'}")
print(f"\n  ТОЛЬКО ТЕКСТОМ ({len(text_langs)}):")
print(f"    {', '.join(sorted(text_langs)) or '—'}")
