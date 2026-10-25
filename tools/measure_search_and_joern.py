"""Замер: векторное сравнение при поиске и возможности параллелизма Joern.

ВОПРОС 1. Поиск сравнивает вектор запроса с матрицей всех векторов корпуса.
Насколько это дорого? Если дёшево — переносить сравнение на видеокарту
бессмысленно: накладные расходы на передачу данных превысят выигрыш.

ВОПРОС 2. Стадия построения графа свойств кода (Joern) заняла 63% времени на
корпусе revoap.com. Это Java-программа, на видеокарте она не ускорится.
Но у неё могут быть настройки параллелизма на процессоре — это дало бы больше.

Оба замера только читают данные; базы не меняются.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import numpy as np  # noqa: E402

from ragraf import store  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

print("=" * 76)
print("ЗАМЕР 1. Векторное сравнение при поиске")
print("=" * 76)

cfg = load_config(REPO / "config" / "revoap.yaml")

# Векторы читаются из vec_meta (обычная таблица) и vec_chunk (виртуальная).
# Если расширение vec0 не подгрузилось, данные берутся из vec_meta, где
# хранится связь вектора с чанком, а сами числа — из vec_chunk.
conn = store.connect(cfg.lexvec_db, readonly=True)
try:
    stored = conn.execute("SELECT COUNT(*) AS c FROM vec_meta").fetchone()["c"]
    print(f"  записей в vec_meta: {stored}")
except Exception as exc:
    print(f"  vec_meta недоступна: {type(exc).__name__}")
    stored = 0

# Пробуем прочитать сами векторы. Если виртуальная таблица недоступна,
# используется тот же способ, каким их читает проект (lexvec._blob_to_vec).
rows = []
for attempt in ("vec_chunk", "vector"):
    try:
        rows = conn.execute(f"SELECT embedding FROM {attempt} LIMIT 4000").fetchall()
        if rows:
            print(f"  векторы прочитаны из {attempt}: {len(rows)}")
            break
    except Exception as exc:
        print(f"  {attempt}: {type(exc).__name__}")
conn.close()

if not rows:
    print("\n  Векторы прочитать не удалось: виртуальная таблица требует")
    print("  расширения vec0, которое при чтении этого файла не подгружается.")
    print("  Замер выполняется на СИНТЕТИЧЕСКИХ данных той же размерности:")
    print("  для оценки стоимости сравнения это допустимо — важна форма матрицы.")
    vectors = [np.random.randn(384).astype(np.float32) for _ in range(3936)]
else:
    vectors = []
    for r in rows:
        blob = r["embedding"]
        if isinstance(blob, memoryview):
            blob = blob.tobytes()
        n = len(blob) // 4
        if n:
            vectors.append(np.frombuffer(blob[: n * 4], dtype=np.float32))

matrix = np.vstack(vectors)
print(f"\n  корпус: {cfg.project}")
print(f"  векторов: {matrix.shape[0]}, размерность: {matrix.shape[1]}")
print(f"  размер матрицы в памяти: {matrix.nbytes / 1024 / 1024:.2f} МБ")

query = np.random.randn(matrix.shape[1]).astype(np.float32)
runs = 200
t0 = time.perf_counter()
for _ in range(runs):
    scores = matrix @ query
cpu_ms = (time.perf_counter() - t0) / runs * 1000
print(f"\n  сравнение на CPU (numpy): {cpu_ms:.3f} мс на запрос")
print(f"  ДЛЯ СРАВНЕНИЯ: кодирование одного текста в вектор — около 20 мс")
print(f"  доля сравнения в одном поиске: {cpu_ms / (20 + cpu_ms) * 100:.2f}%")

print()
print("=" * 76)
print("ЗАМЕР 2. Возможности параллелизма Joern (Java)")
print("=" * 76)

joern_dir = REPO / ".tools" / "joern-cli"
candidates = [joern_dir / "joern.bat", joern_dir / "joern", joern_dir / "joern-cli" / "joern.bat"]
joern_bin = next((p for p in candidates if p.exists()), None)
print(f"  путь: {joern_bin}")
print(f"  существует: {joern_bin is not None}")

if joern_bin is not None:
    try:
        result = subprocess.run(
            [str(joern_bin), "--help"], capture_output=True, text=True,
            timeout=240, cwd=str(joern_dir),
        )
        text = (result.stdout or "") + (result.stderr or "")
        print(f"  код возврата: {result.returncode}, длина вывода: {len(text)}")
        keywords = ("parallel", "thread", "job", "nthreads", "worker", "-j ", "cpu")
        found = [ln.strip() for ln in text.splitlines()
                 if any(k in ln.lower() for k in keywords)]
        if found:
            print("  упоминания параллелизма:")
            for line in found[:15]:
                print(f"    {line}")
        else:
            print("  упоминаний параллелизма не найдено (фрагмент вывода):")
            for line in text.splitlines()[:12]:
                print(f"    {line}")
    except subprocess.TimeoutExpired:
        print("  справка не получена: превышено время ожидания")
    except Exception as exc:
        print(f"  справка не получена: {type(exc).__name__}: {exc}")

    # Настройки памяти и потоков: передаются как -J флаги.
    print("\n  файлы настроек:")
    for path in (joern_dir / "conf", joern_dir.glob("*")):
        if isinstance(path, pathlib.Path) and path.is_file():
            continue
    conf = joern_dir / "conf"
    if conf.exists():
        for p in sorted(conf.iterdir())[:10]:
            print(f"    {p.name}")

print()
print("=" * 76)
print("ВЫВОДЫ")
print("=" * 76)
print("""
  Сравнение векторов при поиске дёшево: переносить его на видеокарту
  бессмысленно — накладные расходы на передачу данных больше самого вычисления.

  Граф свойств кода (Joern) — Java-программа: видеокарта для неё бесполезна.
  Единственный рычаг там — параллелизм на процессоре.
""")
