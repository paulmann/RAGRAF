# -*- coding: utf-8 -*-
"""Живая проверка MCP-пути: `langchain.mcp.MCPAdapter` против сервера RAGRAF.

Проверка пропускается (SKIP, код 0) в двух случаях, и оба названы:
нет `fastmcp` (значит не ставилось дополнение `langchain[mcp]`) либо индекс
устарел — тогда сервер сам запустит переиндексацию (`watch=True`,
`src/ragraf/mcp_server.py:2163`) и вызов будет длиться столько же, сколько
переиндексация. Молча ждать минуты в тесте — плохой ответ; честнее назвать
причину и не притворяться, что проверили.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragraf_langchain import (  # noqa: E402
    RAGRAFConfigError,
    RAGRAFRetriever,
    load_mcp_tools,
    mcp_available,
    server_config,
)

REF = re.compile(r"[\w./\\@-]+\.\w+:\d+")
SKIPPED: list[str] = []


def test_server_config_names_launcher_and_corpus() -> None:
    config = server_config()
    server = config["mcpServers"]["ragraf"]
    assert "ragraf_mcp.py" in " ".join(server["args"])
    assert server["command"], "не назван интерпретатор"
    assert server["env"]["RAGRAF_AGENT_KIND"] == "external"


def test_availability_reason_is_specific() -> None:
    ok, reason = mcp_available()
    assert isinstance(ok, bool)
    if not ok:
        assert reason, "отказ без причины: непонятно, что устанавливать"
        SKIPPED.append(f"MCP недоступен: {reason}")


def test_mcp_tools_are_discovered_and_callable() -> None:
    ok, reason = mcp_available()
    if not ok:
        SKIPPED.append(f"MCP недоступен: {reason}")
        return
    try:
        stale = (RAGRAFRetriever(limit=1).freshness().get("result") or {}).get(
            "changed_since_index")
    except RAGRAFConfigError as exc:
        SKIPPED.append(f"корпус не найден: {str(exc).splitlines()[0]}")
        return
    if stale:
        SKIPPED.append("индекс устарел: сервер запустит переиндексацию, тест не ждёт")
        return

    async def run() -> str:
        tools = await load_mcp_tools()
        names = {tool.name for tool in tools}
        assert len(tools) > 0, "сервер не отдал инструментов"
        assert "ragraf_search" in names, sorted(names)[:10]
        search = next(tool for tool in tools if tool.name == "ragraf_search")
        return str(await search.ainvoke({"query": "corpus_hash", "limit": 2}))

    text = asyncio.run(run())
    assert REF.search(text), text[:300]


if __name__ == "__main__":
    import traceback

    failed = 0
    for name in sorted(n for n in dict(globals()) if n.startswith("test_")):
        try:
            globals()[name]()
            print(f"ok   {name}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    for reason in SKIPPED:
        print(f"SKIP {reason}")
    print(f"\nпровалов: {failed}")
    raise SystemExit(1 if failed else 0)
