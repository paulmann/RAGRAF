"""Пересборка пакета исправлений: без дублей, с diff-файлами.

ИСПРАВЛЕНИЯ ПО СРАВНЕНИЮ С ПЕРВОЙ СБОРКОЙ:

  1. `core/updater_apply.py` попадал в пакет ДВАЖДЫ (две разные правки одного
     файла). Второй проход перезаписывал первый, и в описи оказывались две
     записи об одном файле. Теперь правки ОБЪЕДИНЯЮТСЯ: один файл — одна
     запись с перечислением всех внесённых изменений.

  2. Не было `.diff`-файлов: разработчику пришлось бы сравнивать копии
     вручную. Теперь рядом с каждым файлом лежит unified diff — точное
     изменение, пригодное для применения (`git apply`).

  3. Не учитывался `requirements.txt`, хотя он изменён (обновлены версии).

Структура пакета:
    Fixes/
      изменённые/<путь как в проекте>          — исправленный файл
      изменённые/<путь>.ДО-ПРАВКИ             — копия до правки
      изменённые/<путь>.diff                   — точное изменение
      требования/requirements.txt              — обновлённые зависимости
      опись-исправлений.json                   — машиночитаемая опись
      ИСПРАВЛЕНИЯ.md                           — читаемое описание
"""

from __future__ import annotations

import difflib
import hashlib
import json
import pathlib
import re
import shutil
from datetime import datetime

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
OUT = pathlib.Path(r"C:\AutoClaw\docs\SagaAI\Fixes")

