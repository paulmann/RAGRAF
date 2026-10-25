"""Проверка двух способов скрыть инициализацию видеокарты (14,7 с).

Факт: первое вычисление на RTX 5080 (архитектура sm_120) занимает около 15 с,
и это НЕ подбор алгоритмов — отключение подбора не помогает. Значит это
инициализация: загрузка модулей ядра CUDA под новую архитектуру, подготовка
контекста, размещение библиотек.

Способ 1: ОДИН прогревочный вызов на минимальных данных, выполненный заранее.
  Гипотеза: если прогрев сделать на крошечном тензоре, он пройдёт быстрее,
  а инициализация всё равно завершится. Тогда полную задержку можно убрать
  из критического пути.

Способ 2: ONNX Runtime с CUDA-провайдером.
  Гипотеза: у ONNX своя инициализация CUDA, возможно быстрее. Проверяется,
  поднимается ли CUDAExecutionProvider вообще.

Оба варианта замеряются в отдельных процессах.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import textwrap

REPO = pathlib.Path(__file__).resolve().parents[1]

print("=" * 76)
print("СПОСОБ 1: прогрев на минимальных данных")
print("=" * 76)

PROBE = textwrap.dedent(
    '''
    import sys, time
    import torch

    # Замеряется только инициализация: минимальный тензор.
    t0 = time.perf_counter()
    a = torch.zeros(1, 1, device="cuda")
    _ = a + a
    torch.cuda.synchronize()
    init = time.perf_counter() - t0

    # Замеряется первый проход настоящей модели.
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        device="cuda",
    )
    t0 = time.perf_counter()
    model.encode(["прогрев"], show_progress_bar=False)
    torch.cuda.synchronize()
    first = time.perf_counter() - t0

    print(f"RESULT init={init:.2f} first_model={first:.2f}")
    '''
)

probe_path = REPO / ".staging" / "gpu_probe_warmup.py"
probe_path.write_text(PROBE, encoding="utf-8", newline="\n")

result = subprocess.run(
    [sys.executable, str(probe_path)], capture_output=True, text=True,
    timeout=1200, cwd=str(REPO),
)
text = (result.stdout or "") + (result.stderr or "")
for line in text.splitlines():
    if line.startswith("RESULT"):
        parts = dict(p.split("=") for p in line.split()[1:])
        print(f"  инициализация CUDA (маленький тензор): {float(parts['init']):.2f} с")
        print(f"  после неё первое вычисление модели:    "
              f"{float(parts['first_model']):.2f} с")
        print()
        total = float(parts["init"]) + float(parts["first_model"])
        print(f"  суммарно: {total:.2f} с")
        if float(parts["first_model"]) < 3:
            print("  ВЫВОД: прогрев маленьким тензором СНИМАЕТ основную задержку.")
            print("  Значит инициализацию можно вынести из критического пути.")
        else:
            print("  ВЫВОД: задержка остаётся даже после прогрева — она внутри")
            print("  первого прохода модели, и прогрев тензором её не снимает.")
        break
else:
    print(f"  вывод не распознан (код {result.returncode}): {text[-400:]}")

print()
print("=" * 76)
print("СПОСОБ 2: ONNX Runtime с CUDA-провайдером")
print("=" * 76)

spec = importlib.util.find_spec("onnxruntime")
print(f"  onnxruntime установлен: {spec is not None}")
if spec is not None:
    import onnxruntime as ort

    providers = ort.get_available_providers()
    print(f"  версия: {ort.__version__}")
    print(f"  провайдеры: {providers}")
    if "CUDAExecutionProvider" in providers:
        print("  CUDA-провайдер ЕСТЬ — можно использовать ONNX на видеокарте")
    else:
        print("  CUDA-провайдера НЕТ")
        print("  Для него нужен отдельный пакет onnxruntime-gpu с extras под CUDA")
        print("  (проверено: без extras он не приносит библиотек CUDA)")

print()
print("=" * 76)
print("ЧТО ЭТО ЗНАЧИТ ДЛЯ ПРОЕКТА")
print("=" * 76)
print("""
  Инициализация видеокарты — разовая плата за весь процесс. Отсюда правило:

    * при индексации КРУПНОГО корпуса (тысячи чанков) она окупается;
    * при индексации мелкого корпуса и при разовых командах — нет;
    * в долго живущем процессе (панель управления, сервер) платится один раз
      и далее не влияет ни на что.

  Поэтому переключатель вычислителя нужен не «вообще», а с оглядкой на размер
  работы: для этого в настройках есть и сам переключатель, и это объяснение.
""")
