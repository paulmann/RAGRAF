"""Добавление настроек видеокарты в конфигурации корпусов.

Что добавляется в секцию `embedding` каждого корпуса:
  * use_gpu — считать ли векторы на видеокарте (по умолчанию да);
  * gpu_min_texts — порог окупаемости;
  * gpu_batch_size — размер партии на видеокарте;
  * device — конкретное устройство (auto — выбрать автоматически).

Почему значения разные для корпусов. Порог окупаемости — общий (он определён
разовой подготовкой карты), но размеры корпусов разные, поэтому полезно видеть
в конфиге, окупается ли карта на данном корпусе:

  * z.batteryservice.ru: около 1078 новых векторов при полной индексации.
    Это НИЖЕ порога 1800, поэтому карта на нём в основном не включается.
    Значение use_gpu оставляется включённым: при росте корпуса оно заработает
    само, а сейчас решает порог.
  * revoap.com: около 4000 векторов. Карта выгодна, замерено ускорение
    индексации со 143,7 с до 101,6 с.

Правка идёт точечной заменой с сохранением комментариев. Для каждого файла
создаётся резервная копия. Скрипт идемпотентен.
"""

from __future__ import annotations

import pathlib
import shutil
from datetime import datetime

REPO = pathlib.Path(__file__).resolve().parents[1]

# Значения по корпусам: (use_gpu, порог, партия, примечание)
VALUES = {
    "z-batteryservice": (
        True, 1800, 128,
        "# Векторов при полной индексации около 1078 — это НИЖЕ порога 1800,\n"
        "# поэтому видеокарта на этом корпусе обычно НЕ включается. Значение\n"
        "# use_gpu остаётся включённым: при росте корпуса оно заработает само.",
    ),
    "revoap": (
        True, 1800, 128,
        "# Векторов около 4000 — выше порога, видеокарта выгодна. Замерено:\n"
        "# индексация со 143,7 с до 101,6 с, стадия векторов быстрее в 2,31 раза.",
    ),
}

for name, (use_gpu, min_texts, batch, note) in VALUES.items():
    path = REPO / "config" / f"{name}.yaml"
    text = path.read_text(encoding="utf-8")

    if "use_gpu" in text:
        print(f"{name}: настройки видеокарты уже есть")
        continue

    # Настройки добавляются в конец секции embedding. Точка вставки —
    # строка `batch_size: 64` внутри этой секции: она есть в обоих конфигах
    # и всегда последняя в секции.
    old = """  dim: 384
  batch_size: 64"""
    if old not in text:
        print(f"{name}: ВНИМАНИЕ, секция embedding не найдена в ожидаемом виде")
        continue

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, path.with_name(f"{path.name}.bak.{stamp}-gpu"))

    new = old + f"""
  # --- вычислитель для кодирования векторов ---
  # ОБОСНОВАНИЕ ЗАМЕРАМИ на RTX 5080: само кодирование на видеокарте быстрее
  # в 38 раз (0,35 мс на текст против 13,7). Но есть разовая подготовка карты
  # под архитектуру sm_120 — около 26 с на запуск процесса. Поэтому карта
  # включается только когда работы больше порога окупаемости.
{note}
  use_gpu: {'true' if use_gpu else 'false'}
  gpu_min_texts: {min_texts}   # точка равенства по замерам — 1451 текст
  gpu_batch_size: {batch}
  # auto — выбрать видеокарту автоматически; cpu — только процессор;
  # cuda:0 — конкретное устройство.
  device: auto"""

    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"{name}: настройки видеокарты добавлены, копия "
          f"{path.name}.bak.{stamp}-gpu")

print("\n=== проверка ===")
import sys  # noqa: E402

sys.path.insert(0, str(REPO / "src"))
from ragraf.config import load  # noqa: E402

for name in ("z-batteryservice", "revoap"):
    cfg = load(REPO / "config" / f"{name}.yaml")
    emb = cfg.embedding
    print(f"  {name}: use_gpu={emb.use_gpu}, порог={emb.gpu_min_texts}, "
          f"партия={emb.gpu_batch_size}, device={emb.device}")
