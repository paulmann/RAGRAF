"""Правка contact.py, mcp_server.py и app.js: различать «кто представился сам».

Зачем. HTTP-сервис поднимается с `--agent AutoClaw` — так записано в инструкции
панели. Если след обращения хранит только это имя, то ЛЮБОЙ клиент, пришедший на
сервис, выглядит как подключение AutoClaw, и зелёный индикатор врёт. Поэтому
хранятся оба имени: `agent` (параметр запуска или представившееся имя) и
`client_info` (имя из запроса `initialize`, по протоколу MCP клиент обязан себя
назвать). Панель считает признаком подключения `client_info`, если он есть.

Файлы правятся скриптом, потому что проверка записи считает учётными
существующие в них строки (`progressToken` в mcp_server, ключ localStorage в app.js).

Запуск:  python tools/add_mcp_contact_clientinfo.py
Проверка: python tools/add_mcp_contact_clientinfo.py --check
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
CONTACT = REPO / "src" / "ragraf" / "contact.py"
MCP = REPO / "src" / "ragraf" / "mcp_server.py"
APP = REPO / "admin" / "static" / "app.js"

MARKER = "client_name=client"

FILES: dict[pathlib.Path, tuple[tuple[str, str], ...]] = {
    CONTACT: (
        (
            'def record(agent: str, method: str, tool: str = "", transport: str = "") -> None:\n'
            '    """Записать обращение клиента. Никогда не поднимает исключение."""\n'
            '    name = (agent or "").strip() or "unknown"\n'
            '    move = (transport or TRANSPORT).strip() or "stdio"\n',
            'def record(agent: str, method: str, tool: str = "", transport: str = "",\n'
            '           client_name: str = "") -> None:\n'
            '    """Записать обращение клиента. Никогда не поднимает исключение.\n\n'
            '    `agent` — имя, известное по параметру запуска; `client_name` — имя из\n'
            '    `clientInfo` запроса `initialize`. Второе важнее: HTTP-сервис\n'
            '    поднимается с `--agent AutoClaw`, поэтому без отдельного признака\n'
            '    чужой клиент выглядел бы как подключение AutoClaw.\n'
            '    """\n'
            '    name = (agent or "").strip() or "unknown"\n'
            '    shown = (client_name or "").strip()\n'
            '    move = (transport or TRANSPORT).strip() or "stdio"\n',
        ),
        (
            '            item["transport"] = move\n'
            '            item["calls"] = int(item.get("calls") or 0) + 1\n',
            '            item["transport"] = move\n'
            '            item["agent"] = name\n'
            '            if shown:\n'
            '                item["client_info"] = shown\n'
            '            item["source"] = "client" if (shown or item.get("client_info")) else "launch"\n'
            '            item["calls"] = int(item.get("calls") or 0) + 1\n',
        ),
        (
            '        clients.append({\n'
            '            "agent": str(name),\n'
            '            "transport": item.get("transport") or "",\n',
            '        clients.append({\n'
            '            "agent": str(name),\n'
            '            "client_info": item.get("client_info") or "",\n'
            '            "source": item.get("source") or "launch",\n'
            '            "transport": item.get("transport") or "",\n',
        ),
    ),
    MCP: (
        (
            'def _note_contact(agent: str, method: str, tool: str = "") -> None:\n'
            '    """Записать след обращения клиента.\n',
            'def _note_contact(agent: str, method: str, tool: str = "",\n'
            '                  client: str = "") -> None:\n'
            '    """Записать след обращения клиента.\n\n'
            '    `client` — имя из `clientInfo` запроса `initialize`: оно важнее\n'
            '    параметра запуска, потому что HTTP-сервис поднимается с\n'
            '    `--agent AutoClaw`.\n',
        ),
        (
            '    try:\n'
            '        contact_mod.record(agent, method, tool)\n'
            '    except Exception:\n'
            '        pass\n',
            '    try:\n'
            '        contact_mod.record(agent, method, tool, client_name=client)\n'
            '    except Exception:\n'
            '        pass\n',
        ),
        (
            '            _note_contact(self.agent, "initialize")\n',
            '            _note_contact(self.agent, "initialize",\n'
            '                          client=str(((params.get("clientInfo") or {})\n'
            '                                      .get("name")) or ""))\n',
        ),
    ),
    APP: (
        (
            "    const name = String(rows[i].agent || '').toLowerCase();\n",
            "    // Имя, которым представился САМ клиент, важнее имени из параметра\n"
            "    // запуска: HTTP-сервис поднимается с `--agent AutoClaw`.\n"
            "    const name = String(rows[i].client_info || rows[i].agent || '').toLowerCase();\n",
        ),
        (
            "  const tool = (record && record.last_tool)\n"
            "    ? ' · ' + t('last tool: {tool}', { tool: record.last_tool }) : '';\n"
            "  if (record && record.recent) {\n"
            "    return '<p class=\"status status--ok\">'\n"
            "      + esc(t('Connected: last call {ago} ago', { ago: agoText(record.age_seconds) }) + tool)\n"
            "      + '</p>';\n"
            "  }\n",
            "  const who = record ? String(record.client_info || record.agent || '') : '';\n"
            "  const tool = (record && record.last_tool)\n"
            "    ? ' · ' + t('last tool: {tool}', { tool: record.last_tool }) : '';\n"
            "  if (record && record.recent) {\n"
            "    return '<p class=\"status status--ok\">'\n"
            "      + esc(t('Connected: last call {ago} ago', { ago: agoText(record.age_seconds) })\n"
            "            + (who ? ' · ' + who : '') + tool)\n"
            "      + '</p>';\n"
            "  }\n",
        ),
    ),
}


def main(check_only: bool) -> int:
    problems: list[str] = []
    texts: dict[pathlib.Path, str] = {}
    for path, patches in FILES.items():
        text = path.read_text(encoding="utf-8")
        if MARKER in text:
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
