"""Разложение разовой задержки 24 секунды по частям.

Факт: первый вызов Embedder занял 24,4 с, последующие — 0,25 с. То есть
разовая составляющая 24,2 с. При этом ранее загрузка той же модели заняла
4,75 с. Значит 24 с — не только загрузка весов.

Проверяются по отдельности, с замером каждого шага:
  1. импорт torch;
  2. импорт sentence_transformers;
  3. чтение весов модели с диска;
  4. перенос модели на видеокарту;
  5. ПЕРВОЕ вычисление на видеокарте (здесь torch подбирает алгоритмы
     для конкретной архитектуры — а RTX 5080 это sm_120, новая архитектура,
     и подбор может быть дорогим);
  6. последующие вычисления.

От результата зависит решение: если дорого именно первое вычисление, его надо
выполнять один раз в фоне, а не при первом запросе пользователя.
"""

from __future__ import annotations

import time

model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
steps: list[tuple[str, float]] = []


def step(label: str):
    """Контекстный менеджер для замера шага."""
    class _Timer:
        def __enter__(self):
            self.t0 = time.perf_counter()
            return self

        def __exit__(self, *exc):
            seconds = time.perf_counter() - self.t0
            steps.append((label, seconds))
            print(f"  {label:<48} {seconds:>8.2f} с")

    return _Timer()


print("=" * 76)
print("РАЗЛОЖЕНИЕ РАЗОВОЙ ЗАДЕРЖКИ ПО ШАГАМ (новый процесс)")
print("=" * 76)

with step("импорт torch"):
    import torch

with step("импорт sentence_transformers"):
    from sentence_transformers import SentenceTransformer

with step("чтение весов модели с диска (на процессор)"):
    model = SentenceTransformer(model_name, device="cpu")

with step("перенос модели на видеокарту"):
    model.to("cuda")

with step("ПЕРВОЕ вычисление на видеокарте (подбор алгоритмов)"):
    small = ["проверка работы видеокарты"] * 8
    model.encode(small, batch_size=8, show_progress_bar=False)
    torch.cuda.synchronize()

with step("второе вычисление (алгоритмы уже подобраны)"):
    model.encode(small, batch_size=8, show_progress_bar=False)
    torch.cuda.synchronize()

with step("кодирование 717 текстов"):
    texts = ["<?php function test(): void {}"] * 717
    model.encode(texts, batch_size=128, show_progress_bar=False)
    torch.cuda.synchronize()

print()
print("=" * 76)
print("СВОДКА")
print("=" * 76)
total = sum(s for _, s in steps)
for label, seconds in steps:
    share = seconds / total * 100 if total else 0
    print(f"  {label:<48} {seconds:>7.2f} с {share:>5.1f}%")
print(f"  {'ВСЕГО':<48} {total:>7.2f} с")

print()
print("=" * 76)
print("ЧТО ДЕЛАТЬ")
print("=" * 76)
print("""
  Смотрится самая дорогая строка. Возможные решения по ней:

  * если дорого «первое вычисление» — выполнять его один раз при старте
    процесса в фоне, чтобы к первому запросу индексации видеокарта была готова;
  * если дорого «чтение весов» — сохранить модель в локальный формат и грузить
    его вместо повторного разбора;
  * если дорог «перенос на видеокарту» — держать модель в памяти процесса
    между операциями (для командной строки это не поможет, а для сервера панели
    помогает полностью).
""")
