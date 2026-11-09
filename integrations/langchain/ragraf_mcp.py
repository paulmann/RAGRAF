# -*- coding: utf-8 -*-
"""Запуск MCP-сервера RAGRAF любым интерпретатором, у которого есть зависимости.

Зачем файл-запускатор, а не `ragraf-mcp` напрямую. В этом дереве пакет `ragraf`
намеренно не устанавливается (`tools/ragraf.py:27-33` подставляет `src` в
`sys.path`, потому что `PYTHONPATH` в окружении выполнения отфильтровывается).
MCP-клиент запускает сервер командой, и передать `sys.path` через переменную
окружения нельзя — значит точка входа должна сама найти `src`.

Использование (так его зовёт MCP-клиент LangChain):

    python integrations/langchain/ragraf_mcp.py -p config/ragraf.yaml --agent langchain

Коды возврата и аргументы — те же, что у `ragraf.mcp_server.main`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    src = ROOT / "src"
    if (src / "ragraf" / "mcp_server.py").is_file() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from ragraf.mcp_server import main as server_main  # noqa: PLC0415

    return server_main()


if __name__ == "__main__":
    raise SystemExit(main())
