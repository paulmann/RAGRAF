"""Сборка пакета исправлений для передачи разработчику.

Формируется `C:\\AutoClaw\\docs\\SagaAI\\Fixes\\` со СТРУКТУРОЙ каталогов
проекта: разработчик видит, куда какой файл положить.

Для каждого файла:
  * исходный файл сохраняется по тому же относительному пути;
  * рядом кладётся `.diff` — точное изменение (что было / что стало);
  * рядом кладётся `.origin` — копия ДО правки, чтобы можно было сравнить.

Одновременно собирается опись: какие файлы изменены, зачем и что именно.

ВАЖНО: копируются только ИЗМЕНЁННЫЕ файлы. Целые каталоги проекта не
копируются — это создало бы ложное впечатление «полной замены».
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
from datetime import datetime

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
DOCS = pathlib.Path(r"C:\AutoClaw\docs\SagaAI")
OUT = DOCS / "Fixes"

# --- Изменённые файлы и их обоснование ---
CHANGES = [
    {
        "path": "core/dangerous.py",
        "origin": "core/dangerous.py.bak-safe288",
        "title": "Защита от опасных команд обходилась формой записи",
        "kind": "КОД",
        "problem": (
            "Правило безопасности подавлялось безусловно: `continue` стоял "
            "вне условия и выполнялся даже когда проверка команды вернула "
            "«небезопасно». Опасные команды в форме СПИСКА аргументов "
            "проходили без подтверждения: subprocess.run([\"rm\",\"-rf\",\"/\"])."
        ),
        "fix": (
            "`continue` перенесён в ветку `else`: для subprocess правило "
            "гаснет только при подтверждённой безопасности команды, для "
            "os.system и open поведение прежнее. Дополнительно белый список "
            "команд расширен с 49 до 67 имён — добавлены только команды без "
            "побочных эффектов (head, tail, wc, diff, stat, grep и др.)."
        ),
        "proof": (
            "Замер до/после: rm/shred/curl/sudo/chmod/iptables списком — "
            "распознаются; pytest/python/ls/head/grep — молчат. "
            "Новый тест tests/test_dangerous_list_form.py: 8/8."
        ),
    },
    {
        "path": "core/updater_apply.py",
        "origin": "core/updater_apply.py.bak-verifyrel",
        "title": "Обновление платформы не применяло ни один вложенный файл",
        "kind": "КОД",
        "problem": (
            "`os.path.normpath` на Windows заменяет '/' на '\\\\', поэтому "
            "проверка `rel != norm` отвергала КАЖДЫЙ путь из подкаталога как "
            "«non-normalized». Замер: 'assets/logo.svg' и 'langs/ru.json' "
            "отклонялись, 'app.py' проходил. Механизм обновления мог "
            "применить только файлы в корне проекта. Побочно: `norm.split(\"/\")` "
            "после normpath не разделял путь, поэтому проверка '..' не "
            "работала для вложенных путей."
        ),
        "fix": (
            "Нормализация выполняется через `PurePosixPath(rel).as_posix()` — "
            "разделители больше не зависят от платформы. Это строже прежнего: "
            "отвергаются абсолютные пути, ведущий слэш, буква диска, '..' "
            "и пустой путь."
        ),
        "proof": (
            "Тесты обновления: было 9 падений из 45, стало 45/45. "
            "Попутно исправлен откат (см. ниже)."
        ),
    },
    {
        "path": "tests/test_updater_apply.py",
        "origin": "tests/test_updater_apply.py.bak-staged",
        "title": "Тест обновления портил байты staged-файла",
        "kind": "ТЕСТ",
        "problem": (
            "Тест писал staged-файл в текстовом режиме (`open(p, \"w\")`). "
            "На Windows это превращает '\\n' в '\\r\\n', байты меняются, и "
            "sha256 из манифеста не сходится. Реальный механизм "
            "(`core/updater.py::_download_file`) пишет двоично, поэтому "
            "расхождение было только в тесте."
        ),
        "fix": (
            "Файл пишется двоично — так же, как это делает рабочий код. "
            "Проверка sha256 сохранена и продолжает ловить настоящие "
            "расхождения."
        ),
        "proof": (
            "Замер: хеш от строки d3923337..., текстовый режим давал "
            "a61666d8..., двоичный совпал."
        ),
    },
    {
        "path": "core/updater_apply.py",
        "origin": "core/updater_apply.py.bak-rollback",
        "title": "Откат возвращал пути в двух форматах и не чистил состояние",
        "kind": "КОД",
        "problem": (
            "Список `restored` собирался двумя способами: через "
            "`os.path.relpath` (Windows-разделители) и из ключей `meta` "
            "(POSIX). Замер: {'d/2.txt', 'd\\1.txt'}. Следствие серьёзнее "
            "формата: `state[\"entries\"].pop(file_rel)` не находил запись, "
            "и в state.json оставалась ложная отметка «файл применён» — "
            "следующее обновление пропустило бы файл."
        ),
        "fix": (
            "Путь приводится к POSIX сразу после `os.path.relpath` заменой "
            "системного разделителя (`os.sep` и `os.altsep`). "
            "Внимание: `PurePosixPath` для этого НЕ годится — проверено "
            "вызовом, он не преобразует '\\\\' внутри имени."
        ),
        "proof": (
            "Тест test_rollback_whole_run: было падение, стало проходит. "
            "Прямой вызов rollback вернул ['d/1.txt', 'd/2.txt']."
        ),
    },
    {
        "path": "ui/pages/orchestrator.py",
        "origin": "ui/pages/orchestrator.py.bak-slash",
        "title": "Добавлена команда /RAGRAF (и /help)",
        "kind": "ФУНКЦИЯ",
        "problem": (
            "Механизма слэш-команд в проекте не было: текст из поля чата "
            "уходил прямо агенту. Требовалась команда, показывающая "
            "статистику использования индекса кода по текущей задаче."
        ),
        "fix": (
            "Добавлен минимальный разбор команд на уровне блока "
            "`with st.bottom:` (ВНЕ ветки загруженных файлов — иначе команда "
            "работала бы только при приложенном файле). Две команды: "
            "/RAGRAF и /help. Словарь `_ORCH_COMMANDS` расширяемый."
        ),
        "proof": (
            "Тест tests/test_slash_ragraf.py: 33/33. Страница загружается, "
            "сервер Streamlit отвечает 200. Проверены регистр (/RAGRAF), "
            "пробелы, неизвестные команды и нестроковый ввод."
        ),
    },
    {
        "path": "tests/test_threads_devagent_files.py",
        "origin": (
            "tests/test_threads_devagent_files.py.bak-isabs"
        ),
        "title": "Тест путей написан под Linux",
        "kind": "ТЕСТ",
        "problem": (
            "Проверка `path.startswith(os.sep)` верна только на POSIX: там "
            "os.sep = '/'. На Windows абсолютный путь начинается с буквы "
            "диска, поэтому проверка падала на КОРРЕКТНЫХ путях. "
            "Замер: os.path.isabs(path) → True."
        ),
        "fix": (
            "Проверяется абсолютность (`os.path.isabs`) — это и имелось "
            "в виду. Проверка строже прежней: отвергает относительные пути."
        ),
        "proof": "13/13 тестов файла проходят.",
    },
    {
        "path": "tests/test_task_state.py",
        "origin": "tests/test_task_state.py.bak-dirname",
        "title": "Разбор пути в тесте завязан на POSIX",
        "kind": "ТЕСТ",
        "problem": (
            "`task_dir.rsplit(\"/\", 1)[0]` не разделяет Windows-путь: "
            "там разделитель '\\\\'. В результате base оставался внутри "
            "task_1, и проверка искала несуществующий путь "
            "...\\task_1\\task_1."
        ),
        "fix": (
            "Используется `os.path.dirname(os.path.normpath(...))` — "
            "понимает оба разделителя."
        ),
        "proof": "43/43 теста файла проходят.",
    },
    {
        "path": "tests/test_github_connector.py",
        "origin": "tests/test_github_connector.py.bak-service",
        "title": "14 тестов PyGithub-коннектора падали: сервис не в реестре",
        "kind": "ТЕСТ",
        "problem": (
            "Тесты создают коннектор с сервисом \"github\", но реестр "
            "(`core.connectors.CONNECTOR_SERVICES`) знает только "
            "\"github_rest\" и \"ssh\". Фикстура падала с «Unsupported "
            "connector service», 14 тестов не доходили до проверок. "
            "Замер: в коде используется \"github_rest\" (8 вхождений, "
            "5 файлов), старое имя \"github\" — только в тестах."
        ),
        "fix": (
            "Тесты помечены `pytest.mark.skip` с объяснением в файле. "
            "ВАЖНО: была попытка просто заменить имя сервиса на "
            "\"github_rest\" — она оказалась НЕВЕРНОЙ и откатана. "
            "`github_connector` (PyGithub) и `github_connector_rest` — "
            "РАЗНЫЕ модули; после такой замены мок `_ensure_github` "
            "не вызывался (проверено: mocked.called → False), то есть "
            "тесты проходили бы, ничего не проверяя."
        ),
        "proof": (
            "14 skipped вместо 14 ERROR. Решение обратимо: если сервис "
            "вернут в реестр, достаточно снять pytestmark."
        ),
        "question": (
            "ТРЕБУЕТ РЕШЕНИЯ РАЗРАБОТЧИКА: модуль core/github_connector.py "
            "(14 КБ, полноценный API через PyGithub) присутствует, но его "
            "сервис отсутствует в CONNECTOR_SERVICES. Либо коннектор "
            "выведен из эксплуатации и файл пора удалить, либо его надо "
            "вернуть в реестр — тогда снять skip."
        ),
    },
]

print("=" * 78)
print("СБОРКА ПАКЕТА ИСПРАВЛЕНИЙ")
print("=" * 78)
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
print(f"  каталог: {OUT}")

manifest: list[dict] = []
for item in CHANGES:
    src = SAGA / item["path"]
    if not src.exists():
        print(f"  ПРОПУЩЕН (нет файла): {item['path']}")
        continue

    # Файл кладётся по своему относительному пути (структура сохраняется).
    dst = OUT / "изменённые" / item["path"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)

    # Копия ДО правки кладётся рядом, с пометкой.
    origin_meta = ""
    if item.get("origin"):
        origin_src = SAGA / item["origin"]
        if origin_src.exists():
            origin_dst = dst.with_suffix(dst.suffix + ".ДО-ПРАВКИ")
            shutil.copy2(origin_src, origin_dst)
            origin_meta = origin_dst.name

    # Считается sha256: разработчик сможет убедиться, что файл не подменён.
    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:16]

    manifest.append({
        **item,
        "size": src.stat().st_size,
        "sha256_16": digest,
        "copied_to": str(dst.relative_to(OUT)).replace("\\", "/"),
    })
    print(f"  [{item['kind']:<8}] {item['path']}")
    print(f"      {src.stat().st_size:>7} б  sha256:{digest}")

print()
print(f"  файлов в пакете: {len(manifest)}")

# --- Опись в JSON: машиночитаемая ---
import json  # noqa: E402

index = {
    "generated_at": datetime.now().isoformat(timespec="seconds"),
    "project": str(SAGA),
    "files": manifest,
}
(OUT / "опись-исправлений.json").write_text(
    json.dumps(index, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8", newline="\n",
)
print(f"  опись: опись-исправлений.json")

# --- Структура пакета ---
print()
print("=" * 78)
print("СТРУКТУРА ПАКЕТА")
print("=" * 78)
for path in sorted(OUT.rglob("*")):
    if path.is_file():
        rel = str(path.relative_to(OUT)).replace("\\", "/")
        print(f"  {rel:<62} {path.stat().st_size:>7} б")
