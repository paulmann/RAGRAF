# -*- coding: utf-8 -*-
"""MCP-путь LangChain: сервер RAGRAF как набор инструментов агента.

Когда он нужен. Не как «более современный транспорт», а по двум конкретным
причинам:

1. **Изоляция зависимостей.** Сервер (`src/ragraf/mcp_server.py`, JSON-RPC 2.0
   поверх stdio, протокол `2024-11-05`) живёт в отдельном процессе. Процессу
   LangChain тогда не нужны `fastembed`, `onnxruntime`, `tree-sitter` и
   `sqlite-vec` — только клиент. Для прямого вызова это не так: там нужен весь
   рантайм RAGRAF в том же процессе.
2. **Другие клиенты.** Тот же сервер обслуживает любого MCP-клиента, поэтому
   интерфейс не приходится писать второй раз.

Три измеренных границы (03.11.2026, langchain 1.4.8, fastmcp 4.1.6, mcp 2.3.0):

* `langchain.mcp` доступен только с дополнением `langchain[mcp]` (тянет
  `fastmcp`) и помечен как beta: при импорте выдаётся `LangChainBetaWarning`.
* Старый пакет `langchain-mcp-adapters` 0.3.2 в такой среде НЕ импортируется:
  его код ждёт `RequestContext` из `mcp.shared.context`, а `mcp` 2.3.0 его не
  отдаёт. Объявленная зависимость `mcp>=1.24,<2` этому противоречит.
* Сервер собран с `watch=True` (`src/ragraf/mcp_server.py:2163`), поэтому первый
  вызов после правки кода может сам запустить переиндексацию корпуса. Это не
  зависание: столько же длится переиндексация из CLI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

__all__ = ["server_config", "load_mcp_tools", "mcp_available", "MCP_UNAVAILABLE"]


def _project_root() -> Path | None:
    """Корень дерева RAGRAF: ищем вверх каталог с `src/ragraf/mcp_server.py`."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "src" / "ragraf" / "mcp_server.py").is_file():
            return candidate
    return None


def _launcher() -> Path | None:
    root = _project_root()
    if root is None:
        return None
    launcher = root / "integrations" / "langchain" / "ragraf_mcp.py"
    return launcher if launcher.is_file() else None


def server_config(config_path: str | Path | None = None, *,
                  python: str | None = None,
                  agent: str = "langchain") -> dict[str, Any]:
    """Конфигурация MCP-сервера RAGRAF для `MCPAdapter` (формат `mcpServers`).

    Интерпретатор можно задать явно (`python=`): это единственный способ, которым
    RAGRAF ставится в отдельной среде и не делит зависимости с процессом
    LangChain.
    """
    launcher = _launcher()
    if launcher is None:
        raise RuntimeError(
            "не найден запускатор integrations/langchain/ragraf_mcp.py: "
            "MCP-путь работает из дерева RAGRAF; для установленного пакета "
            "используйте команду ragraf-mcp")
    if config_path is None:
        config_path = os.environ.get("RAGRAF_CONFIG") or os.environ.get("RAGRAF_PROJECT")
    if not config_path:
        root = _project_root()
        candidate = (root / "config" / "ragraf.yaml") if root else None
        if candidate is not None and candidate.is_file():
            config_path = candidate
    args = [str(launcher)]
    if config_path:
        args.extend(["-p", str(config_path)])
    args.extend(["--agent", agent, "--agent-kind", "external"])
    env = {"RAGRAF_AGENT": agent, "RAGRAF_AGENT_KIND": "external"}
    if config_path:
        env["RAGRAF_PROJECT"] = str(config_path)
    return {
        "mcpServers": {
            "ragraf": {
                "command": python or sys.executable,
                "args": args,
                "env": env,
            }
        }
    }


def mcp_available() -> tuple[bool, str]:
    """Есть ли путь MCP: названа причина отказа, а не только «нет»."""
    try:
        import fastmcp  # noqa: F401, PLC0415
    except ImportError:
        return False, ("нет fastmcp: установите `pip install \"langchain[mcp]\"` "
                       "(дополнение тянет fastmcp и mcp 2.x)")
    try:
        from langchain.mcp import MCPAdapter  # noqa: F401, PLC0415
    except Exception as exc:  # noqa: BLE001 — причина называется целиком
        return False, f"langchain.mcp недоступен: {type(exc).__name__}: {exc}"
    return True, ""


MCP_UNAVAILABLE = ("MCP-путь недоступен: {reason}. Прямой вызов (RAGRAFRetriever, "
                   "build_tools) работает без него.")


async def load_mcp_tools(config_path: str | Path | None = None, *,
                         python: str | None = None,
                         agent: str = "langchain",
                         timeout: float = 120.0,
                         compact: bool = True) -> list[Any]:
    """Инструменты сервера RAGRAF как инструменты LangChain (асинхронные).

    Возвращает 74 инструмента, включая `ragraf_search`, `ragraf_symbol`,
    `ragraf_callers`, `ragraf_write_check`. Пустой список — это не «сервер без
    инструментов»: если сервер не поднялся, исключение называет причину.

    `compact=True` (по умолчанию) приводит ответ к той же форме, что у прямого
    вызова: адрес `путь:строка` в тексте. Без этого серверный ответ несёт хиты
    только в JSON-блоке, а в сводке их нет — то есть агент не получил бы цитату.
    `compact=False` оставляет сырой ответ сервера (нужно тем, кто разбирает
    `structuredContent` сам).
    """
    import asyncio  # noqa: PLC0415

    ok, reason = mcp_available()
    if not ok:
        raise RuntimeError(MCP_UNAVAILABLE.format(reason=reason))
    from langchain_core.tools import StructuredTool  # noqa: PLC0415
    from langchain.mcp import MCPAdapter  # noqa: PLC0415

    from .tools import format_mcp_output  # noqa: PLC0415 — избегаем цикла модулей

    adapter = MCPAdapter(server_config(config_path, python=python, agent=agent))
    tools = await asyncio.wait_for(adapter.list_tools(), timeout=timeout)
    if not compact:
        return list(tools)

    wrapped: list[Any] = []
    for tool in tools:
        async def call(_tool=tool, **kwargs):  # noqa: ANN003 — аргументы даёт схема
            return format_mcp_output(_tool.name, await _tool.ainvoke(kwargs))

        wrapped.append(StructuredTool.from_function(
            coroutine=call,
            name=tool.name,
            description=tool.description or tool.name,
            args_schema=tool.args_schema,
        ))
    return wrapped
