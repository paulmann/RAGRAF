# -*- coding: utf-8 -*-
"""Интеграция RAGRAF с LangChain: три уровня, один источник истины.

Уровни намеренно разделены, потому что это разные вопросы:

* **обязательный поиск** — `RAGRAFRetriever`: возвращает `Document[]` с адресом
  `path:line`, вызывается пайплайном до генерации (2-step RAG);
* **инструменты по решению агента** — `build_tools()`: семь узких операций
  (`search`, `symbol`, `callers`, `impact`, `read`, `write_check`, `freshness`);
* **MCP** — `load_mcp_tools()`: те же данные как 74 инструмента сервера, для
  других клиентов и для изоляции зависимостей.

Ни один уровень не переписывает логику поиска: все три идут через фасад RAGRAF
(`src/ragraf/facade.py`, 67 операций), поэтому ранжирование, фильтрация исключений
и провенанс остаются в одном месте. Прямого доступа к хранилищам SQLite интеграция
не делает вовсе — обойти правила RAGRAF (что исключено, какие связи доказаны) она
не может по устройству.
"""

from __future__ import annotations

from ._bridge import (
    RAGRAFConfigError,
    clear_facades,
    describe_environment,
    facade_cache_size,
    find_project_root,
    get_facade,
    resolve_config_path,
)
from .mcp_backend import MCP_UNAVAILABLE, load_mcp_tools, mcp_available, server_config
from .retriever import RAGRAFRetriever, hit_to_document
from .tools import build_tools

__all__ = [
    "RAGRAFRetriever",
    "build_tools",
    "load_mcp_tools",
    "mcp_available",
    "server_config",
    "MCP_UNAVAILABLE",
    "RAGRAFConfigError",
    "clear_facades",
    "describe_environment",
    "facade_cache_size",
    "find_project_root",
    "get_facade",
    "hit_to_document",
    "resolve_config_path",
    "__version__",
]

#: Версия интеграции (не версия RAGRAF: о ней говорит `describe_environment`).
__version__ = "0.1.0"
