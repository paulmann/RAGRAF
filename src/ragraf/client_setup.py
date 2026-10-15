"""Автоматическая настройка клиентов RAGRAF: точные значения и применение.

Что делает модуль
    Готовит для каждого клиента ровно то, что нужно ввести или применить, и там,
    где это безопасно, применяет сам:

      * **AutoClaw** — значения для формы «Add tool»: имя, URL, значение доступа.
        Применять НЕ может и не пытается: AutoClaw регистрирует MCP-серверы только
        через свой интерфейс, а единственное место хранения (`runtime.sqlite`,
        таблица `governed_connected_sources`) принадлежит продукту. Запись туда в
        обход продукта обходит учёт прав и артефактов (`governed_tool_*`), поэтому
        база только читается.
      * **DeepSeek Harness** — блок профиля. Применяется по-настоящему: панель
        добавляет запись в `cordis.patch.yml` профиля, снимает резервную копию и
        проверяет, что патч разбирается. Предусмотрены два транспорта:
        `stdio` (как сейчас) и `streamable-http`.
      * **SagaAI** — только проверка. Файл инструмента лежит в чужом проекте
        (`orchestrators/dev_agent/functions/ragraf.py`), и панель RAGRAF не имеет
        права его править без прямого указания владельца. Показывается состояние
        и переменные, которыми инструмент управляется.

Почему значения формируются, а не «угадываются»
    Каждое значение проверено запуском: команда запуска stdio-сервера, адрес
    HTTP-сервиса, порты, интерпретатор. Запуск идёт интерпретатором **из `.venv`
    проекта**: внешний клиент передаёт дочернему процессу урезанное окружение, и
    зависимости RAGRAF достижимы только так (измерено ранее).

Автор: Михаил Дейнекин
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
VENV_PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
SERVER = REPO / "tools" / "ragraf-mcp.py"
HTTP_SERVER = REPO / "tools" / "ragraf-mcp-http.py"
ADMIN_CONFIG = REPO / "config" / "ragraf.yaml"

USER_HOME = Path(os.path.expanduser("~"))
DSH_HOME = Path(os.environ.get("DSH_HOME") or (USER_HOME / ".dsh"))
DSH_PATCH = DSH_HOME / "profiles" / "desktop" / "cordis.patch.yml"

# Расположение инструмента SagaAI задаётся настройкой окружения: инструмент
# живёт в ЧУЖОМ проекте, и угадывать локальный путь нельзя — на другой машине он
# другой. Пустое значение означает «не заявлен», и состояние сообщается как
# «не найден», а не выдумывается.
_saga_env = os.environ.get("RAGRAF_SAGA_ROOT") or ""
SAGA_ROOT: Path | None = Path(_saga_env) if _saga_env else None
SAGA_TOOL: Path | None = (
    SAGA_ROOT / "orchestrators" / "dev_agent" / "functions" / "ragraf.py"
    if SAGA_ROOT else None
)

HTTP_PORT = 8765
MARK_BEGIN = "# == RAGRAF: подключение MCP (добавлено панелью RAGRAF)"
MARK_END = "# == конец блока RAGRAF"


def interpreter() -> str:
    """Интерпретатор для запуска сервера.

    Именно `.venv` проекта: внешний клиент передаёт урезанное окружение (только
    PATH/TEMP/APPADATA и подобное), и зависимости RAGRAF из пользовательского
    каталога пакетов становятся недостижимы. Замерено ранее: `.venv` самодостаточен.
    """
    if VENV_PYTHON.is_file():
        return str(VENV_PYTHON)
    return sys.executable or "python"


# ---------------------------------------------------------------------------
# Формирование значений
# ---------------------------------------------------------------------------


def env_lines_local() -> str:
    """Переменные окружения для типа «Local program» в AutoClaw.

    Поля «рабочий каталог» в форме НЕТ, а корпус и имя агента задать надо:
    клиент передаёт окружение процессу, поэтому значения идут строками
    `KEY=value` — по одной в строке (так их и ждёт форма).
    """
    return "\n".join([
        f"RAGRAF_PROJECT={ADMIN_CONFIG}",
        "RAGRAF_AGENT=AutoClaw",
        "RAGRAF_AGENT_KIND=autoclaw",
    ])


def autoclaw_values(access_hint: str = "") -> dict[str, Any]:
    """Значения для формы «Add tool» в AutoClaw — ОБЕ формы подключения.

    Владелец спросил 15.10.2026: «а если я хочу через Local program, а не Online
    service?» Прежде блок описывал только Online service, хотя AutoClaw умеет и
    локальную программу: тогда клиент запускает stdio-процесс сам, и ни порт, ни
    значение доступа не нужны. Обе формы описаны и показываются в панели.

    Ключи `how`/`fields`/`before`/`access_note` оставлены как были: они отвечают
    форме Online service, и их читают проверки проекта.
    """
    online = {
        "type": "Online service",
        "transport": "streamable-http",
        "how": ("Settings → Tools & connections → Add tool. Type: Online service. "
                "AutoClaw сам подключается к сервису по адресу ниже: сервис должен "
                "быть запущен, иначе подключение не состоится."),
        "fields": [
            {"label": "Name", "value": "RAGRAF"},
            {"label": "Type", "value": "Online service"},
            {"label": "Service URL", "value": f"http://127.0.0.1:{HTTP_PORT}/mcp"},
            {"label": "Bearer token", "value": "значение из раздела «Доступ»"},
        ],
        "before": [f"поднимите HTTP-сервис: {interpreter()} {HTTP_HTTP_HINT}"],
        "access_note": ("значение доступа выдаётся в разделе «Доступ»: создайте "
                        "отдельный токен с именем AutoClaw и вставьте в поле "
                        "Bearer token — показывается один раз"),
        "contact": "online",
    }
    local = {
        "type": "Local program",
        "transport": "stdio",
        "how": ("Settings → Tools & connections → Add tool. Type: Local program. "
                "Процесс запускает сам AutoClaw, поэтому порт и значение доступа "
                "не нужны, а сервис поднимать не надо. Поля «рабочий каталог» "
                "в форме НЕТ — вместо него переменные окружения ниже, и именно "
                "они задают корпус и имя агента."),
        "fields": [
            {"label": "Name", "value": "RAGRAF"},
            {"label": "Type", "value": "Local program"},
            {"label": "Launch command", "value": interpreter()},
            {"label": "Arguments", "value": str(SERVER)},
            {"label": "Environment variables",
             "value": env_lines_local()},
        ],
        "before": [],
        "access_note": ("доступ по токену относится только к HTTP-сервису: при "
                        "локальном запуске клиент сам владеет процессом"),
        "contact": "local",
    }
    return {
        "client": "autoclaw",
        "title": "AutoClaw",
        "how": ("Settings → Tools & connections → Add tool. Type: Online service. "
                "Значения ниже вводятся руками: AutoClaw регистрирует MCP-серверы "
                "только через свой интерфейс, файла конфигурации для них нет."),
        "fields": [
            {"label": "Name", "value": "RAGRAF"},
            {"label": "Type", "value": "Online service"},
            {"label": "Service URL", "value": f"http://127.0.0.1:{HTTP_PORT}/mcp"},
        ],
        "before": [
            f"поднимите HTTP-сервис: {interpreter()} {HTTP_HTTP_HINT}",
        ],
        "access_note": ("значение доступа выдаётся в разделе «Доступ»: создайте "
                        "отдельный токен с именем AutoClaw и вставьте в поле "
                        "Bearer token — показывается один раз"),
        "access_hint": access_hint,
        "variants": [online, local],
    }


HTTP_HTTP_HINT = (f"{HTTP_SERVER} --port={HTTP_PORT} "
                  f"--project {ADMIN_CONFIG} --agent AutoClaw --agent-kind autoclaw")


def harness_block(transport: str = "stdio", access_hint: str = "") -> dict[str, Any]:
    """Блок профиля DeepSeek Harness для одного из двух транспортов."""
    transport = transport if transport in ("stdio", "streamable-http") else "stdio"
    if transport == "stdio":
        entry = {
            "id": "mcp-ragraf",
            "name": "@deepseek-ai/dsh-mcp-client",
            "config": {
                "serverName": "ragraf",
                "transport": "stdio",
                "command": interpreter(),
                "args": [str(SERVER), "--agent", "DeepSeek Harness",
                         "--agent-kind", "external"],
                "cwd": str(REPO),
                "toolCallTimeoutMs": 300000,
                "failOnStartupError": False,
            },
        }
        note = ("stdio не требует ни порта, ни значения доступа: клиент запускает "
                "процесс сам. Корпус определяется по рабочему каталогу.")
    else:
        entry = {
            "id": "mcp-ragraf-http",
            "name": "@deepseek-ai/dsh-mcp-client",
            "config": {
                "serverName": "ragraf",
                "transport": "streamable-http",
                "url": f"http://127.0.0.1:{HTTP_PORT}/mcp",
                "headers": {"Authorization": f"Bearer {access_hint}"},
                "toolCallTimeoutMs": 300000,
                "failOnStartupError": False,
            },
        }
        note = ("streamable-http требует запущенного HTTP-сервиса и значения "
                "доступа: подставьте его вместо заглушки. Порт по умолчанию "
                f"{HTTP_PORT}.")
    return {
        "client": "harness",
        "title": "DeepSeek Harness",
        "transport": transport,
        "entry": entry,
        "path": str(DSH_PATCH),
        "note": note,
        "applies": True,
    }


def sagaai_state() -> dict[str, Any]:
    """Состояние инструмента SagaAI: только чтение, без правок."""
    exists = bool(SAGA_TOOL) and SAGA_TOOL.is_file()
    config = ""
    if exists:
        text = SAGA_TOOL.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'RAGRAF_CONFIG",\s*r?"([^"]+)"', text)
        config = str(m.group(1)) if m else ""
    return {
        "client": "sagaai",
        "title": "SagaAI",
        "tool_path": str(SAGA_TOOL or ""),
        "tool_exists": exists,
        "config": config,
        "config_exists": bool(config) and Path(config).is_file(),
        "variables": ["RAGRAF_ROOT", "RAGRAF_CONFIG", "RAGRAF_AGENT",
                      "RAGRAF_AGENT_KIND", "RAGRAF_TASK"],
        "note": ("инструмент лежит в ЧУЖОМ проекте, и панель его не правит: "
                 "показывается фактическое состояние и переменные, которыми он "
                 "управляется. Правка — по отдельному указанию владельца."),
        "applies": False,
    }


# ---------------------------------------------------------------------------
# Применение блока Harness
# ---------------------------------------------------------------------------


def harness_apply(transport: str = "stdio") -> dict[str, Any]:
    """Добавить или заменить блок RAGRAF в профиле Harness.

    Блок обрамляется метками: повторное применение заменяет его, а не добавляет
    второй (иначе в профиле оказались бы два сервера с одним именем, и клиент
    отверг бы загрузку). Перед записью снимается резервная копия, после записи
    профиль проверяется разбором — для этого вызывается `dsh --dump-config`.
    """
    if not DSH_PATCH.parent.is_dir():
        return {"ok": False, "error": "каталог профиля Harness не найден",
                "action": "проверьте, установлен ли DeepSeek Harness"}
    transport = transport if transport in ("stdio", "streamable-http") else "stdio"
    block = harness_block(transport)
    yaml_block = _render_patch_yaml(block["entry"])
    text = DSH_PATCH.read_text(encoding="utf-8") if DSH_PATCH.is_file() else ""

    # Удаляются ОБА вида прежних блоков, иначе в профиле окажутся два сервера с
    # одним именем, и клиент отвергнет загрузку (проверено: `serverName` обязан
    # быть уникальным внутри области регистрации).
    #
    #  1. блок, добавленный панелью — он обрамлён метками;
    #  2. блок, добавленный ранее другим способом (инструкцией) — меток нет,
    #     но запись видна по `id: mcp-ragraf` и её настройкам.
    tagged = re.compile(
        re.escape(MARK_BEGIN) + r".*?" + re.escape(MARK_END) + r"\s*", re.S)
    text = tagged.sub("", text)

    # Немаркированный блок: запись видна по нашему id. Удаляется от строки
    # `insert:` (список вставки, ровно так его пишет профиль) до конца записи —
    # то есть до строки, которая начинается с `- ` на верхнем уровне (следующая
    # запись) или до конца текста.
    #
    # Первая редакция требовала, чтобы строка `- insert:` шла непосредственно
    # перед записью, и НЕ находила блок в реальном профиле: там `insert:`
    # отделён от записи (измерено на живом файле). Из-за этого повторное
    # применение оставляло прежний блок и добавляло второй — а два сервера с
    # одним именем клиент отвергает.
    loose = re.compile(
        r"(?ms)^(?:#[^\n]*\n)?- insert:\n"
        r"(?:(?!^- insert:)[\s\S])*?"
        r"^    - id: mcp-ragraf(?:-http)?\n"
        r"(?:(?!^- insert:)[\s\S])*?"
        r"(?=^- insert:|^- [a-z]|\Z)")
    text = loose.sub("", text)

    new_text = text.rstrip() + "\n\n" + MARK_BEGIN + "\n" + yaml_block + MARK_END + "\n"

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = DSH_PATCH.with_name(f"{DSH_PATCH.name}.bak-{stamp}-panel")
    try:
        DSH_PATCH.parent.mkdir(parents=True, exist_ok=True)
        if DSH_PATCH.is_file():
            shutil.copy2(DSH_PATCH, backup)
        io.open(DSH_PATCH, "w", encoding="utf-8", newline="\n").write(new_text)
    except Exception as exc:
        return {"ok": False, "error": f"запись не удалась: {type(exc).__name__}",
                "action": "проверьте права на каталог профиля"}

    check = _verify_patch()
    result = {
        "ok": check["ok"],
        "transport": transport,
        "path": str(DSH_PATCH),
        "backup": str(backup) if backup.is_file() else "",
        "check": check,
        "note": ("приложение перечитает профиль при следующем запуске: до "
                 "перезапуска Harness блок в работу не вступит"),
    }
    if not check["ok"]:
        # Откат: непроверенный профиль хуже прежнего.
        try:
            if backup.is_file():
                shutil.copy2(backup, DSH_PATCH)
                result["reverted"] = True
        except Exception:
            pass
        result["action"] = "профиль возвращён к прежнему виду: проверка не прошла"
    return result


def _render_patch_yaml(entry: dict[str, Any]) -> str:
    """Запись одного MCP-сервера в виде YAML-патча профиля Harness."""
    lines = ["- insert:", f"    - id: {entry['id']}",
             f"      name: '{entry['name']}'", "      config:"]
    for key, value in entry["config"].items():
        if isinstance(value, bool):
            lines.append(f"        {key}: {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"        {key}: {value}")
        elif isinstance(value, list):
            lines.append(f"        {key}:")
            for item in value:
                lines.append(f"          - {_quote(item)}")
        elif isinstance(value, dict):
            lines.append(f"        {key}:")
            for sub_key, sub_value in value.items():
                lines.append(f"          {sub_key}: {_quote(sub_value)}")
        else:
            lines.append(f"        {key}: {_quote(value)}")
    return "\n".join(lines) + "\n"


def _quote(value: Any) -> str:
    text = str(value)
    if text.startswith("Bearer ") or any(ch in text for ch in ":\\/ "):
        return "'" + text.replace("'", "''") + "'"
    return text


def _verify_patch() -> dict[str, Any]:
    """Проверить профиль тем же разбором, что использует приложение.

    Вызывается `dsh --profile headless --patch <наша копия> --dump-config`:
    приложение разбирает патч и печатает результат. Если блок не разбирается,
    это видно до перезапуска — а не после.
    """
    dsh = _find_dsh()
    if not dsh:
        return {"ok": True, "skipped": "команда dsh не найдена — разбор не проверен"}
    tmp = Path(os.environ.get("TEMP") or ".") / "ragraf-patch-verify.yml"
    try:
        text = DSH_PATCH.read_text(encoding="utf-8")
        m = re.search(re.escape(MARK_BEGIN) + r"(.*?)" + re.escape(MARK_END),
                      text, re.S)
        if not m:
            return {"ok": False, "reason": "блок не найден в профиле"}
        io.open(tmp, "w", encoding="utf-8", newline="\n").write(m.group(1))
        proc = subprocess.run(
            [str(dsh), "--profile", "headless", "--patch", str(tmp), "--dump-config"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        # Критерий — не слово «error» в тексте: оно встречается в описаниях
        # промптов внутри вывода, и первая редакция проверки считала годный патч
        # сломанным (измерено). Проверяется именно РЕЗУЛЬТАТ: блок попал в
        # собранное дерево профиля и команда завершилась успешно.
        # Проверка идёт ПО СТРОКАМ, а не подстрокой: имя `failOnStartupError`
        # содержит «rror:», и поиск подстроки объявлял годный патч сломанным
        # (измерено). Ошибкой считается строка, которая НАЧИНАЕТСЯ с `error`
        # или с префикса ошибки загрузчика.
        bad = [ln for ln in out.splitlines()
               if ln.strip().lower().startswith(("error", "failed to", "invalid "))]
        ok = ("serverName: ragraf" in out and proc.returncode == 0 and not bad)
        last = out.strip().splitlines()[-1][:160] if out.strip() else "нет вывода"
        return {"ok": ok, "detail": ("патч разбирается приложением" if ok else last),
                "returncode": proc.returncode}
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            if tmp.is_file():
                tmp.unlink()
        except Exception:
            pass


def _find_dsh() -> Path | None:
    """Найти исполняемый файл CLI Harness."""
    candidates = [
        Path(os.environ.get("LOCALAPPDATA") or "") /
        "Programs" / "DeepSeek Harness" / "resources" / "runtime" / "cli" / "bin" / "dsh.cmd",
        Path(os.path.expanduser("~")) / "AppData" / "Local" / "Programs" /
        "DeepSeek Harness" / "resources" / "runtime" / "cli" / "bin" / "dsh.cmd",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def state() -> dict[str, Any]:
    """Полное состояние автонастройки клиентов для панели."""
    harness_now = ""
    if DSH_PATCH.is_file():
        text = DSH_PATCH.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"serverName:\s*ragraf[\s\S]{0,500}?transport:\s*([a-z-]+)", text)
        harness_now = m.group(1) if m else ""
    return {
        "interpreter": interpreter(),
        "venv_used": VENV_PYTHON.is_file(),
        "http_port": HTTP_PORT,
        "autoclaw": autoclaw_values(),
        "harness": {
            "path": str(DSH_PATCH),
            "installed": harness_now,
            "variants": [harness_block("stdio"), harness_block("streamable-http")],
        },
        "sagaai": sagaai_state(),
    }
