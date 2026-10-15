"""Launcher MCP-сервера RAGRAF (работает и без установки пакета, и после установки).

Использование (в конфигурации MCP-клиента):
    python <repo>/tools/ragraf-mcp.py --project <repo>/config/<корпус>.yaml

`--project` не обязателен: без него сервер определяет корпус по рабочему
каталогу через реестр разработок, а если не нашёл — поднимается всё равно,
отвечает на `catalog` и `use_project` и честно отказывает остальным операциям,
называя причину. Так один сервер обслуживает несколько проектов
(`src/ragraf/mcp_server.py`: `main()` и `_resolve_default_project`).

Каталог этого файла убирается из sys.path до импорта: имя файла (ragraf-mcp.py)
не совпадает с именем пакета, но в каталоге tools/ лежат и другие скрипты,
и чтобы источник пакета был один (src/), путь до пакета ставится первым явно.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
_TOOLS_DIR = str(Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if str(Path(p or ".").resolve()) != _TOOLS_DIR]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
