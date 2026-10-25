"""Запуск интерфейса администратора RAGRAF.

Назначение
    Отдельная точка входа: администрирование — не операция фасада, и смешивать
    его с CLI для агента не нужно. Агент работает с индексом, администратор —
    с настройками.

Почему отдельный сервер, а не страница в существующем
    Панель управляет НАСТРОЙКАМИ и показывает размеры баз; она не должна быть
    доступна агенту и не должна попадать в корпус разработки. Отдельный процесс
    на localhost решает обо: сеть не слушается, файлы панели лежат вне корпусов.

Запуск
    python tools/ragraf-admin.py
    python tools/ragraf-admin.py --port=9000 --no-browser
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
_TOOLS_DIR = str(pathlib.Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if str(pathlib.Path(p or ".").resolve()) != _TOOLS_DIR]
sys.path.insert(0, str(REPO / "src"))

from ragraf.admin_server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
