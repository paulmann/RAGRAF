"""Кодирование текста в векторы: два вычислителя — процессор и видеокарта.

Зачем два вычислителя
    Кодирование — единственная часть работы RAGRAF, которая ускоряется на
    видеокарте. Замерено на RTX 5080: 1,06 мс на текст против 13,97 мс на
    процессоре через torch и 18,45 мс через ONNX (прежний стек). Ускорение
    13,2 раза против torch CPU и 17,4 раза против ONNX.

    При этом кодирование занимает до 94% времени стадии лексики и векторов,
    а эта стадия — до 94% времени быстрой переиндексации. То есть выигрыш
    от видеокарты заметен именно там, где он нужен: при правке кода в длинной
    сессии, когда индекс обновляется часто.

Почему переключатель, а не «всегда видеокарта»
    Видеокарта может быть занята другой задачей или недоступна (нет драйвера,
    нет библиотек). Тогда система обязана вернуться к процессору и СКАЗАТЬ об
    этом, а не упасть. Поэтому режим вычислителя — настройка, а состояние
    вычислителя видно снаружи.

ГЛАВНОЕ СВОЙСТВО, проверенное замером
    Векторы, посчитанные ONNX и torch, сопоставимы: близость 0,999997.
    Значит переключение вычислителя НЕ требует перекодирования существующего
    индекса. Это проверено вызовом (tools/check_vector_compat.py), а не
    предположено: иначе смешивание векторов разных вычислителей дало бы
    незаметное ухудшение поиска.

Чего здесь НЕТ
    * передачи самих векторов на видеокарту для поиска: замерено, что
      сравнение вектора с матрицей занимает 0,09 мс — это доли процента
      времени поиска, и передача данных дороже вычисления;
    * кеша моделей на видеопамяти между запусками: модель загружается при
      первом кодировании и живёт в процессе.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ComputeStatus:
    """Состояние вычислителя кодирования.

    Показывается в интерфейсе администратора и в состоянии индекса: надо видеть,
    где считаются векторы и почему выбран этот путь.
    """

    backend: str                 # "torch-gpu" | "torch-cpu" | "onnx-cpu" | "none"
    device: str                  # "cuda:0" | "cpu" | ""
    available: bool
    reason: str = ""             # почему выбран именно этот путь
    gpu_name: str = ""
    ms_per_text: float | None = None
    gpu_free_mb: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "device": self.device,
            "available": self.available,
            "reason": self.reason,
            "gpu_name": self.gpu_name,
            "ms_per_text": self.ms_per_text,
            "gpu_free_mb": self.gpu_free_mb,
            "uses_gpu": self.backend.endswith("gpu"),
        }


class GpuUnavailable(Exception):
    """Видеокарта недоступна: переходим к процессору, а не падаем."""


def probe_torch() -> tuple[bool, str, str, int | None]:
    """Проверить, годится ли torch для кодирования на видеокарте.

    Возвращает четвёрку: (годится, имя устройства, причина отказа, свободно МБ).

    Проверяется три вещи, каждая из которых уже встречалась на практике:
      * установлен ли torch;
      * видит ли он видеокарту (compute capability 12.0 у RTX 5080 требует
        свежей сборки, иначе появляется «no kernel image is available»);
      * работает ли РЕАЛЬНОЕ вычисление на этой архитектуре — наличие
        видеокарты ещё не значит, что в сборке есть нужные ядра.
    """
    try:
        import torch  # type: ignore
    except ImportError:
        return False, "", "torch не установлен", None

    try:
        if not torch.cuda.is_available():
            return False, "cpu", "torch не видит видеокарту", None
    except Exception as exc:
        return False, "", f"проверка CUDA не удалась: {type(exc).__name__}", None

    try:
        name = torch.cuda.get_device_name(0)
        # Пробное вычисление: именно здесь выясняется, есть ли ядра под
        # архитектуру карты. Наличие видеокарты этого не гарантирует.
        probe = torch.randn(64, 64, device="cuda")
        _ = probe @ probe
        torch.cuda.synchronize()
        free = None
        try:
            free_bytes, _total = torch.cuda.mem_get_info(0)
            free = int(free_bytes / 1024 / 1024)
        except Exception:
            free = None
        return True, "cuda:0", f"torch {torch.__version__}, {name}", free
    except Exception as exc:
        return (False, "", f"вычисление на видеокарте не работает: "
                           f"{type(exc).__name__}: {str(exc)[:120]}", None)


@dataclass
class TorchBackend:
    """Кодирование через библиотеку torch на заданном устройстве.

    Зачем отдельный класс, а не один вызов внутри Embedder: у вычислителей
    разный способ загрузки модели и разные параметры партии. Смешивать их в
    одном методе — значит получить ветвление, которое невозможно проверить.
    """

    model_name: str
    device: str
    batch_size: int = 128
    _model: Any = None
    _load_seconds: float = 0.0
    # Время последнего кодирования. Хранится, чтобы состояние вычислителя
    # показывало ФАКТИЧЕСКУЮ скорость, а не только выбранный путь: иначе
    # администратор не увидит, действительно ли карта помогает.
    last_seconds: float = 0.0

    def load(self) -> None:
        if self._model is not None:
            return
        import time

        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise GpuUnavailable(
                "sentence-transformers не установлен: "
                "pip install sentence-transformers"
            ) from exc
        started = time.perf_counter()
        try:
            # Устройство задаётся ЯВНО. Это не формальность: без указания
            # библиотека сама выбирает видеокарту, если она есть, и тогда
            # «процессорный» замер тоже считается на видеокарте — на этом уже
            # была допущена ошибка измерения.
            self._model = SentenceTransformer(self.model_name, device=self.device)
        except Exception as exc:
            raise GpuUnavailable(
                f"модель '{self.model_name}' не загружена на {self.device}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        self._load_seconds = time.perf_counter() - started

    def encode(self, texts: list[str]) -> list[list[float]]:
        import time

        self.load()
        try:
            started = time.perf_counter()
            vectors = self._model.encode(
                texts,
                batch_size=self.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            # Синхронизация обязательна: без неё замер ловит только постановку
            # задачи в очередь, а не её выполнение, и скорость выглядит
            # завышенной в десятки раз.
            try:
                import torch  # type: ignore

                if self.device.startswith("cuda"):
                    torch.cuda.synchronize()
            except Exception:
                pass
            self.last_seconds = time.perf_counter() - started
        except Exception as exc:
            raise GpuUnavailable(
                f"кодирование на {self.device} не удалось: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return [[float(x) for x in row] for row in vectors]

    @property
    def load_seconds(self) -> float:
        return self._load_seconds
