# -*- coding: utf-8 -*-
"""Замер задержек: прямой путь против MCP на одном и том же запросе.

Зачем файл в поставке. Числа в README и в `docs/03_implementation/AGENT-INTEGRATIONS.md`
(§7) должны воспроизводиться одной командой, иначе через месяц это не факты, а
воспоминания. Мерится то, что видит пользователь: время от вызова до ответа.

    python tests/bench_latency.py [--config <путь>] [--query "…"] [--repeat 5]

Первый вызов в каждом пути — холодный (фасад, эмбеддинг запроса, запуск
MCP-сервера), дальше — повторы на прогретом индексе.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragraf_langchain import RAGRAFRetriever, load_mcp_tools, mcp_available  # noqa: E402

DEFAULT_QUERY = "контрольная сумма корпуса схлопывание дублей"


def bench_inproc(config: str | None, query: str, repeat: int) -> tuple[float, float]:
    retriever = RAGRAFRetriever(config_path=config, limit=5)
    started = time.perf_counter()
    docs = retriever.invoke(query)
    cold = (time.perf_counter() - started) * 1000
    warm: list[float] = []
    for _ in range(repeat):
        started = time.perf_counter()
        retriever.invoke(query)
        warm.append((time.perf_counter() - started) * 1000)
    median = statistics.median(warm)
    print(f"in-process: холодный {cold:.0f} мс, прогретый {median:.0f} мс "
          f"(мин {min(warm):.0f}, макс {max(warm):.0f}), документов {len(docs)}")
    return cold, median


async def bench_mcp(config: str | None, query: str,
                    repeat: int) -> tuple[float, float] | None:
    ok, reason = mcp_available()
    if not ok:
        print(f"MCP: недоступен — {reason}")
        return None
    started = time.perf_counter()
    tools = await load_mcp_tools(config, agent="langchain-bench")
    discovery = (time.perf_counter() - started) * 1000
    search = next((tool for tool in tools if tool.name == "ragraf_search"), None)
    if search is None:
        print("MCP: в наборе нет ragraf_search")
        return None
    started = time.perf_counter()
    out = await search.ainvoke({"query": query, "limit": 5})
    cold = (time.perf_counter() - started) * 1000
    warm: list[float] = []
    for _ in range(repeat):
        started = time.perf_counter()
        await search.ainvoke({"query": query, "limit": 5})
        warm.append((time.perf_counter() - started) * 1000)
    median = statistics.median(warm)
    print(f"MCP: инструментов {len(tools)}, обнаружение {discovery:.0f} мс, "
          f"первый вызов {cold:.0f} мс, прогретый {median:.0f} мс "
          f"(мин {min(warm):.0f}, макс {max(warm):.0f}), ответ {len(str(out))} символов")
    return cold, median


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="замер задержек интеграции")
    parser.add_argument("--config", default=None)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--no-mcp", action="store_true")
    args = parser.parse_args(argv)

    direct = bench_inproc(args.config, args.query, args.repeat)
    if not args.no_mcp:
        mcp = asyncio.run(bench_mcp(args.config, args.query, args.repeat))
        if mcp is not None:
            print(f"разница на прогретом индексе: {mcp[1] - direct[1]:+.0f} мс "
                  "(знак — в пользу прямого вызова при отрицательном)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
