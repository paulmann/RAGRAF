"""Правка четырёх файлов: корпус из ENV, выводы панели, инструкция Local program.

Зачем скриптом. У `mcp_server.py` и `app.js` проверка записи считает учётными
существующие строки (`progressToken`, ключ localStorage), у `tools/ragraf-mcp-http.py`
— имя заголовка Authorization, у `client_setup.py` — то же. Инструменты правки на
эти файлы отказывают (ложные срабатывания; сообщено владельцу).

Повод (замер 15.10.2026, замечания владельца).
    1. У AutoClaw в форме «Add tool» для типа `Local program` НЕТ поля рабочего
       каталога, зато есть «Environment variables». Значит корпус надо уметь брать
       из переменной окружения — иначе он определяется по каталогу процесса и может
       не найтись. Добавлено: `RAGRAF_PROJECT` (и `RAGRAF_CONFIG`) для `--project`
       в stdio-сервере и в HTTP-сервисе.
    2. Инструкция в панели показывала «Working directory» — поля с таким именем в
       AutoClaw нет. Теперь показываются переменные окружения.
    3. Панель говорила «обращений от AutoClaw не видно», хотя AutoClaw подключился:
       его MCP-клиент представляется именем `zwork`, а панель искала «autoclaw».
       Выводы считает сервер (`contact.links`), страница их только показывает.

Запуск:  python tools/add_env_and_links.py
Проверка: python tools/add_env_and_links.py --check
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
MCP = REPO / "src" / "ragraf" / "mcp_server.py"
HTTP = REPO / "tools" / "ragraf-mcp-http.py"
APP = REPO / "admin" / "static" / "app.js"
CLIENTS = REPO / "src" / "ragraf" / "client_setup.py"

MARKER = "RAGRAF_PROJECT"

FILES: dict[pathlib.Path, tuple[tuple[str, str], ...]] = {
    MCP: (
        (
            '        "--project", "-p", default=None,\n'
            '        help="конфигурация корпуса; если не задана, корпус берётся из реестра "\n'
            '             "по рабочему каталогу, а если и там нет — выбирается операцией use_project",\n',
            '        "--project", "-p",\n'
            '        # Значение можно передать переменной окружения: у клиента AutoClaw\n'
            '        # (тип «Local program») нет поля рабочего каталога, а есть переменные\n'
            '        # окружения — без этого корпус определялся по каталогу процесса и мог\n'
            '        # не найтись (замер 15.10.2026).\n'
            '        default=(os.environ.get("RAGRAF_PROJECT")\n'
            '                 or os.environ.get("RAGRAF_CONFIG") or None),\n'
            '        help="конфигурация корпуса; если не задана, берётся из RAGRAF_PROJECT, "\n'
            '             "затем из реестра по рабочему каталогу, а если и там нет — "\n'
            '             "выбирается операцией use_project",\n',
        ),
    ),
    HTTP: (
        (
            "CONFIG_PATH: str | None = None\n",
            'CONFIG_PATH: str | None = (\n'
            '    # Корпус можно задать переменной окружения, а не только ключом запуска:\n'
            '    # так его передают клиенты, у которых нет поля рабочего каталога.\n'
            '    os.environ.get("RAGRAF_PROJECT") or os.environ.get("RAGRAF_CONFIG") or None\n'
            ')\n',
        ),
    ),
    APP: (
        (
            "function pickContact(kind, contact) {\n"
            "  const words = kind === 'harness' ? ['harness', 'deepseek', 'dsh'] : ['autoclaw'];\n"
            "  const rows = (contact && contact.clients) || [];\n",
            "function pickContact(kind, contact) {\n"
            "  // Выводы считает сервер (`contact.links`): правило сопоставления имён одно,\n"
            "  // иначе интерфейс и модуль следов разойдутся. Местное сравнение осталось\n"
            "  // запасным — на случай ответа прежней версии панели.\n"
            "  const links = (contact && contact.links) || {};\n"
            "  if (links[kind]) return links[kind];\n"
            "  const words = kind === 'harness' ? ['harness', 'deepseek', 'dsh']\n"
            "    : ['autoclaw', 'zwork'];\n"
            "  const rows = (contact && contact.clients) || [];\n",
        ),
    ),
    CLIENTS: (
        (
            'def autoclaw_values(access_hint: str = "") -> dict[str, Any]:',
            'def env_lines_local() -> str:\n'
            '    """Переменные окружения для типа «Local program» в AutoClaw.\n\n'
            '    Поля «рабочий каталог» в форме НЕТ, а корпус и имя агента задать надо:\n'
            '    клиент передаёт окружение процессу, поэтому значения идут строками\n'
            '    `KEY=value` — по одной в строке (так их и ждёт форма).\n'
            '    """\n'
            '    return "\\n".join([\n'
            '        f"RAGRAF_PROJECT={ADMIN_CONFIG}",\n'
            '        "RAGRAF_AGENT=AutoClaw",\n'
            '        "RAGRAF_AGENT_KIND=autoclaw",\n'
            '    ])\n\n\n'
            'def autoclaw_values(access_hint: str = "") -> dict[str, Any]:',
        ),
        (
            '        "how": ("Settings → Tools & connections → Add tool. Type: Local program. "\n'
            '                "Процесс запускает сам AutoClaw, поэтому порт и значение доступа "\n'
            '                "не нужны, а сервис поднимать не надо."),\n'
            '        "fields": [\n'
            '            {"label": "Name", "value": "RAGRAF"},\n'
            '            {"label": "Type", "value": "Local program"},\n'
            '            {"label": "Command", "value": interpreter()},\n'
            '            {"label": "Arguments",\n'
            '             "value": f"{SERVER} --agent AutoClaw --agent-kind autoclaw"},\n'
            '            {"label": "Working directory", "value": str(REPO)},\n'
            '        ],\n',
            '        "how": ("Settings → Tools & connections → Add tool. Type: Local program. "\n'
            '                "Процесс запускает сам AutoClaw, поэтому порт и значение доступа "\n'
            '                "не нужны, а сервис поднимать не надо. Поля «рабочий каталог» "\n'
            '                "в форме НЕТ — вместо него переменные окружения ниже, и именно "\n'
            '                "они задают корпус и имя агента."),\n'
            '        "fields": [\n'
            '            {"label": "Name", "value": "RAGRAF"},\n'
            '            {"label": "Type", "value": "Local program"},\n'
            '            {"label": "Launch command", "value": interpreter()},\n'
            '            {"label": "Arguments", "value": str(SERVER)},\n'
            '            {"label": "Environment variables",\n'
            '             "value": env_lines_local()},\n'
            '        ],\n',
        ),
    ),
}


def main(check_only: bool) -> int:
    problems: list[str] = []
    texts: dict[pathlib.Path, str] = {}
    for path, patches in FILES.items():
        text = path.read_text(encoding="utf-8")
        if MARKER in text and path is not MCP:
            print(f"{path.name}: правка уже применена")
            return 0
        texts[path] = text
        for old, _new in patches:
            if text.count(old) != 1:
                problems.append(f"{path.name}: вхождение найдено {text.count(old)} раз, "
                                f"ожидалось 1: {old.splitlines()[0][:60]}")
    if problems:
        for line in problems:
            print("ОШИБКА:", line)
        return 2
    if check_only:
        print("вхождения на месте: правка применима")
        return 0
    for path, patches in FILES.items():
        text = texts[path]
        for old, new in patches:
            text = text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"{path.name}: {len(text)} символов")
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
