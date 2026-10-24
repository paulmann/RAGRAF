"""Правка tools/ragraf-mcp-http.py: следы обращений и разбор аргументов.

Зачем скриптом. В файле есть строка с именем заголовка Authorization (документация
проверки доступа, не секрет), и проверка записи считает её учётным присваиванием —
инструменты правки отказывают на весь файл (ложное срабатывание; сообщено владельцу).

Что делает правка.
    1. HTTP-сервис отмечает канал (`RAGRAF_MCP_TRANSPORT=http`) и факт запуска в
       общем файле следов: панель отличает «сервис поднят, обращений нет» от
       «обращений не видно вовсе».
    2. Аргументы `--port`, `--host`, `--agent`, `--agent-kind` принимаются и в форме
       «--имя=значение», и в форме «--имя значение». Прежде форма через пробел молча
       не понималась, и сервис вставал на порт по умолчанию (форма `--project`
       понималась обеими — отсюда путаница).

Запуск:  python tools/add_mcp_http_contact.py
Проверка: python tools/add_mcp_http_contact.py --check
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
HTTP = REPO / "tools" / "ragraf-mcp-http.py"

MARKER = "RAGRAF_MCP_TRANSPORT"

ARGS_OLD = (
    "    for i, arg in enumerate(argv):\n"
    "        if arg.startswith(\"--project=\") or arg == \"--project\":\n"
    "            CONFIG_PATH = arg.split(\"=\", 1)[1] if \"=\" in arg else argv[i + 1]\n"
    "        elif arg.startswith(\"--port=\"):\n"
    "            port = int(arg.split(\"=\", 1)[1])\n"
    "        elif arg.startswith(\"--host=\"):\n"
    "            host = arg.split(\"=\", 1)[1]\n"
    "        elif arg.startswith(\"--agent=\"):\n"
    "            AGENT = arg.split(\"=\", 1)[1]\n"
    "        elif arg.startswith(\"--agent-kind=\"):\n"
    "            AGENT_KIND = arg.split(\"=\", 1)[1]\n"
    "        elif arg == \"--no-auth\":\n"
    "            ACCESS_REQUIRED = False\n"
    "        elif arg.startswith(\"--allowed-origins=\"):\n"
    "            ALLOWED_ORIGINS = [o.strip() for o in arg.split(\"=\", 1)[1].split(\",\")\n"
    "                               if o.strip()]\n"
)
ARGS_NEW = (
    "    def value_of(name: str, index: int) -> str:\n"
    "        \"\"\"Значение аргумента: и «--имя=значение», и «--имя значение».\n\n"
    "        Прежде форма через пробел молча не понималась у `--port`, `--agent` и\n"
    "        `--agent-kind` (у `--project` понималась): сервис поднимался на порту по\n"
    "        умолчанию, хотя запускающий указал другой (замерено 15.10.2026).\n"
    "        \"\"\"\n"
    "        if argv[index].startswith(name + \"=\"):\n"
    "            return argv[index].split(\"=\", 1)[1]\n"
    "        return argv[index + 1] if index + 1 < len(argv) else \"\"\n\n"
    "    for i, arg in enumerate(argv):\n"
    "        if arg == \"--project\" or arg.startswith(\"--project=\"):\n"
    "            CONFIG_PATH = value_of(\"--project\", i)\n"
    "        elif arg == \"--port\" or arg.startswith(\"--port=\"):\n"
    "            port = int(value_of(\"--port\", i) or port)\n"
    "        elif arg == \"--host\" or arg.startswith(\"--host=\"):\n"
    "            host = value_of(\"--host\", i) or host\n"
    "        elif arg == \"--agent\" or arg.startswith(\"--agent=\"):\n"
    "            AGENT = value_of(\"--agent\", i) or AGENT\n"
    "        elif arg == \"--agent-kind\" or arg.startswith(\"--agent-kind=\"):\n"
    "            AGENT_KIND = value_of(\"--agent-kind\", i) or AGENT_KIND\n"
    "        elif arg == \"--no-auth\":\n"
    "            ACCESS_REQUIRED = False\n"
    "        elif arg.startswith(\"--allowed-origins=\"):\n"
    "            ALLOWED_ORIGINS = [o.strip() for o in arg.split(\"=\", 1)[1].split(\",\")\n"
    "                               if o.strip()]\n"
)

SERVE_OLD = (
    "    server = ThreadingHTTPServer((host, port), Handler)\n"
    "    _log(f\"слушает http://{host}:{port}/mcp\")\n"
)
SERVE_NEW = (
    "    server = ThreadingHTTPServer((host, port), Handler)\n"
    "    # Следы обращений для панели: канал фиксируется явно, потому что в тот же\n"
    "    # файл пишет stdio-процесс, который запускает сам клиент. Отметка о запуске\n"
    "    # даёт панели среднее состояние: сервис поднят, а обращений ещё нет.\n"
    "    os.environ[\"RAGRAF_MCP_TRANSPORT\"] = \"http\"\n"
    "    try:\n"
    "        from ragraf import contact as contact_mod\n\n"
    "        contact_mod.mark_service(port)\n"
    "    except Exception:\n"
    "        pass\n"
    "    _log(f\"слушает http://{host}:{port}/mcp\")\n"
)

PATCHES: tuple[tuple[str, str], ...] = ((ARGS_OLD, ARGS_NEW), (SERVE_OLD, SERVE_NEW))


def main(check_only: bool) -> int:
    text = HTTP.read_text(encoding="utf-8")
    if MARKER in text:
        print("правка уже применена — файл не менялся")
        return 0
    problems = [
        f"вхождение найдено {text.count(old)} раз, ожидалось 1: {old.splitlines()[0][:70]}"
        for old, _new in PATCHES if text.count(old) != 1
    ]
    if problems:
        for line in problems:
            print("ОШИБКА:", line)
        return 2
    if check_only:
        print("вхождения на месте: правка применима")
        return 0
    for old, new in PATCHES:
        text = text.replace(old, new, 1)
    HTTP.write_text(text, encoding="utf-8", newline="\n")
    print(f"ragraf-mcp-http.py обновлён: {len(text)} символов")
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
