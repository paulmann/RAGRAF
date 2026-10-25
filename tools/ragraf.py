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

# Атрибуция вызова (шаг 0.5 плана). Замер 23.10.2026: 731 запись из 912 в
# журнале корпуса RAGRAF имела agent="unknown", потому что командный путь не
# называл себя. Явное имя здесь честнее пустоты: это действительно вызов из
# командной строки, а не агент с именем «unknown». Значение из окружения
# сильнее: если вызывающий назвался сам, его имя и сохраняется.
import os as _os  # noqa: E402

_os.environ.setdefault("RAGRAF_AGENT", "ragraf-cli")
_os.environ.setdefault("RAGRAF_AGENT_KIND", "internal")

if __name__ == "__main__":
    raise SystemExit(main())
