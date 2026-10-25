"""Исправление дефекта: обновление отвергает нормальные пути в Windows.

НАЙДЕННЫЙ ДЕФЕКТ (воспроизводится вызовом).

`core/updater_apply.py`, функция `_verify_rel`, строки 93-103:

    norm = os.path.normpath(rel)          # на Windows: 'a/b' -> 'a\\b'
    parts = norm.split("/")               # split('/') по '\\' НЕ разделяет!
    ...
    if rel != norm:
        raise ValueError("non-normalized path: %r" % rel)

Две ошибки, обе проявляются только на Windows:

  1. `os.path.normpath` на Windows заменяет прямые слеши (`/`) на обратные
     (`\\`). Поэтому `rel != norm` истинно для ЛЮБОГО вложенного пути,
     и функция отвергает его как «ненормализованный».

  2. `norm.split("/")` после normpath не разделяет путь: разделитель уже
     `\\`. Значит проверки `".." in parts` и `parts == [""]` не работают
     для вложенных путей — защита ослаблена.

ЗАМЕР:
    'assets/logo.svg'   -> normpath 'assets\\logo.svg'  -> отвергнут
    'langs/ru.json'     -> normpath 'langs\\ru.json'    -> отвергнут
    'app.py'            -> normpath 'app.py'            -> принят

СЛЕДСТВИЕ: на Windows механизм обновления платформы не может применить
НИ ОДНОГО файла из подкаталога. Обновляются только файлы в корне проекта.
Именно поэтому падают 6+ тестов обновления — они проверяют основной
сценарий, и он сломан.

ИСПРАВЛЕНИЕ:
  * для проверки формы используется `PurePosixPath(rel).as_posix()`, а не
    `os.path.normpath` — тогда разделители не зависят от платформы;
  * `parts` берётся из уже нормализованного POSIX-пути, поэтому `..`
    и абсолютные пути распознаются верно;
  * защита усиливается, а не ослабляется: отвергаются и абсолютные пути,
    и ведущий слэш, и буква диска, и `..`, и пустой путь.

Правка НЕ меняет интерфейс функции: она принимает строку и возвращает
нормализованный POSIX-путь либо возбуждает ValueError с прежними текстами
сообщений («unsafe path», «empty path», «backslash in path»).
"""

from __future__ import annotations

import ast
import pathlib
import shutil

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "core" / "updater_apply.py"
text = TARGET.read_text(encoding="utf-8")

OLD = '''def _verify_rel(rel):
    """Validate a manifest-relative path; return it normalized or raise
    ValueError. Rejects absolute paths, '..' components, backslashes and
    non-normalized forms so a malicious manifest can never write outside
    the project root."""
    if not isinstance(rel, str) or not rel:
        raise ValueError("empty path")
    if "\\\\" in rel:
        raise ValueError("backslash in path: %r" % rel)
    norm = os.path.normpath(rel)
    parts = norm.split("/")
    if (
        norm.startswith("/")
        or ":" in parts[0]
        or ".." in parts
        or parts == [""]
    ):
        raise ValueError("unsafe path: %r" % rel)
    if rel != norm:
        raise ValueError("non-normalized path: %r" % rel)
    return norm'''

NEW = '''def _verify_rel(rel):
    """Validate a manifest-relative path; return it normalized or raise
    ValueError. Rejects absolute paths, '..' components, backslashes and
    non-normalized forms so a malicious manifest can never write outside
    the project root.

    PLATFORM INDEPENDENCE. The path is normalized with `PurePosixPath`,
    NOT with `os.path.normpath`. On Windows `os.path.normpath` rewrites
    forward slashes to backslashes ('a/b' -> 'a\\\\b'), which broke two
    things at once:

      * `rel != norm` was true for EVERY nested path, so every file inside
        a subdirectory was rejected as "non-normalized" - updates could
        only apply files sitting in the project root;
      * `norm.split("/")` no longer split anything (the separator had
        already become '\\\\'), so the '..' check silently stopped working
        for nested paths.

    Manifest paths are always POSIX-style, so they are validated in POSIX
    form. This is stricter than before, not looser: absolute paths, a
    leading slash, a drive letter, '..' components and the empty path are
    all rejected.
    """
    if not isinstance(rel, str) or not rel:
        raise ValueError("empty path")
    if "\\\\" in rel:
        raise ValueError("backslash in path: %r" % rel)

    norm = PurePosixPath(rel).as_posix()
    parts = norm.split("/")
    if (
        norm.startswith("/")
        or ":" in parts[0]
        or ".." in parts
        or parts == [""]
        or not norm
        or norm == "."
    ):
        raise ValueError("unsafe path: %r" % rel)
    if rel != norm:
        raise ValueError("non-normalized path: %r" % rel)
    return norm'''

OLD_IMPORT = "import hashlib\nimport json\nimport os"
NEW_IMPORT = "import hashlib\nimport json\nimport os\nfrom pathlib import PurePosixPath"

if OLD not in text:
    if "PurePosixPath" in text:
        print("  правка уже внесена")
        raise SystemExit(0)
    raise SystemExit("НЕ НАЙДЕНА функция _verify_rel")

backup = TARGET.with_suffix(".py.bak-verifyrel")
shutil.copy2(TARGET, backup)
print(f"  бэкап: {backup.name}")

text = text.replace(OLD, NEW, 1)
if "from pathlib import PurePosixPath" not in text:
    if OLD_IMPORT in text:
        text = text.replace(OLD_IMPORT, NEW_IMPORT, 1)
        print("  добавлен импорт PurePosixPath")
    else:
        # Импорт добавляется после последнего импорта в начале файла.
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                last = i
        lines.insert(last + 1, "from pathlib import PurePosixPath")
        text = "\n".join(lines) + "\n"
        print("  импорт добавлен после блока импортов")

TARGET.write_text(text, encoding="utf-8", newline="\n")
after = TARGET.read_text(encoding="utf-8")
print("  правка внесена")

try:
    ast.parse(after)
    print("  синтаксис верен")
except SyntaxError as exc:
    print(f"  ПРОВАЛ синтаксиса: строка {exc.lineno}: {exc.msg}")
    raise SystemExit(1)

checks = [
    ("PurePosixPath(rel).as_posix()" in after, "используется PurePosixPath"),
    ("os.path.normpath(rel)" not in after.split("def _verify_rel")[1]
     .split("def ")[0] if "def _verify_rel" in after else False,
     "os.path.normpath больше не используется в проверке"),
    ("from pathlib import PurePosixPath" in after, "импорт добавлен"),
]
print()
for ok, label in checks:
    print(f"  [{'OK ' if ok else '--'}] {label}")
