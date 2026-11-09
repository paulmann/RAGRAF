# -*- coding: utf-8 -*-
"""Живые проверки на настоящем индексе: адрес `path:line`, кэш фасада, async.

Пропуск вместо провала, когда корпуса нет: интеграция обязана работать и в чужом
проекте, поэтому «нет конфигурации» — это SKIP с названной причиной (код 0), а не
зелёная проверка, которая ничего не осмотрела.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ragraf_langchain import (  # noqa: E402
    RAGRAFConfigError,
    RAGRAFRetriever,
    build_tools,
    describe_environment,
    facade_cache_size,
)

REF = re.compile(r"[\w./\\@-]+\.\w+:\d+")
CONFIG = os.environ.get("RAGRAF_CONFIG") or None
SKIPPED: list[str] = []


def _config_path() -> str | None:
    try:
        return str(describe_environment(CONFIG)["config"])
    except RAGRAFConfigError as exc:
        SKIPPED.append(f"корпус не найден: {str(exc).splitlines()[0]}")
        return None


def test_retriever_returns_addressable_documents() -> None:
    config = _config_path()
    if config is None:
        return
    docs = RAGRAFRetriever(config_path=config, limit=3).invoke("контрольная сумма корпуса")
    assert docs, "поиск вернул пустой список на непустом корпусе"
    for doc in docs:
        assert REF.search(str(doc.metadata.get("ref"))), doc.metadata
        assert doc.metadata.get("index_rev"), doc.metadata
        assert doc.page_content.strip(), "документ без текста и без адреса"


def test_async_invoke_matches_sync() -> None:
    config = _config_path()
    if config is None:
        return
    retriever = RAGRAFRetriever(config_path=config, limit=2)
    sync_refs = [doc.metadata["ref"] for doc in retriever.invoke("дедупликация файлов")]
    async_docs = asyncio.run(retriever.ainvoke("дедупликация файлов"))
    assert [doc.metadata["ref"] for doc in async_docs] == sync_refs


def test_facade_is_shared_between_instances() -> None:
    config = _config_path()
    if config is None:
        return
    before = facade_cache_size()
    RAGRAFRetriever(config_path=config, limit=1).invoke("rev")
    RAGRAFRetriever(config_path=config, limit=1).invoke("rev")
    assert facade_cache_size() == before, "фасад открывается заново на каждый экземпляр"


def test_freshness_is_reported_not_guessed() -> None:
    config = _config_path()
    if config is None:
        return
    info = RAGRAFRetriever(config_path=config, limit=1).freshness()
    result = info.get("result") or {}
    assert "changed_since_index" in result, result
    assert isinstance(result["changed_since_index"], bool)


def test_tools_answer_with_addresses() -> None:
    config = _config_path()
    if config is None:
        return
    tools = {tool.name: tool for tool in build_tools(config)}
    out = str(tools["ragraf_search"].invoke({"query": "corpus_hash", "limit": 3}))
    assert REF.search(out), out[:300]
    symbol = str(tools["ragraf_symbol"].invoke({"name": "corpus_hash", "limit": 3}))
    assert "corpus_hash" in symbol
    freshness = str(tools["ragraf_freshness"].invoke({}))
    assert "rev=" in freshness


def test_every_tool_declares_its_arguments() -> None:
    config = _config_path()
    if config is None:
        return
    for tool in build_tools(config):
        schema = tool.args_schema.model_json_schema() if tool.args_schema else {}
        assert tool.description, f"{tool.name}: без описания модель не поймёт, когда звать"
        if tool.name == "ragraf_freshness":
            continue
        assert schema.get("properties"), f"{tool.name}: схема аргументов пуста"


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
