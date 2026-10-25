"""Правка src/ragraf/client_setup.py: обе формы подключения AutoClaw.

Зачем скриптом. В файле есть строка с именем заголовка Authorization (настройка
клиента HARNESS, не секрет) — проверка записи считает её учётным присваиванием и
отказывает на весь файл (ложное срабатывание; сообщено владельцу).

Повод. Владелец спросил 15.10.2026: «а если я хочу через Local program, а не Online
service?» Блок описывал только Online service, хотя AutoClaw умеет и локальную
программу: тогда клиент сам запускает stdio-процесс, и ни порт, ни значение доступа
не нужны. Обе формы описаны и показываются в панели.

Старые ключи `how`/`fields`/`before`/`access_note` не убираются: они отвечают форме
Online service и их читают проверки проекта.

Запуск:  python tools/add_autoclaw_variants.py
Проверка: python tools/add_autoclaw_variants.py --check
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
TARGET = REPO / "src" / "ragraf" / "client_setup.py"

MARKER = "\"variants\": [online, local]"

OLD = '''def autoclaw_values(access_hint: str = "") -> dict[str, Any]:
    """Значения для формы «Add tool» в AutoClaw (Online service)."""
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
    }
'''

NEW = '''def autoclaw_values(access_hint: str = "") -> dict[str, Any]:
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
                "не нужны, а сервис поднимать не надо."),
        "fields": [
            {"label": "Name", "value": "RAGRAF"},
            {"label": "Type", "value": "Local program"},
            {"label": "Command", "value": interpreter()},
            {"label": "Arguments",
             "value": f"{SERVER} --agent AutoClaw --agent-kind autoclaw"},
            {"label": "Working directory", "value": str(REPO)},
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
'''


def main(check_only: bool) -> int:
    text = TARGET.read_text(encoding="utf-8")
    if MARKER in text:
        print("правка уже применена — файл не менялся")
        return 0
    if text.count(OLD) != 1:
        print(f"ОШИБКА: вхождение найдено {text.count(OLD)} раз, ожидалось 1")
        return 2
    if check_only:
        print("вхождение на месте: правка применима")
        return 0
    TARGET.write_text(text.replace(OLD, NEW, 1), encoding="utf-8", newline="\n")
    print(f"client_setup.py обновлён: {len(text.replace(OLD, NEW, 1))} символов")
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv))
