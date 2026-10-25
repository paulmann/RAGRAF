"""КРИТИЧНАЯ проверка: совместимы ли векторы от ONNX и от torch.

Почему это решает всё. Сейчас индекс заполнен векторами, посчитанными через
ONNX (fastembed). Если включить видеокарту и считать через torch, новые векторы
должны быть СОПОСТАВИМЫ с уже лежащими в базе. Если они окажутся из другого
пространства, поиск начнёт возвращать мусор: часть векторов будет «близка»
к запросу только из-за расхождения способов счёта, а не из-за смысла.

Это опасный класс ошибок: он не даёт ни исключения, ни явного сбоя — просто
результаты поиска становятся хуже, и заметить это можно только проверкой.

Что проверяется:
  1. считаются векторы для одних и тех же текстов двумя способами;
  2. сравнивается косинусная близость: если она ниже 0,999, смешивать нельзя;
  3. проверяется, что нормы векторов сопоставимы;
  4. делается вывод: можно ли переключать backend на существующем индексе
     или требуется полное перекодирование.

Ничего не меняет: только считает в память.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import numpy as np  # noqa: E402

from ragraf.config import load as load_config  # noqa: E402

cfg = load_config(REPO / "config" / "z-batteryservice.yaml")
model_name = cfg.embedding.model
print(f"модель: {model_name}")
print(f"размерность по конфигу: {cfg.embedding.dim}")

# Тексты: разные по содержанию, как реальные чанки.
texts = [
    "<?php function z_agr_select(string $id, string $name, array $items): string "
    "{ $out = ''; foreach ($items as $k => $v) { $out .= z_e($k); } return $out; }",
    "CREATE TABLE DocPayments (ID int NOT NULL AUTO_INCREMENT, No varchar(32), "
    "Status tinyint, InitiatorID int, PRIMARY KEY (ID));",
    ".z-agr-card__head { display: flex; justify-content: space-between; "
    "padding: 12px 16px; border-bottom: 1px solid var(--z-border); }",
    "Роль руководитель подразделения согласует договор первым шагом маршрута.",
    "SELECT COUNT(*) FROM worklog WHERE UserID = ? AND WorkDate >= ?",
]

print()
print("=" * 76)
print("1. Векторы через ONNX (текущий стек проекта)")
print("=" * 76)
onnx_vectors = None
try:
    from ragraf.embeddings import Embedder

    emb = Embedder(cfg, allow_download=False)
    onnx_vectors = np.array(emb.embed(texts), dtype=np.float32)
    print(f"  получено: {onnx_vectors.shape}")
    print(f"  нормы: {[round(float(np.linalg.norm(v)), 3) for v in onnx_vectors]}")
except Exception as exc:
    print(f"  не удалось: {type(exc).__name__}: {exc}")

print()
print("=" * 76)
print("2. Векторы через torch на процессоре")
print("=" * 76)
torch_cpu_vectors = None
try:
    from sentence_transformers import SentenceTransformer

    m_cpu = SentenceTransformer(model_name, device="cpu")
    torch_cpu_vectors = np.array(
        m_cpu.encode(texts, convert_to_numpy=True), dtype=np.float32
    )
    print(f"  получено: {torch_cpu_vectors.shape}")
    print(f"  нормы: {[round(float(np.linalg.norm(v)), 3) for v in torch_cpu_vectors]}")
except Exception as exc:
    print(f"  не удалось: {type(exc).__name__}: {exc}")

print()
print("=" * 76)
print("3. Векторы через torch на видеокарте")
print("=" * 76)
torch_gpu_vectors = None
try:
    from sentence_transformers import SentenceTransformer

    m_gpu = SentenceTransformer(model_name, device="cuda")
    torch_gpu_vectors = np.array(
        m_gpu.encode(texts, convert_to_numpy=True), dtype=np.float32
    )
    print(f"  получено: {torch_gpu_vectors.shape}")
    print(f"  нормы: {[round(float(np.linalg.norm(v)), 3) for v in torch_gpu_vectors]}")
except Exception as exc:
    print(f"  не удалось: {type(exc).__name__}: {exc}")


def cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Косинусная близость построчно."""
    na = a / np.linalg.norm(a, axis=1, keepdims=True)
    nb = b / np.linalg.norm(b, axis=1, keepdims=True)
    return (na * nb).sum(axis=1)


print()
print("=" * 76)
print("4. ГЛАВНАЯ ПРОВЕРКА: совместимость пространств")
print("=" * 76)

pairs = [
    ("ONNX против torch(CPU)", onnx_vectors, torch_cpu_vectors),
    ("torch(CPU) против torch(GPU)", torch_cpu_vectors, torch_gpu_vectors),
    ("ONNX против torch(GPU)", onnx_vectors, torch_gpu_vectors),
]

verdicts: dict[str, float] = {}
for label, a, b in pairs:
    if a is None or b is None:
        print(f"  {label}: не проверено (нет данных)")
        continue
    if a.shape != b.shape:
        print(f"  {label}: формы разные {a.shape} и {b.shape} — НЕСОВМЕСТИМЫ")
        continue
    sim = cosine(a, b)
    verdicts[label] = float(sim.min())
    print(f"\n  {label}:")
    print(f"    минимальная близость: {sim.min():.6f}")
    print(f"    средняя близость:     {sim.mean():.6f}")
    mark = "СОВМЕСТИМЫ" if sim.min() > 0.999 else (
        "близки, но различаются" if sim.min() > 0.99 else "НЕСОВМЕСТИМЫ"
    )
    print(f"    вывод: {mark}")

print()
print("=" * 76)
print("5. ВЫВОД ДЛЯ РЕШЕНИЯ")
print("=" * 76)
compatible = verdicts.get("ONNX против torch(GPU)", 0)
if compatible > 0.999:
    print("""
  Векторы совместимы: можно переключать вычислитель на существующем индексе
  без перекодирования. Разница ниже порога, значимого для поиска.
""")
elif compatible > 0.99:
    print(f"""
  Векторы очень близки ({compatible:.6f}), но не совпадают точно.
  Практическое следствие: перемешивание векторов разных вычислителей в одном
  индексе даст небольшое искажение ранжирования. Безопасный путь — при смене
  вычислителя перекодировать ВСЕ векторы корпуса, а не только новые.
  Стоимость перекодирования: см. замеры скорости кодирования.
""")
else:
    print(f"""
  ВНИМАНИЕ: векторы НЕСОВМЕСТИМЫ (близость {compatible:.6f}).
  Переключать вычислитель без полного перекодирования НЕЛЬЗЯ: поиск начнёт
  возвращать неверные результаты без каких-либо ошибок.
""")