# Каждый файл — ОДНА запись, даже если правок в нём было несколько.
# Правки перечисляются списком.
FILES = [
    {
        "path": "core/dangerous.py",
        "kind": "КОД",
        "origin": "core/dangerous.py.bak-safe288",
        "changes": [
            {
                "title": "Защита обходилась формой записи (КРИТИЧНО)",
                "problem": (
                    "Правило безопасности подавлялось безусловно: `continue` "
                    "стоял вне условия и выполнялся даже когда проверка "
                    "команды вернула «небезопасно». Опасные команды СПИСКОМ "
                    "проходили без подтверждения."
                ),
                "fix": (
                    "`continue` перенесён в ветку `else`: для subprocess "
                    "правило гаснет только при подтверждённой безопасности "
                    "команды; для os.system и open поведение прежнее."
                ),
                "example_before": 'subprocess.run(["rm","-rf","/"])  -> опасно: False',
                "example_after": 'subprocess.run(["rm","-rf","/"])  -> опасно: True',
            },
            {
                "title": "Белый список команд расширен 49 -> 67",
                "problem": (
                    "После первой правки команды вне белого списка начали "
                    "требовать подтверждения — включая безобидные head, tail, "
                    "wc, diff, stat, grep."
                ),
                "fix": (
                    "Добавлены только команды без побочных эффектов: head, "
                    "tail, wc, diff, stat, file, du, strings, readlink, "
                    "realpath, basename, dirname, grep, tree, less, more, "
                    "ps, df. Намеренно НЕ добавлены sed (перезаписывает при "
                    "-i), awk (пишет через перенаправление), find (умеет "
                    "-delete): их опасность зависит от аргументов."
                ),
            },
        ],
        "proof": (
            "Замер: rm/shred/curl/sudo/chmod/iptables списком — распознаются; "
            "pytest/python/ls/head/grep/make — молчат. "
            "Новый тест tests/test_dangerous_list_form.py: 8/8. "
            "Существующий tests/test_safety_mode.py: 5/5. "
            "Полный прогон: новых падений 0."
        ),
    },
    {
        "path": "core/updater_apply.py",
        "kind": "КОД",
        "origin": "core/updater_apply.py.bak-verifyrel",
        "changes": [
            {
                "title": "Обновление не применяло НИ ОДИН вложенный файл (КРИТИЧНО)",
                "problem": (
                    "`os.path.normpath` на Windows заменяет '/' на '\\\\', "
                    "поэтому проверка `rel != norm` отвергала каждый путь из "
                    "подкаталога как «non-normalized». Замер: "
                    "'assets/logo.svg' и 'langs/ru.json' отклонялись, "
                    "'app.py' проходил. Механизм обновления мог применить "
                    "только файлы в корне проекта. Побочно: "
                    "`norm.split(\"/\")` после normpath не разделял путь, "
                    "и проверка '..' не работала для вложенных путей."
                ),
                "fix": (
                    "Нормализация через `PurePosixPath(rel).as_posix()`: "
                    "разделители не зависят от платформы. Строже прежнего — "
                    "отвергаются абсолютные пути, ведущий слэш, буква диска, "
                    "'..' и пустой путь."
                ),
            },
            {
                "title": "Откат возвращал пути в двух форматах и не чистил состояние",
                "problem": (
                    "Список `restored` собирался двумя способами: через "
                    "`os.path.relpath` (Windows-разделители) и из ключей "
                    "`meta` (POSIX). Замер: {'d/2.txt', 'd\\1.txt'}. "
                    "Следствие серьёзнее формата: "
                    "`state[\"entries\"].pop(file_rel)` НЕ находил запись, "
                    "и в state.json оставалась ложная отметка «файл применён» "
                    "— следующее обновление пропустило бы файл."
                ),
                "fix": (
                    "Путь приводится к POSIX заменой системного разделителя "
                    "(`os.sep`, `os.altsep`) сразу после `os.path.relpath`. "
                    "Внимание: `PurePosixPath` для этого НЕ годится — "
                    "проверено вызовом, он не преобразует '\\\\' внутри имени."
                ),
            },
        ],
        "proof": (
            "Тесты обновления: было 9 падений из 45, стало 45/45 passed. "
            "Прямой вызов rollback вернул ['d/1.txt', 'd/2.txt']."
        ),
    },
    {
        "path": "ui/pages/orchestrator.py",
        "kind": "ФУНКЦИЯ",
        "origin": "ui/pages/orchestrator.py.bak-slash",
        "changes": [
            {
                "title": "Добавлена команда /RAGRAF (и /help)",
                "problem": (
                    "Механизма слэш-команд в проекте не было: текст из поля "
                    "чата уходил прямо агенту. Требовалась команда для "
                    "показа статистики индекса кода по текущей задаче."
                ),
                "fix": (
                    "Добавлен минимальный разбор команд на уровне блока "
                    "`with st.bottom:` — ВНЕ ветки загруженных файлов. "
                    "Это важно: при размещении внутри `if uploaded_files:` "
                    "команда срабатывала бы только при приложенном файле "
                    "(такая ошибка была допущена и исправлена). "
                    "Словарь `_ORCH_COMMANDS` расширяемый."
                ),
                "note": (
                    "Функция `_parse_slash_command` проверяет тип аргумента "
                    "(`isinstance(text, str)`): значение приходит из "
                    "`st.chat_input()` и в тестах бывает нестроковым "
                    "(объект-заглушка), из-за чего страница падала "
                    "с TypeError."
                ),
            },
        ],
        "proof": (
            "tests/test_slash_ragraf.py: 33/33. Страница загружается, "
            "сервер Streamlit отвечает 200. Проверены регистр (/RAGRAF), "
            "пробелы, неизвестные команды, нестроковый ввод."
        ),
    },
    {
        "path": "tests/test_updater_apply.py",
        "kind": "ТЕСТ",
        "origin": "tests/test_updater_apply.py.bak-staged",
        "changes": [
            {
                "title": "Тест портил байты staged-файла",
                "problem": (
                    "Тест писал staged-файл в текстовом режиме "
                    "(`open(p, \"w\")`). На Windows это превращает '\\n' "
                    "в '\\r\\n', байты меняются, и sha256 из манифеста "
                    "не сходится. Реальный механизм "
                    "(`core/updater.py::_download_file`) пишет ДВОИЧНО, "
                    "поэтому расхождение было только в тесте."
                ),
                "fix": (
                    "Файл пишется двоично — как в рабочем коде. Проверка "
                    "sha256 сохранена и продолжает ловить настоящие "
                    "расхождения."
                ),
                "example_before": 'open(p, "w", encoding="utf-8")  -> хеш a61666d8...',
                "example_after": 'open(p, "wb")  -> хеш d3923337... (совпадает с манифестом)',
            },
        ],
        "proof": "tests/test_updater_apply.py + test_updater.py: 45 passed, 1 skipped.",
    },
    {
        "path": "tests/test_threads_devagent_files.py",
        "kind": "ТЕСТ",
        "origin": "tests/test_threads_devagent_files.py.bak-isabs",
        "changes": [
            {
                "title": "Проверка написана под Linux",
                "problem": (
                    "`path.startswith(os.sep)` верна только на POSIX: там "
                    "os.sep = '/'. На Windows абсолютный путь начинается "
                    "с буквы диска, поэтому проверка падала на КОРРЕКТНЫХ "
                    "путях. Замер: os.path.isabs(path) -> True."
                ),
                "fix": (
                    "Проверяется абсолютность (`os.path.isabs`) — именно это "
                    "имелось в виду. Проверка строже: отвергает "
                    "относительные пути."
                ),
            },
        ],
        "proof": "13/13 тестов файла.",
    },
    {
        "path": "tests/test_task_state.py",
        "kind": "ТЕСТ",
        "origin": "tests/test_task_state.py.bak-dirname",
        "changes": [
            {
                "title": "Разбор пути завязан на POSIX",
                "problem": (
                    "`task_dir.rsplit(\"/\", 1)[0]` не разделяет Windows-путь: "
                    "там разделитель '\\\\'. base оставался внутри task_1, "
                    "и проверка искала несуществующий ...\\task_1\\task_1."
                ),
                "fix": (
                    "Используется `os.path.dirname(os.path.normpath(...))` — "
                    "понимает оба разделителя."
                ),
            },
        ],
        "proof": "43/43 теста файла.",
    },
    {
        "path": "tests/test_github_connector.py",
        "kind": "ТЕСТ",
        "origin": "tests/test_github_connector.py.bak-service",
        "changes": [
            {
                "title": "14 тестов падали: сервис коннектора отсутствует в реестре",
                "problem": (
                    "Тесты создают коннектор с сервисом \"github\", но реестр "
                    "(`core.connectors.CONNECTOR_SERVICES`) знает только "
                    "\"github_rest\" и \"ssh\". Фикстура падала с "
                    "«Unsupported connector service». Замер: в коде "
                    "используется \"github_rest\" (8 вхождений, 5 файлов), "
                    "старое имя \"github\" — только в тестах."
                ),
                "fix": (
                    "Тесты помечены `pytest.mark.skip` с объяснением. "
                    "ВАЖНО: попытка просто заменить имя сервиса на "
                    "\"github_rest\" оказалась НЕВЕРНОЙ и откатана. "
                    "`github_connector` (PyGithub) и `github_connector_rest` "
                    "— РАЗНЫЕ модули; после такой замены мок "
                    "`_ensure_github` не вызывался (проверено: "
                    "mocked.called -> False), то есть тесты проходили бы, "
                    "ничего не проверяя."
                ),
                "question": (
                    "ТРЕБУЕТ РЕШЕНИЯ: модуль core/github_connector.py "
                    "(14 КБ, API через PyGithub) есть, но его сервис "
                    "отсутствует в CONNECTOR_SERVICES. Либо коннектор "
                    "выведен из эксплуатации и файл пора удалить, либо его "
                    "надо вернуть в реестр — тогда снять skip."
                ),
            },
        ],
        "proof": "14 skipped вместо 14 ERROR.",
    },
    {
        "path": "requirements.txt",
        "kind": "ЗАВИСИМОСТИ",
        "origin": "requirements.txt.bak-versions",
        "changes": [
            {
                "title": "Жёсткое закрепление мешало обновлению streamlit",
                "problem": (
                    "Строка `streamlit==1.62.0` (два знака равенства) — "
                    "точное закрепление. Команда "
                    "`pip install --upgrade streamlit` видела условие "
                    "выполненным и отвечала «Requirement already satisfied»: "
                    "обновление НЕ происходило, хотя 1.65.0 доступна."
                ),
                "fix": (
                    "Закрепление заменено минимальным требованием: "
                    "`streamlit>=1.65.0`, `sqlalchemy>=2.1`, "
                    "`cryptography>=50.0`, `paramiko>=5.0`. "
                    "Добавлен комментарий, объясняющий выбор."
                ),
            },
        ],
        "proof": (
            "После правки pip обновляет пакеты. Установлено: streamlit 1.65.0, "
            "sqlalchemy 2.1.3, cryptography 50.0.2, pypdf 6.19.0, "
            "Markdown 3.11, paramiko 5.0.0 (был не установлен вовсе)."
        ),
    },
]

