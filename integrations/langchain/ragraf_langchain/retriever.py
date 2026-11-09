# -*- coding: utf-8 -*-
"""`RAGRAFRetriever` — обязательный поиск: вопрос → документы с провенансом.

Почему retriever, а не только инструмент. В LangChain это разные уровни: retriever
возвращает `Document[]` и вызывается ПАЙПЛАЙНОМ (2-step RAG: сначала поиск, потом
генерация), а `Tool` вызывает модель по своему решению. Если требование звучит как
«каждый ответ опирается на индекс», поиск обязан быть шагом workflow, а не
надеждой на то, что модель вспомнит про инструмент.

Что возвращается вместо «просто текста»: каждый `Document` несёт `path:line` —
доказательство, по которому факт можно проверить. Без этого ответ выглядит
достоверным только потому, что пришёл «из базы».

Смысл `score` назван честно: это взвешенное RRF-слияние каналов лексики (вес 1.0)
и вектора (вес 0.6) — `src/ragraf/lexvec.py:562-598`. Значение сравнимо ТОЛЬКО
внутри одного ответа: у него нет ни вероятностного смысла, ни общей шкалы между
запросами.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForRetrieverRun,
    CallbackManagerForRetrieverRun,
)
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict, Field, PrivateAttr

from ._bridge import get_facade

__all__ = ["RAGRAFRetriever", "hit_to_document"]

#: Поля конверта `op_search`, которые попадают в метаданные каждого документа.
ENVELOPE_KEYS = ("index_rev", "capability", "edition", "provenance", "drift")


def _stale_flag(facade: Any) -> bool | None:
    """Устарел ли индекс — без запуска переиндексации и без исключения.

    Признак берётся из результата `op_freshness`: `changed_since_index`
    (`src/ragraf/facade.py:1829`, результат — словарь с `corpus_hash_now` и
    `corpus_hash_indexed`). В конверте поля `stale` нет, поэтому читать его оттуда
    было бы тихой ошибкой: признак всегда был бы `None`.
    `None` в возврате — «спросить не удалось», и это отличается от `False` («свеж»).
    """
    try:
        fresh = facade.op_freshness(auto_reindex=False)
    except Exception:  # noqa: BLE001 — признак свежести не имеет права ломать поиск
        return None
    result = fresh.get("result")
    if not isinstance(result, dict) or "changed_since_index" not in result:
        return None
    return bool(result.get("changed_since_index"))


def hit_to_document(hit: dict[str, Any], envelope: dict[str, Any] | None = None,
                    *, stale: bool | None = None) -> Document:
    """Хит `op_search` → `Document` с адресом `path:line`.

    `page_content` — начало текста чанка из индекса (`snippet`, когда он запрошен);
    если текста в индексе нет (прежняя ревизия, чужой корпус), содержимым становится
    сам адрес: пустая строка выглядела бы как «документ без текста», а не как
    «текст не запрошен».
    """
    envelope = envelope or {}
    path = str(hit.get("path") or "")
    start = hit.get("start_line")
    end = hit.get("end_line")
    ref = f"{path}:{start}" if start is not None else path
    metadata: dict[str, Any] = {
        "path": path,
        "start_line": start,
        "end_line": end,
        "ref": ref,
        "chunk_id": hit.get("chunk_id"),
        "node_id": hit.get("node_id"),
        "source_kind": hit.get("source_kind"),
        "score": hit.get("score"),
        "why": hit.get("why"),
        "material": hit.get("material"),
        "doc_role": hit.get("doc_role"),
        "doc_role_title": hit.get("doc_role_title"),
        "origin": "ragraf",
    }
    for key in ENVELOPE_KEYS:
        if key in envelope:
            metadata[key] = envelope[key]
    if stale is not None:
        metadata["stale"] = stale
    snippet = hit.get("snippet")
    content = str(snippet).strip() if snippet else ref
    return Document(page_content=content, metadata=metadata, id=hit.get("chunk_id"))


class RAGRAFRetriever(BaseRetriever):
    """Гибридный поиск RAGRAF как `BaseRetriever` LangChain.

    Один экземпляр держит один фасад: конструктор фасада открывает три хранилища
    SQLite, поэтому он кэшируется на конфигурацию процесса (`_bridge.get_facade`).
    Экземпляр не потокобезопасен по соединениям SQLite — это названо в границах.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    config_path: str | None = Field(
        default=None,
        description="Путь к конфигурации корпуса; None — искать как CLI (RAGRAF_CONFIG, ./config/ragraf.yaml)",
    )
    limit: int = Field(default=8, ge=1, le=100, description="Сколько чанков вернуть")
    kinds: list[str] | None = Field(
        default=None,
        description="Фильтр по виду чанка (code, doc, file, …); None — все",
    )
    mode: str = Field(default="auto", description="Режим поиска RAGRAF: auto/code/docs/themes")
    with_text: bool = Field(default=True, description="Запрашивать начало текста чанка из индекса")
    text_width: int = Field(default=800, ge=80, le=8000, description="Сколько символов текста чанка")
    watch: bool = Field(
        default=False,
        description="Разрешить сторожу RAGRAF переиндексировать корпус при расхождении суммы",
    )
    telemetry: bool = Field(
        default=True,
        description="Писать обращение в журнал RAGRAF (статистика «кто пользовался индексом»)",
    )
    agent: str = Field(default="langchain", description="Имя агента для статистики RAGRAF")

    _stale_cache: bool | None = PrivateAttr(default=None)
    _rev_cache: str | None = PrivateAttr(default=None)

    def _get_facade(self) -> Any:
        # Фасад НЕ хранится в экземпляре: он живёт в кэше моста с ключом по потоку.
        # Иначе `ainvoke` (работа уходит в поток) получил бы соединение SQLite,
        # созданное в другом потоке, и упал бы на проверке sqlite3 — замерено
        # 03.11.2026 на собственном тесте `test_async_invoke_matches_sync`.
        return get_facade(self.config_path, watch=self.watch,
                          telemetry=self.telemetry, agent=self.agent)

    def _search(self, query: str) -> list[Document]:
        facade = self._get_facade()
        envelope = facade.op_search(
            query,
            limit=self.limit,
            kinds=list(self.kinds) if self.kinds else None,
            with_text=self.with_text,
            text_width=self.text_width,
            mode=self.mode,
        )
        if self._stale_cache is None:
            self._stale_cache = _stale_flag(facade)
        hits = envelope.get("result") or []
        return [hit_to_document(hit, envelope, stale=self._stale_cache) for hit in hits]

    def _get_relevant_documents(self, query: str, *,
                                run_manager: CallbackManagerForRetrieverRun,
                                ) -> list[Document]:
        # Пустой ответ — это пустой список, а не исключение: по контракту RAGRAF
        # «ничего не найдено» — код 0 с found=false (`src/ragraf/cli.py:87`).
        return self._search(query)

    async def _aget_relevant_documents(self, query: str, *,
                                       run_manager: AsyncCallbackManagerForRetrieverRun,
                                       ) -> list[Document]:
        # SQLite в RAGRAF синхронный: уводим работу в поток, чтобы не блокировать
        # event loop LangChain на время запроса к индексу.
        return await asyncio.to_thread(self._search, query)

    # --- удобства, не входящие в контракт BaseRetriever ---------------------
    @property
    def index_rev(self) -> str | None:
        """Ревизия индекса, по которой отвечает этот retriever."""
        return self._get_facade().current_rev()

    def freshness(self) -> dict[str, Any]:
        """Свежесть индекса без переиндексации (`op_freshness(auto_reindex=False)`)."""
        return self._get_facade().op_freshness(auto_reindex=False)

    def status(self) -> dict[str, Any]:
        """Состояние ревизии: покрытие, деградация, размер журнала."""
        return self._get_facade().op_index_status()
