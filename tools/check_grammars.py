"""Проверка: какие языки умеет разбирать tree-sitter в этой установке.

От этого зависит выбор способа: если грамматика есть — можно разбирать деревом
и получать узлы, рёбра, вызовы. Если грамматики нет — язык подключается как
текстовый (чанки и поиск без структуры).

Проверяется вызовом, а не по памяти: у tree-sitter набор языков зависит
от версии пакета tree-sitter-language-pack.
"""

from __future__ import annotations

import importlib.metadata as md

print("=" * 78)
print("ВЕРСИИ РАЗБОРЩИКА")
print("=" * 78)
for pkg in ("tree-sitter", "tree-sitter-language-pack", "tree_sitter_language_pack"):
    try:
        print(f"  {pkg}: {md.version(pkg)}")
    except Exception:
        print(f"  {pkg}: не установлен")

print()
print("=" * 78)
print("КАКИЕ ЯЗЫКИ ДОСТУПНЫ В ГРАММАТИКАХ")
print("=" * 78)

# Интересуют языки, найденные при сканировании стеков.
WANTED = [
    ("php", "PHP"),
    ("javascript", "JavaScript"),
    ("typescript", "TypeScript"),
    ("tsx", "TypeScript JSX"),
    ("css", "CSS"),
    ("html", "HTML"),
    ("bash", "bash / sh"),
    ("c_sharp", "C#"),
    ("csharp", "C# (альтернативное имя)"),
    ("perl", "Perl"),
    ("python", "Python"),
    ("c", "C"),
    ("cpp", "C++"),
    ("java", "Java"),
    ("go", "Go"),
    ("rust", "Rust"),
    ("ruby", "Ruby"),
    ("lua", "Lua"),
    ("sql", "SQL"),
    ("yaml", "YAML"),
    ("json", "JSON"),
    ("toml", "TOML"),
    ("ini", "INI"),
    ("powershell", "PowerShell"),
    ("batch", "batch / CMD"),
    ("markdown", "Markdown"),
    ("xml", "XML"),
]

try:
    from tree_sitter_language_pack import get_parser  # type: ignore

    available: list[str] = []
    unavailable: list[tuple[str, str]] = []
    for name, title in WANTED:
        try:
            get_parser(name)
            available.append(f"{name} ({title})")
        except Exception as exc:
            unavailable.append((f"{name} ({title})", type(exc).__name__))

    print(f"\n  ДОСТУПНЫ ({len(available)}):")
    for item in available:
        print(f"    ✓ {item}")
    print(f"\n  НЕДОСТУПНЫ ({len(unavailable)}):")
    for item, err in unavailable:
        print(f"    ✗ {item} — {err}")

except ImportError as exc:
    print(f"  пакет грамматик недоступен: {exc}")

print()
print("=" * 78)
print("ПОЛНЫЙ СПИСОК ЯЗЫКОВ ПАКЕТА (для справки)")
print("=" * 78)
try:
    from tree_sitter_language_pack import get_binding  # type: ignore

    # Пробуется получить список через модуль, если он есть.
    import tree_sitter_language_pack as pack  # type: ignore

    names = [n for n in dir(pack) if not n.startswith("_")]
    print(f"  экспортируемые имена: {names[:20]}")
except Exception as exc:
    print(f"  список получить не удалось: {type(exc).__name__}: {exc}")

# Проверка на реальном файле: разбирается ли, и сколько узлов даёт.
print()
print("=" * 78)
print("ПРОВЕРКА РАЗБОРА НА РЕАЛЬНЫХ ФАЙЛАХ")
print("=" * 78)
import pathlib  # noqa: E402

SAMPLES = [
    (r"C:\1st_RdpMON\Service\src", ".cs", "c_sharp"),
    (r"C:\AutoClaw\RAGRAF\scripts", ".ps1", "powershell"),
    (r"C:\AutoClaw\mdviewer", ".js", "javascript"),
]

try:
    from tree_sitter_language_pack import get_parser  # type: ignore
    for root_str, ext, lang in SAMPLES:
        root = pathlib.Path(root_str)
        if not root.exists():
            print(f"  {root_str}: каталога нет")
            continue
        files = sorted(root.rglob(f"*{ext}"))[:1]
        if not files:
            print(f"  {root_str}: файлов {ext} не найдено")
            continue
        path = files[0]
        data = path.read_bytes()
        try:
            parser = get_parser(lang)
            tree = parser.parse(data)
            errors = 1 if tree.root_node.has_error else 0
            print(f"  {lang:<12} {path.name}: разобран, "
                  f"корневых узлов {len(tree.root_node.children)}, "
                  f"ошибок {'да' if errors else 'нет'}")
        except Exception as exc:
            print(f"  {lang:<12} {path.name}: НЕ разобран — {type(exc).__name__}")
except Exception as exc:
    print(f"  проверка не выполнена: {type(exc).__name__}: {exc}")
