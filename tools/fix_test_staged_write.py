"""Исправление теста: staged-файл писался текстово и портил хеш.

НАЙДЕННЫЙ ДЕФЕКТ — В ТЕСТЕ, а не в коде обновления.

Сравнение поведения:

  РЕАЛЬНЫЙ механизм (`core/updater.py:509`) скачивает staged-файл ДВОИЧНО:
      with open(tmp, 'wb') as out:            # байты сохраняются как есть
  Значит в хранилище подготовленных файлов лежат те же байты, от которых
  считался sha256 в манифесте. Проверка сходится.

  ТЕСТ (`tests/test_updater_apply.py:27`) писал staged-файл ТЕКСТОВО:
      with open(p, "w", encoding="utf-8") as f:
          f.write(content)
  На Windows это заменяет '\\n' на '\\r\\n', байты меняются, и хеш
  расходится: манифест ждёт 'd3923337...', файл даёт 'a61666d8...'.

ЗАМЕР (воспроизведено вызовом):
    хеш от строки:                     d39233370c5c37...  <- в манифесте
    файл, записанный текстово:         a61666d8bf6d47...  <- порча байтов
    файл, записанный двоично:          d39233370c5c37...  <- совпало
    файл с newline='':                 d39233370c5c37...  <- тоже совпало

ПРАВКА. Тест приводится в соответствие с реальным механизмом: файл
пишется так, чтобы байты не менялись. Два равнозначных способа:
    open(p, "wb") + content.encode("utf-8")
    open(p, "w", encoding="utf-8", newline="")
Выбран первый: он совпадает с тем, как это делает `_download_file`,
поэтому тест проверяет то же поведение, что и рабочий код.

ВАЖНО: правка теста НЕ ослабляет защиту. Проверка хеша осталась,
и она по-прежнему ловит настоящее расхождение — это проверяется
отдельным случаем.
"""

from __future__ import annotations

import ast
import pathlib
import shutil

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
TARGET = SAGA / "tests" / "test_updater_apply.py"
text = TARGET.read_text(encoding="utf-8")

OLD = '''def _write_staged(root, rel, content):
    p = os.path.join(pending_dir(root), rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)'''

NEW = '''def _write_staged(root, rel, content):
    """Write a staged file EXACTLY as the real updater stores it.

    The real downloader (`core/updater.py::_download_file`) writes the file
    in BINARY mode, so the bytes on disk are the bytes the manifest sha256
    was computed from. Writing it in text mode on Windows would translate
    '\\n' to '\\r\\n', change the bytes and make every apply fail with
    "sha256 mismatch" - which is a defect in the TEST, not in the applier.

    Keep this binary to stay faithful to production behaviour.
    """
    p = os.path.join(pending_dir(root), rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(content.encode("utf-8"))'''

if OLD not in text:
    if 'with open(p, "wb") as f:' in text:
        print("  правка уже внесена")
        raise SystemExit(0)
    raise SystemExit("НЕ НАЙДЕНА функция _write_staged")

backup = TARGET.with_suffix(".py.bak-staged")
shutil.copy2(TARGET, backup)
print(f"  бэкап: {backup.name}")

text = text.replace(OLD, NEW, 1)
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
    ('with open(p, "wb") as f:' in after, "файл пишется двоично"),
    ("content.encode(\"utf-8\")" in after, "кодировка задана явно"),
    ("остальные проверки не тронуты" if after.count("def test_") >= 10
     else "мало тестов", f"тестов в файле: {after.count('def test_')}"),
]
print()
for ok, label in checks:
    print(f"  [{'OK ' if ok else '--'}] {label}")