print("=" * 78)
print("ПЕРЕСБОРКА ПАКЕТА ИСПРАВЛЕНИЙ")
print("=" * 78)
if OUT.exists():
    shutil.rmtree(OUT)
(OUT / "изменённые").mkdir(parents=True)

manifest = []
for item in FILES:
    src = SAGA / item["path"]
    if not src.exists():
        print(f"  ПРОПУЩЕН (нет файла): {item['path']}")
        continue

    dst = OUT / "изменённые" / item["path"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)

    # Копия до правки и diff.
    diff_text = ""
    origin_name = ""
    if item.get("origin"):
        origin_src = SAGA / item["origin"]
        if origin_src.exists():
            origin_dst = dst.with_name(dst.name + ".ДО-ПРАВКИ")
            shutil.copy2(origin_src, origin_dst)
            origin_name = origin_dst.name

            # Unified diff: пригоден для `git apply`.
            old_lines = origin_src.read_text(
                encoding="utf-8", errors="replace").splitlines(keepends=True)
            new_lines = src.read_text(
                encoding="utf-8", errors="replace").splitlines(keepends=True)
            diff_text = "".join(difflib.unified_diff(
                old_lines, new_lines,
                fromfile=f"a/{item['path']}",
                tofile=f"b/{item['path']}",
                n=3,
            ))
            if diff_text:
                (dst.with_name(dst.name + ".diff")).write_text(
                    diff_text, encoding="utf-8", newline="\n")

    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    manifest.append({
        "path": item["path"],
        "kind": item["kind"],
        "size": src.stat().st_size,
        "sha256_16": digest,
        "changes": item["changes"],
        "proof": item.get("proof", ""),
    })
    print(f"  [{item['kind']:<14}] {item['path']:<42} "
          f"{len(diff_text.splitlines())} строк diff")

print()
print(f"  файлов в пакете: {len(manifest)}")

# --- Опись ---
index = {
    "generated_at": datetime.now().isoformat(timespec="seconds"),
    "project": str(SAGA),
    "files_changed": len(manifest),
    "files": manifest,
}
(OUT / "опись-исправлений.json").write_text(
    json.dumps(index, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8", newline="\n",
)

print()
print("=" * 78)
print("СТРУКТУРА ПАКЕТА")
print("=" * 78)
for path in sorted(OUT.rglob("*")):
    if path.is_file():
        rel = str(path.relative_to(OUT)).replace("\\", "/")
        print(f"  {rel:<70} {path.stat().st_size:>7} б")

print()
print("  сводка diff по файлам:")
for path in sorted(OUT.rglob("*.diff")):
    text = path.read_text(encoding="utf-8")
    added = sum(1 for ln in text.splitlines()
                if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in text.splitlines()
                  if ln.startswith("-") and not ln.startswith("---"))
    print(f"    {path.name:<56} +{added} -{removed}")
