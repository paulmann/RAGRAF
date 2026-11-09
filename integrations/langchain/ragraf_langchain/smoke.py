# -*- coding: utf-8 -*-
"""Проверка интеграции на живом индексе: печатает вердикт и код возврата.

Коды: 0 — все проверки прошли (ссылка `path:line` получена);
1 — проверка провалилась; 2 — интеграция не может работать в этом окружении
(нет конфигурации, нет пакета); 3 — недоступен запрошенный MCP-путь.

Зачем отдельный запускатор, а не только тесты. Смоук отвечает на один вопрос:
«данные дошли до LangChain в том виде, в каком их можно цитировать». Тесты
проверяют поведение (пустой ответ, кэш, метаданные), а этот файл — пригодность
окружения, и он же вызывается из `smoke.ps1`.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from pathlib import Path

#: Ссылка-доказательство: файл и строка. Без неё факт неотличим от догадки.
REF = re.compile(r"[\w./\\@-]+\.\w+:\d+")


def _header(text: str) -> None:
    print(f"\n=== {text}")


def check_retriever(config: Path | None, limit: int) -> tuple[bool, str]:
    from ragraf_langchain import RAGRAFRetriever  # noqa: PLC0415

    retriever = RAGRAFRetriever(config_path=str(config) if config else None, limit=limit)
    started = time.perf_counter()
    docs = retriever.invoke("контрольная сумма корпуса и схлопывание дублей")
    elapsed = (time.perf_counter() - started) * 1000
    if not docs:
        return False, "поиск вернул пустой список"
    refs = [doc.metadata.get("ref") for doc in docs]
    bad = [ref for ref in refs if not ref or not REF.search(str(ref))]
    print(f"  документов: {len(docs)}, первый вызов {elapsed:.0f} мс, rev="
          f"{docs[0].metadata.get('index_rev')}, stale={docs[0].metadata.get('stale')}")
    for ref in refs[:3]:
        print(f"  path:line = {ref}")
    if bad:
        return False, f"адреса без строки: {bad[:3]}"
    return True, ""


def check_tools(config: Path | None) -> tuple[bool, str]:
    from ragraf_langchain import build_tools  # noqa: PLC0415

    tools = {tool.name: tool for tool in build_tools(str(config) if config else None)}
    expected = {"ragraf_search", "ragraf_symbol", "ragraf_callers", "ragraf_impact",
                "ragraf_read", "ragraf_write_check", "ragraf_freshness"}
    missing = expected - set(tools)
    if missing:
        return False, f"нет инструментов: {sorted(missing)}"
    print(f"  инструментов: {len(tools)} — {', '.join(sorted(tools))}")
    out = str(tools["ragraf_search"].invoke({"query": "corpus_hash", "limit": 3}))
    if not REF.search(out):
        return False, f"поиск не дал адреса path:line: {out[:200]}"
    print(f"  path:line = {REF.search(out).group(0)}")
    out = str(tools["ragraf_freshness"].invoke({}))
    print(f"  свежесть: {out[:160]}")
    return True, ""


def check_mcp(config: Path | None) -> tuple[bool, str]:
    """Обнаружение инструментов и вызов — в ОДНОМ цикле событий.

    Два отдельных `asyncio.run` здесь не работают: клиент внутри `MCPAdapter`
    привязан к циклу, в котором открыт, и второй цикл получает «Event loop is
    closed» (замерено 03.11.2026 — именно так и было сломано).
    """
    from ragraf_langchain import load_mcp_tools, mcp_available  # noqa: PLC0415

    ok, reason = mcp_available()
    if not ok:
        return False, reason

    async def run() -> tuple[bool, str]:
        started = time.perf_counter()
        tools = await load_mcp_tools(config, agent="langchain-smoke")
        elapsed = (time.perf_counter() - started) * 1000
        if not tools:
            return False, "сервер не отдал ни одного инструмента"
        print(f"  инструментов MCP: {len(tools)}, обнаружение {elapsed:.0f} мс")
        search = next((tool for tool in tools if tool.name == "ragraf_search"), None)
        if search is None:
            return False, "в MCP-наборе нет ragraf_search"
        text = str(await asyncio.wait_for(
            search.ainvoke({"query": "corpus_hash", "limit": 2}), timeout=180))
        match = REF.search(text)
        if match is None:
            return False, f"MCP-поиск не дал адреса path:line: {text[:200]}"
        print(f"  path:line = {match.group(0)}")
        return True, ""

    return asyncio.run(run())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="проверка интеграции RAGRAF ↔ LangChain")
    parser.add_argument("--config", default=None, help="конфигурация корпуса")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--no-mcp", action="store_true", help="не проверять MCP-путь")
    args = parser.parse_args(argv)
    config = Path(args.config) if args.config else None

    failures: list[str] = []
    try:
        from ragraf_langchain import describe_environment  # noqa: PLC0415

        _header("окружение")
        env = describe_environment(config)
        for key, value in env.items():
            print(f"  {key}: {value}")
    except Exception as exc:  # noqa: BLE001 — код 2 объясняет причину
        print(f"  ПРОВАЛ: {type(exc).__name__}: {exc}")
        return 2

    _header("retriever: обязательный поиск")
    try:
        ok, reason = check_retriever(config, args.limit)
        print(f"  [{'ok' if ok else 'FAIL'}] {reason or 'ссылки path:line получены'}")
        if not ok:
            failures.append(f"retriever: {reason}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {type(exc).__name__}: {exc}")
        failures.append(f"retriever: {exc}")

    _header("инструменты агента")
    try:
        ok, reason = check_tools(config)
        print(f"  [{'ok' if ok else 'FAIL'}] {reason or 'инструменты отвечают адресом'}")
        if not ok:
            failures.append(f"tools: {reason}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {type(exc).__name__}: {exc}")
        failures.append(f"tools: {exc}")

    mcp_failed = False
    if not args.no_mcp:
        _header("MCP-путь (langchain.mcp)")
        try:
            ok, reason = check_mcp(config)
            print(f"  [{'ok' if ok else 'SKIP'}] {reason or 'сервер отвечает'}")
            mcp_failed = not ok
        except Exception as exc:  # noqa: BLE001
            print(f"  [SKIP] {type(exc).__name__}: {str(exc)[:200]}")
            mcp_failed = True

    print("\nвердикт:", "ок" if not failures else f"провалов {len(failures)}")
    for item in failures:
        print(f"  - {item}")
    if failures:
        return 1
    if mcp_failed:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
