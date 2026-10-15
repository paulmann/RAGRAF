"""Launcher RAGRAF: работает и без установки пакета, и после установки.

Зачем нужен: в окружении выполнения переменная PYTHONPATH отфильтровывается,
поэтому вместо неё путь до src/ добавляется явно.

Отдельная тонкость — имя файла совпадает с именем пакета. При запуске
`python tools/ragraf.py` интерпретатор кладёт каталог скрипта (`tools/`) в
sys.path[0], и после `pip install -e .` импорт `ragraf` начинал находить
САМ ЭТОТ ФАЙЛ, а не пакет из src/: вывод падал на строке импорта
(`'ragraf' is not a package`). Измерено 12.10.2026: без установки обёртка
работала, с установкой — нет, и признак не был виден до этой проверки.
Поэтому каталог скрипта убирается из пути до импорта: источником пакета
обязан быть только src/.

Использование:
    python tools/ragraf.py --project config/revoap.yaml selftest
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

# Каталог этого файла выкидывается из sys.path, если он там есть: иначе файл
# `tools/ragraf.py` затеняет пакет `src/ragraf` при установленном пакете.
_TOOLS_DIR = str(Path(__file__).resolve().parent)
sys.path[:] = [p for p in sys.path if str(Path(p or ".").resolve()) != _TOOLS_DIR]

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
