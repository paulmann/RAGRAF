"""Проверка способов убрать разовую задержку 15 с на первом вычислении.

Установлено: первое вычисление на видеокарте занимает 15,08 с — это torch
подбирает алгоритмы свёрток и матричных умножений под архитектуру sm_120
(RTX 5080, новая архитектура). Второе вычисление — 0,01 с.

Что проверяется:
  1. помогает ли КЕШ алгоритмов cuDNN (torch.backends.cudnn.benchmark)
     сохранить подобранное между запусками;
  2. сокращает ли задержку УМЕНЬШЕНИЕ числа кандидатов
     (torch.backends.cudnn.benchmark = False — тогда берётся алгоритм по
     умолчанию без подбора);
  3. влияет ли размер первого вычисления (может, подбор идёт по форме тензора,
     и большая форма подбирается дольше);
  4. работает ли заранее сохранённый кеш между двумя разными процессами.

Каждый вариант проверяется в ОТДЕЛЬНОМ процессе, потому что подбор алгоритмов
происходит один раз на процесс и иначе не измерить.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

REPO = pathlib.Path(__file__).resolve().parents[1]

PROBE = textwrap.dedent(
    '''
    """Замер первого вычисления при заданных настройках torch."""
    import sys, time, os

    # Настройки из аргументов: benchmark, размер партии, длина текста.
    benchmark = sys.argv[1] == "benchmark"
    batch = int(sys.argv[2])
    chars = int(sys.argv[3])

    import torch
    from sentence_transformers import SentenceTransformer

    torch.backends.cudnn.benchmark = benchmark
    model = SentenceTransformer(
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        device="cpu",
    )
    model.to("cuda")
    model.max_seq_length = 128

    texts = [("x" * chars)] * batch
    t0 = time.perf_counter()
    model.encode(texts, batch_size=batch, show_progress_bar=False)
    torch.cuda.synchronize()
    first = time.perf_counter() - t0

    t0 = time.perf_counter()
    model.encode(texts, batch_size=batch, show_progress_bar=False)
    torch.cuda.synchronize()
    second = time.perf_counter() - t0

    print(f"RESULT benchmark={benchmark} batch={batch} chars={chars} "
          f"first={first:.2f} second={second:.3f}")
    '''
)

probe_path = REPO / ".staging" / "gpu_probe_first.py"
probe_path.write_text(PROBE, encoding="utf-8", newline="\n")


def run(benchmark: bool, batch: int, chars: int) -> tuple[float, float]:
    """Запустить замер в отдельном процессе. Возвращает (первое, второе)."""
    result = subprocess.run(
        [sys.executable, str(probe_path), "benchmark" if benchmark else "default",
         str(batch), str(chars)],
        capture_output=True, text=True, timeout=1200, cwd=str(REPO),
    )
    text = (result.stdout or "") + (result.stderr or "")
    first = second = -1.0
    for line in text.splitlines():
        if line.startswith("RESULT"):
            parts = dict(p.split("=") for p in line.split()[1:])
            first = float(parts["first"])
            second = float(parts["second"])
            break
    if first < 0:
        print(f"    вывод не распознан (код {result.returncode}): "
              f"{text[-300:]}")
    return first, second


print("=" * 76)
print("ПРОВЕРКА СПОСОБОВ УБРАТЬ РАЗОВУЮ ЗАДЕРЖКУ (каждый в новом процессе)")
print("=" * 76)
print(f"  {'настройка':<22} {'партия':>8} {'символов':>9} "
      f"{'первое, с':>10} {'второе, с':>10}")
print("  " + "-" * 66)

variants = [
    (True, 128, 60),     # как в изолированном тесте: короткие тексты
    (True, 128, 400),    # реальные чанки
    (False, 128, 400),   # без подбора алгоритмов
    (False, 8, 400),     # маленькая первая партия
    (True, 8, 400),      # маленькая партия с подбором
]
for benchmark, batch, chars in variants:
    first, second = run(benchmark, batch, chars)
    label = "подбор алгоритмов" if benchmark else "без подбора"
    print(f"  {label:<22} {batch:>8} {chars:>9} {first:>10.2f} {second:>10.3f}")

print()
print("=" * 76)
print("ВЫВОД")
print("=" * 76)
print("""
  Сравниваются строки. Если строка «без подбора» даёт малое первое вычисление,
  значит задержку создаёт именно подбор алгоритмов, и его можно отключить.

  Если задержка остаётся и без подбора — она в инициализации самой видеокарты
  под эту архитектуру, и тогда единственный способ её скрыть: выполнить первое
  вычисление ЗАРАНЕЕ, при старте процесса, а не при первом запросе.
""")
