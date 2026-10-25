"""Локальные эмбеддинги без torch (ONNX через fastembed).

Модель релиза 1: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384),
решение заказчика.

Слой изолирован: при отсутствии модели/библиотеки векторная часть отключается,
лексический поиск продолжает работать (архитектура §15 — «не скачалась модель»).

О сертификатах: в сети с MITM-прокси (корпоративный SSL-инспектор) стандартный
модуль ssl не находит корневой сертификат, и загрузка модели падает с
CERTIFICATE_VERIFY_FAILED. Здесь подключается системное хранилище сертификатов
Windows (truststore) — проверка подлинности НЕ ослабляется.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import ProjectConfig
from .compute import ComputeStatus


def _use_system_trust_store() -> bool:
    """Подключить системное хранилище сертификатов (Windows) для HTTPS.

    Зачем: в сети с SSL-инспекцией стандартный ssl-модуль Python не видит корневой
    сертификат, и загрузка модели падает с CERTIFICATE_VERIFY_FAILED.
    Модуль truststore берёт корни из хранилища ОС — проверка подлинности при этом
    сохраняется полностью (не подменяется на unverified-контекст).
    """
    try:
        import truststore  # type: ignore

        truststore.inject_into_ssl()
        return True
    except Exception:
        return False


class EmbeddingUnavailable(Exception):
    """Модель или библиотека недоступны. Не ошибка сборки: векторный слой опционален."""


@dataclass
class EmbeddingStatus:
    available: bool
    model: str
    dim: int
    reason: str | None = None


class Embedder:
    """Обёртка над fastembed с ленивой загрузкой модели.

    allow_download=False (по умолчанию для операций ЧТЕНИЯ): модель берётся только из
    кэша. Операции поиска не должны инициировать загрузку — иначе обычный запрос
    агента неожиданно тянет сотни мегабайт из сети.
    """

    def __init__(self, cfg: ProjectConfig, *, allow_download: bool = False) -> None:
        self.cfg = cfg
        self.model_name = cfg.embedding.model
        self.dim = cfg.embedding.dim
        self.allow_download = allow_download
        self._model = None
        self._reason: str | None = None
        # Вычислитель определяется лениво, при первом кодировании: тогда
        # операции чтения, которым векторы не нужны, не тратят время на
        # проверку видеокарты и загрузку библиотек.
        self._backend_name: str = ""
        self._backend = None
        self._compute_status = None

    @property
    def cache_dir(self) -> Path:
        """Общий кэш моделей: не дублируется в каждом корпусе.

        Путь: <домашний каталог>/.ragraf/models (переопределяется RAGRAF_MODEL_CACHE).
        """
        import os

        override = os.environ.get("RAGRAF_MODEL_CACHE")
        base = Path(override) if override else (Path.home() / ".ragraf" / "models")
        base.mkdir(parents=True, exist_ok=True)
        return base

    def cached(self) -> bool:
        """Есть ли модель в кэше (без обращения к сети)."""
        import os

        if os.environ.get("RAGRAF_MODEL_CACHE_FORCE"):
            return True
        slug = self.model_name.replace("/", "--")
        needle = slug.split("--")[-1].lower()
        try:
            for entry in self.cache_dir.iterdir():
                if entry.is_dir() and needle[:18] in entry.name.lower():
                    return True
        except OSError:
            return False
        return False

    # ---- состояние ----

    def status(self) -> EmbeddingStatus:
        if self._model is not None:
            return EmbeddingStatus(True, self.model_name, self.dim)
        try:
            self._load()
            return EmbeddingStatus(True, self.model_name, self.dim)
        except EmbeddingUnavailable as exc:
            self._reason = str(exc)
            return EmbeddingStatus(False, self.model_name, self.dim, str(exc))

    def available(self) -> bool:
        return self.status().available

    # ---- загрузка ----

    def _load(self):
        if self._model is not None:
            return self._model
        if not self.allow_download and not self.cached():
            raise EmbeddingUnavailable(
                f"модель '{self.model_name}' нет в кэше {self.cache_dir}; "
                "операции чтения не скачивают модели — запустите индексацию с --vectors"
            )
        try:
            from fastembed import TextEmbedding  # type: ignore
        except Exception as exc:
            raise EmbeddingUnavailable(f"fastembed недоступен: {type(exc).__name__}") from exc
        _use_system_trust_store()
        try:
            self._model = TextEmbedding(
                model_name=self.model_name, cache_dir=str(self.cache_dir)
            )
        except Exception as exc:
            raise EmbeddingUnavailable(
                f"модель '{self.model_name}' не загружена: {type(exc).__name__}: {exc}"
            ) from exc
        # Проверка размерности — иначе схема vec0 окажется несовместимой.
        probe = next(iter(self._model.embed(["проверка размерности"])))
        got = len(probe)
        if got != self.dim:
            raise EmbeddingUnavailable(
                f"модель отдаёт {got} измерений, конфигурация ожидает {self.dim}"
            )
        return self._model

    # ---- кодирование ----

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Кодировать тексты в векторы.

        Выбор вычислителя происходит здесь, один раз на процесс, и учитывает
        ОБЪЁМ работы:

          * если текстов мало (меньше порога окупаемости), считается на
            процессоре: разовая подготовка видеокарты (15 с) дороже выигрыша;
          * если текстов много — на видеокарте: она в 70 раз быстрее на самом
            кодировании, и подготовка окупается;
          * если видеокарта недоступна — процессор с записью причины.

        Почему порог, а не «всегда видеокарта». Замерено: подготовка видеокарты
        под архитектуру sm_120 занимает 15 с, а кодирование — 0,35 мс на текст.
        На корпусе из 300 чанков выигрыш (0,3 с) меньше платы (15 с),
        то есть видеокарта была бы ВРЕДНА. Поэтому решение зависит от объёма.
        """
        if not texts:
            return []

        backend, skip_reason = self._pick_backend(len(texts))
        if backend is not None:
            try:
                result = backend.encode(texts)
                # Запоминается фактическое время, чтобы состояние показывало
                # реальную скорость, а не только выбранный путь.
                if self._compute_status is not None and result:
                    count = max(1, len(texts))
                    self._compute_status.ms_per_text = round(
                        backend.last_seconds / count * 1000, 3
                    )
                return result
            except Exception as exc:
                # Видеокарта отвалилась в процессе: фиксируем причину
                # и считаем на процессоре, а не падаем.
                self._note_fallback(f"вычислитель отказал: {type(exc).__name__}: {exc}")
                self._backend = None
                self._backend_name = ""

        model = self._load()
        batch = max(1, self.cfg.embedding.batch_size)
        out: list[list[float]] = []
        try:
            for start in range(0, len(texts), batch):
                part = texts[start : start + batch]
                for vec in model.embed(part):
                    out.append([float(x) for x in vec])
        except Exception as exc:
            raise EmbeddingUnavailable(f"ошибка кодирования: {type(exc).__name__}: {exc}") from exc
        return out

    # ---- вычислитель ----

    def _pick_backend(self, text_count: int = 0):
        """Выбрать вычислитель. Возвращает (backend | None, причина отказа).

        Первая проверка — по объёму работы, ДО обращения к видеокарте: если
        текстов мало, незачем тратить 15 с на её подготовку.
        """
        if self._backend is not None:
            return self._backend, ""
        if self._backend_name == "onnx-cpu":
            return None, ""

        emb = self.cfg.embedding
        use_gpu = bool(getattr(emb, "use_gpu", False))
        device_cfg = str(getattr(emb, "device", "auto") or "auto")
        min_texts = int(getattr(emb, "gpu_min_texts", 1000) or 1000)

        if not use_gpu or device_cfg == "cpu":
            self._backend_name = "onnx-cpu"
            self._compute_status = ComputeStatus(
                backend="onnx-cpu", device="cpu", available=True,
                reason="видеокарта выключена в настройках",
            )
            return None, ""

        # Порог окупаемости. Считается только для НОВЫХ текстов: если часть
        # векторов переиспользуется, индексатор передаёт сюда уже остаток.
        if text_count and text_count < min_texts:
            self._backend_name = "onnx-cpu"
            self._compute_status = ComputeStatus(
                backend="onnx-cpu", device="cpu", available=True,
                reason=(f"текстов {text_count}, меньше порога окупаемости "
                        f"{min_texts}: подготовка видеокарты (около 15 с) дороже "
                        f"выигрыша"),
            )
            return None, "мало работы"

        from .compute import GpuUnavailable, TorchBackend, probe_torch

        ok, device, reason, free_mb = probe_torch()
        if not ok:
            self._note_fallback(reason)
            return None, reason

        # Если в настройках задано КОНКРЕТНОЕ устройство, оно должно совпасть
        # с обнаруженным. Проверка нужна потому, что настройка может остаться
        # от другой машины (например `cuda:1`, где второй карты нет), и тогда
        # молчаливое использование первого устройства было бы неправильным:
        # администратор считал бы, что работает выбранное.
        if device_cfg not in ("auto", "gpu"):
            wanted = device_cfg
            if device_cfg.startswith("cuda") and not device.startswith("cuda"):
                self._note_fallback(
                    f"в настройках указано «{wanted}», но видеокарта недоступна"
                )
                return None, "устройство недоступно"
            if device_cfg == "cuda:0":
                device = "cuda:0"
            elif device_cfg.startswith("cuda:"):
                # Устройство с другим номером: проверяется его существование
                # пробным вычислением, иначе получится отказ в середине работы.
                try:
                    import torch  # type: ignore

                    index = int(wanted.split(":", 1)[1])
                    if index >= torch.cuda.device_count():
                        self._note_fallback(
                            f"в настройках указано «{wanted}», но таких устройств "
                            f"только {torch.cuda.device_count()}"
                        )
                        return None, "устройства нет"
                    probe = torch.randn(32, 32, device=wanted)
                    _ = probe @ probe
                    torch.cuda.synchronize()
                    device = wanted
                except Exception as exc:
                    self._note_fallback(
                        f"устройство «{wanted}» не работает: {type(exc).__name__}"
                    )
                    return None, "устройство не работает"

        # Проверка свободной памяти: если видеокарта занята чужой задачей,
        # лучше считать на процессоре, чем получить отказ в середине партии.
        if free_mb is not None and free_mb < 512:
            self._note_fallback(
                f"на видеокарте свободно {free_mb} МБ — мало, считаем на процессоре"
            )
            return None, "мало видеопамяти"

        backend = TorchBackend(
            model_name=self.model_name,
            device=device,
            batch_size=int(getattr(emb, "gpu_batch_size", 128) or 128),
        )
        self._backend = backend
        self._backend_name = "torch-gpu"
        self._compute_status = ComputeStatus(
            backend="torch-gpu", device=device, available=True,
            reason=(f"видеокарта доступна, работы достаточно ({text_count} текстов): "
                    f"{reason}"),
            gpu_name=reason, gpu_free_mb=free_mb,
        )
        return backend, ""

    def _note_fallback(self, reason: str) -> None:
        """Зафиксировать переход на процессор и причину."""
        self._backend_name = "onnx-cpu"
        self._compute_status = ComputeStatus(
            backend="onnx-cpu", device="cpu", available=True, reason=reason,
        )

    def compute_status(self) -> ComputeStatus:
        """Состояние вычислителя: где считаются векторы и почему.

        Показывается в интерфейсе администратора. Вызов не запускает видеокарту
        принудительно: если вычислитель ещё не выбран, возвращается описание
        настроек, а не факт.
        """
        if self._compute_status is not None:
            return self._compute_status
        emb = self.cfg.embedding
        use_gpu = bool(getattr(emb, "use_gpu", False))
        return ComputeStatus(
            backend="не выбран", device="", available=True,
            reason=("видеокарта разрешена: будет использована при первом кодировании"
                    if use_gpu else "видеокарта выключена в настройках"),
        )

    def embed_one(self, text: str) -> list[float]:
        vecs = self.embed([text])
        if not vecs:
            raise EmbeddingUnavailable("пустой результат кодирования")
        return vecs[0]


def truncate_for_embedding(text: str, max_chars: int = 4000) -> str:
    """Ограничение длины входа: у модели есть предел контекста, крупные символы режутся.

    Обрезка с конца сохраняет объявление и начало тела — самое информативное.
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def batch_iter(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
