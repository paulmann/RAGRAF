"""Разведка: можно ли узнать агента из MCP-протокола.

Установлено: MCP-сервер создаёт фасад без указания `tool`, поэтому вызовы
агентов попадают в телеметрию как `unknown` (113 из 138 в корпусе z).
Значения `tool` сейчас различают только каналы: admin, cli, dev, selftest.

Владелец хочет статистику ПО АГЕНТУ: какой агент AutoClaw использовал RAGRAF,
плюс общую по всем. Значит нужно понять, откуда взять имя агента.

Проверяется:
  1. что присылает клиент в `initialize` — там бывает clientInfo;
  2. какие поля вообще приходят в запросах;
  3. можно ли передать имя агента параметром запуска.

Это разведка по протоколу. Читается код сервера, ничего не меняется.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
src = REPO / "src" / "ragraf" / "mcp_server.py"
text = src.read_text(encoding="utf-8")

print("=" * 78)
print("1. ЧТО СЕРВЕР ЧИТАЕТ ИЗ ЗАПРОСОВ")
print("=" * 78)
# Ищутся места, где разбираются параметры запроса.
for i, line in enumerate(text.splitlines(), 1):
    if any(k in line for k in ("clientInfo", "client_info", "initialize",
                               "params.get", "params[")):
        print(f"  {i:>4}: {line.strip()[:90]}")

print()
print("=" * 78)
print("2. ОБРАБОТКА initialize")
print("=" * 78)
idx = text.find('"initialize"')
if idx < 0:
    idx = text.find("initialize")
# Печатается блок вокруг обработки.
start = max(0, text.rfind("\n", 0, idx - 400))
print(text[start : idx + 900])

print()
print("=" * 78)
print("3. КАКИЕ ПАРАМЕТРЫ ПРИНИМАЕТ СЕРВЕР")
print("=" * 78)
for i, line in enumerate(text.splitlines(), 1):
    if "add_argument" in line:
        print(f"  {i:>4}: {line.strip()}")

print()
print("=" * 78)
print("4. ЧТО ВИДНО В РЕАЛЬНОМ ЗАПРОСЕ АГЕНТА")
print("=" * 78)
# Ищется журнал запросов, если сервер его ведёт.
logs = list((REPO / "logs").glob("*.log")) if (REPO / "logs").exists() else []
logs += list((REPO / ".ragraf").glob("*.log")) if (REPO / ".ragraf").exists() else []
print(f"  файлов журнала: {len(logs)}")
for f in logs[:5]:
    print(f"    {f.relative_to(REPO)}")

# Ищутся конфигурации MCP у агентов: как они запускают сервер.
print()
print("=" * 78)
print("5. КАК АГЕНТЫ ЗАПУСКАЮТ MCP-СЕРВЕР (поиск конфигураций)")
print("=" * 78)
import os  # noqa: E402

# Каталоги, где AutoClaw и другие агенты держат настройки MCP.
CANDIDATES = [
    pathlib.Path(os.environ.get("USERPROFILE", "")) / ".pi" / "agent",
    pathlib.Path(__file__).resolve().parents[1].parent / ".agents",
    pathlib.Path(__file__).resolve().parents[1].parent / ".openclaw",
    pathlib.Path(__file__).resolve().parents[1].parent / ".autoclaw",
]
for base in CANDIDATES:
    if not base.exists():
        continue
    hits = []
    for path in base.rglob("*.json"):
        try:
            t = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "ragraf" in t.lower():
            hits.append(path)
    if hits:
        print(f"\n  {base}:")
        for h in hits[:6]:
            print(f"    {h.relative_to(base)}")

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Проверка показывает, откуда можно взять имя агента:
    * из поля `clientInfo` в запросе `initialize` — по протоколу MCP клиент
      обязан себя назвать;
    * либо из параметра запуска сервера (например --agent).

  Второй путь надёжнее: он не зависит от того, заполнит ли клиент clientInfo,
  и позволяет задать понятное имя вместо технического.
""")
