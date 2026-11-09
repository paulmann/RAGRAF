"""Извлечение фактов о документах: промпт, разбор ответа, запись слоя.

Зачем отдельный модуль, а не продолжение `docgraph.py`
    В `docgraph.py` живёт СЛОЙ: схема, идентификаторы, проверка целостности,
    отпечаток, состояние. Здесь — ПРОХОД: он спрашивает модель, разбирает ответ
    и пишет факты через те же `add_entity`/`add_relation`. Разделение не
    косметическое: слой обязан оставаться работоспособным без сети, а проход
    без настроенного LLM не начинается вовсе.

Три правила, без которых слой становится вторым корпусом
    1. **Цитата берётся из текста, а не из ответа модели.** Модель называет
       фразу; проход ищет её В ЧАНКЕ и сам считает смещения. Не нашлась — факт
       отвергается кодом `quote_not_in_chunk`, а не записывается «почти верно».
    2. **Смещения считаются по полному тексту чанка.** В модель уходит предел
       `docgraph_max_chars` знаков, но адрес считается в исходном тексте:
       иначе у длинного чанка адрес указывал бы в обрезанную копию.
    3. **Сначала весь ответ, потом одна запись.** Факты копятся в памяти и
       пишутся ОДНОЙ транзакцией. Отказ провайдера на середине оставляет слой
       прежним, а ответ называет, сколько фактов было бы записано. Половина
       слоя, записанная до отказа, — это худший исход: она выглядит как факт.

Почему проход идёт в активную ревизию
    Ровно как у живого слоя баз (`cli.cmd_db`): слой — часть того же индекса,
    а не отдельное хранилище, и второй ревизии ради фактов никто не заводит.
    Инвариант «ревизия иммутабельна» касается ГРАФА кода; дополнительные слои
    (базы, документный граф) пишутся в активную ревизию и пересобираются
    повторным проходом, а не правкой задним числом.

Повторный прогон
    Кэш ответов адресуется содержимым чанка, поэтому второй прогон по
    неизменному корпусу не делает ни одного обращения к провайдеру, а запись
    идёт теми же идентификаторами (`INSERT OR REPLACE`) — слой остаётся
    побайтово тем же. Проверяется свойством, а не обещанием.

Границы
    Модуль не решает, ЧТО считать сущностью: это делает промпт. Он отвечает за
    дисциплину формата, адреса, детерминизм и за то, чтобы отказ был назван.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from . import docgraph as dg
from . import llm as llm_mod
from . import settings as settings_mod
from . import store, util
from .docnode import split_locator

#: Инструкция модели. Версия живёт в настройке `llm_prompt_version` и входит в
#: отпечаток слоя и в ключ кэша: правка текста инструкции — новый вход, поэтому
#: старые ответы не переиспользуются.
INSTRUCTION = """Ты извлекаешь факты из фрагмента документа.

Отвечай ТОЛЬКО JSON-объектом такой формы:
{"entities":[{"name":"...","kind":"...","quote":"..."}],
 "relations":[{"from":"...","to":"...","kind":"...","quote":"..."}]}

Правила:
1. name, from и to — имена, которые ЕСТЬ в тексте фрагмента. Не придумывай
   сущности, которых в тексте нет, и не переводи имена.
2. quote — дословная подстрока фрагмента, по которой видно упоминание. Не
   пересказывай и не сокращай слова.
3. kind — одно слово без пробелов: что это за сущность или связь.
4. Не больше {max_entities} сущностей из одного фрагмента.
5. Если фактов нет — верни {{"entities":[],"relations":[]}}.
6. Никаких пояснений, никакого markdown, никаких ``` вокруг JSON."""

#: Коды отказа разбора. Машинные: по ним считается статистика отказов.
REJECT_BAD_JSON = "bad_json"
REJECT_EMPTY = "empty"
REJECT_QUOTE_NOT_IN_CHUNK = "quote_not_in_chunk"
REJECT_QUOTE_TOO_SHORT = "quote_too_short"
REJECT_BAD_NAME = "bad_name"
REJECT_UNKNOWN_ENTITY = "unknown_entity"

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_WS = re.compile(r"\s+")


class ExtractionError(Exception):
    """Отказ прохода. Код машинный, сообщение — для человека."""

    def __init__(self, code: str, message: str = "", detail: dict | None = None):
        super().__init__(message or code)
        self.code = str(code or "error")
        self.message = str(message or "")
        self.detail = dict(detail or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": dict(self.detail)}


# ---------------------------------------------------------------------------
# Текст и адрес
# ---------------------------------------------------------------------------


def build_instruction(max_entities: int) -> str:
    """Инструкция с пределом сущностей. Предел — часть входа, а не подсказка."""
    return INSTRUCTION.replace("{max_entities}", str(int(max_entities)))


def parse_answer(text: str) -> dict[str, Any]:
    """Разобрать ответ модели в словарь. Строгий JSON, терпимость только к ```."""
    raw = str(text or "").strip()
    if not raw:
        raise ExtractionError(REJECT_BAD_JSON, "модель вернула пустой ответ")
    candidate = _FENCE.sub("", raw).strip()
    try:
        payload = json.loads(candidate)
    except ValueError:
        # Вторая попытка — по границам объекта: некоторые модели добавляют
        # строку до или после JSON. Это терпимость к обрамлению, а не к смыслу:
        # разбирается ровно один объект, и он обязан быть корректным.
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise ExtractionError(REJECT_BAD_JSON, "в ответе нет JSON-объекта",
                                  {"head": candidate[:200]}) from None
        try:
            payload = json.loads(candidate[start:end + 1])
        except ValueError as exc:
            raise ExtractionError(REJECT_BAD_JSON, "ответ не разбирается как JSON",
                                  {"head": candidate[:200]}) from exc
    if not isinstance(payload, dict):
        raise ExtractionError(REJECT_BAD_JSON, "ответ не объект JSON")
    return payload


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """Смещения цитаты в ПОЛНОМ тексте чанка. `None` — цитаты в тексте нет.

    Сначала точное совпадение. Если его нет, сравнивается текст с схлопнутыми
    пробелами (модель часто меняет перевод строки на пробел), и найденное место
    переводится обратно в смещения ИСХОДНОГО текста: адрес обязан указывать в
    чанк, а не в его нормализованную копию.
    """
    body = str(text or "")
    needle = str(quote or "").strip()
    if not body or not needle:
        return None
    pos = body.find(needle)
    if pos >= 0:
        return pos, pos + len(needle)
    # Схлопнутые пробелы: строим карту «нормализованный символ → индекс в body».
    collapsed: list[str] = []
    index_of: list[int] = []
    previous_space = False
    for index, char in enumerate(body):
        if char.isspace():
            if previous_space:
                continue
            collapsed.append(" ")
            index_of.append(index)
            previous_space = True
            continue
        collapsed.append(char)
        index_of.append(index)
        previous_space = False
    flat = "".join(collapsed)
    flat_needle = _WS.sub(" ", needle)
    pos = flat.find(flat_needle)
    if pos < 0:
        return None
    start = index_of[pos]
    end = index_of[pos + len(flat_needle) - 1] + 1
    return start, end


def line_range(locator: str, text: str) -> tuple[int, int]:
    """Диапазон строк чанка по локатору. Неразобранный локатор — 1..строк."""
    form, _path, fields = split_locator(locator)
    if form == "line":
        try:
            start = int(str(fields.get("start") or 1))
            end = int(str(fields.get("end") or start))
            return max(1, start), max(start, end)
        except (TypeError, ValueError):
            pass
    lines = max(1, str(text or "").count("\n") + 1)
    return 1, lines


def make_mention(chunk: "ChunkRow", quote: str) -> dg.Mention | None:
    """Упоминание с адресом. `None` — цитаты в чанке нет."""
    span = locate_quote(chunk.text, quote)
    if span is None:
        return None
    start_line, end_line = line_range(chunk.locator, chunk.text)
    return dg.Mention(
        chunk_id=chunk.chunk_id, path=chunk.path,
        start_line=start_line, end_line=end_line,
        char_start=span[0], char_end=span[1],
        quote=str(quote).strip(),
    )


@dataclass
class ChunkRow:
    """Документный чанк в том виде, в котором он нужен проходу."""

    chunk_id: str
    path: str
    locator: str
    kind: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"chunk_id": self.chunk_id, "path": self.path,
                "locator": self.locator, "kind": self.kind,
                "chars": len(self.text)}


# ---------------------------------------------------------------------------
# Проход
# ---------------------------------------------------------------------------


class Extractor:
    """Один прогон извлечения по активной ревизии.

    `client` подменяется проверкой: она обязана работать без сети, и только так
    можно утверждать «второго обращения не было» счётчиком, а не временем.
    """

    def __init__(self, cfg, *, client: llm_mod.LLMClient | None = None,
                 values: dict[str, Any] | None = None,
                 limit: int | None = None) -> None:
        self.cfg = cfg
        self.values = dict(values if values is not None else self._load_values())
        self.limit = limit
        self.client = client or llm_mod.LLMClient(
            cfg, config=llm_mod.config_from_settings(self.values))
        self.max_chars = int(self.values.get("docgraph_max_chars") or 6000)
        self.max_entities = int(self.values.get("docgraph_max_entities") or 30)
        self.min_quote = int(self.values.get("docgraph_min_quote_chars") or 8)
        self.instruction = build_instruction(self.max_entities)

    @staticmethod
    def _load_values() -> dict[str, Any]:
        try:
            return dict(settings_mod.SettingsStore().load())
        except Exception:  # noqa: BLE001 — файла нет либо он не разбирается
            return dict(settings_mod.DEFAULTS)

    # -- чтение ---------------------------------------------------------

    def chunks(self, rev_id: str) -> list[ChunkRow]:
        """Чанки ревизии в устойчивом порядке: по идентификатору."""
        path = getattr(self.cfg, "lexvec_db", None)
        if not path or not str(path):
            return []
        rows: list[ChunkRow] = []
        try:
            conn = store.connect(path, readonly=True)
        except (OSError, sqlite3.Error):
            return []
        try:
            if not store.table_exists(conn, "document_chunk"):
                return []
            for row in conn.execute(
                "SELECT chunk_id, path, locator, kind, text FROM document_chunk "
                "WHERE rev_id = ? ORDER BY chunk_id", (rev_id,)
            ).fetchall():
                rows.append(ChunkRow(
                    chunk_id=str(row["chunk_id"]), path=str(row["path"]),
                    locator=str(row["locator"] or ""), kind=str(row["kind"] or ""),
                    text=str(row["text"] or ""),
                ))
        except sqlite3.Error:
            return []
        finally:
            conn.close()
        if self.limit is not None and self.limit >= 0:
            rows = rows[: int(self.limit)]
        return rows

    def explain(self, rev_id: str) -> dict[str, Any]:
        """Сколько работы и почему её может не быть. Без сети."""
        rows = self.chunks(rev_id)
        state = self.client.status()
        return {
            "index_rev": rev_id,
            "chunks": len(rows),
            "chars": sum(len(row.text) for row in rows),
            "llm": state,
            "prompt_version": str(self.values.get("llm_prompt_version") or ""),
            "instruction_chars": len(self.instruction),
        }

    # -- разбор одного ответа -------------------------------------------

    def _facts_of(self, chunk: ChunkRow, answer: dict[str, Any],
                  rejected: dict[str, int]) -> tuple[dict, list[dict]]:
        """Сущности и отношения чанка после проверки адресов.

        Ничего не пишет и не меняет слой: только говорит, что можно записать.
        """
        entities: dict[tuple[str, str], dict[str, Any]] = {}
        raw_entities = answer.get("entities")
        if not isinstance(raw_entities, list):
            rejected[REJECT_BAD_JSON] = rejected.get(REJECT_BAD_JSON, 0) + 1
            raw_entities = []
        for item in raw_entities[: self.max_entities]:
            if not isinstance(item, dict):
                rejected[REJECT_BAD_NAME] = rejected.get(REJECT_BAD_NAME, 0) + 1
                continue
            name = " ".join(str(item.get("name") or "").split())
            kind = str(item.get("kind") or "").strip().casefold().replace(" ", "_")
            quote = str(item.get("quote") or "").strip()
            if not name or not kind or len(name) > 200:
                rejected[REJECT_BAD_NAME] = rejected.get(REJECT_BAD_NAME, 0) + 1
                continue
            if len(quote) < self.min_quote:
                rejected[REJECT_QUOTE_TOO_SHORT] = rejected.get(
                    REJECT_QUOTE_TOO_SHORT, 0) + 1
                continue
            mention = make_mention(chunk, quote)
            if mention is None:
                rejected[REJECT_QUOTE_NOT_IN_CHUNK] = rejected.get(
                    REJECT_QUOTE_NOT_IN_CHUNK, 0) + 1
                continue
            key = (dg.norm_name(name), kind)
            slot = entities.setdefault(key, {"name": name, "kind": kind,
                                             "mentions": []})
            slot["mentions"].append(mention)

        relations: list[dict[str, Any]] = []
        raw_relations = answer.get("relations")
        if not isinstance(raw_relations, list):
            raw_relations = []
        for item in raw_relations[: self.max_entities]:
            if not isinstance(item, dict):
                continue
            src = " ".join(str(item.get("from") or "").split())
            dst = " ".join(str(item.get("to") or "").split())
            kind = str(item.get("kind") or "").strip().casefold().replace(" ", "_")
            quote = str(item.get("quote") or "").strip()
            if not src or not dst or not kind or len(quote) < self.min_quote:
                rejected[REJECT_BAD_NAME] = rejected.get(REJECT_BAD_NAME, 0) + 1
                continue
            src_key = (dg.norm_name(src), )
            dst_key = (dg.norm_name(dst), )
            known = {key[0] for key in entities}
            # Связь опирается на сущность, у которой есть упоминание В ЭТОМ
            # чанке: иначе конец связи некуда адресовать, и это была бы связь
            # по имени, а не факт.
            if src_key[0] not in known or dst_key[0] not in known:
                rejected[REJECT_UNKNOWN_ENTITY] = rejected.get(
                    REJECT_UNKNOWN_ENTITY, 0) + 1
                continue
            mention = make_mention(chunk, quote)
            if mention is None:
                rejected[REJECT_QUOTE_NOT_IN_CHUNK] = rejected.get(
                    REJECT_QUOTE_NOT_IN_CHUNK, 0) + 1
                continue
            relations.append({"src_name": src, "dst_name": dst, "kind": kind,
                              "mention": mention})
        return entities, relations

    # -- запись ---------------------------------------------------------

    def _write(self, rev_id: str, entities: dict, relations: list[dict]) -> dict[str, int]:
        """Записать слой одной транзакцией. Возвращает числа записанного."""
        conn = store.connect(self.cfg.lexvec_db)
        try:
            dg.ensure_schema(conn)
            # Пересбор: прежние факты ревизии убираются ПЕРЕД записью, чтобы
            # повторный прогон не добавлял второй набор к первому.
            for table in ("entity", "entity_mention", "entity_relation",
                          "theme", "theme_member", "theme_summary"):
                conn.execute(f"DELETE FROM {table} WHERE rev_id = ?", (rev_id,))
            id_by_name: dict[str, str] = {}
            mentions_written = 0
            for key, slot in sorted(entities.items()):
                eid = dg.add_entity(conn, rev_id, name=slot["name"],
                                    kind=slot["kind"], mentions=slot["mentions"],
                                    commit=False, with_schema=False)
                mentions_written += len(slot["mentions"])
                id_by_name.setdefault(str(key[0]), eid)
            relations_written = 0
            for rel in relations.values():
                src = id_by_name.get(dg.norm_name(rel["src_name"]))
                dst = id_by_name.get(dg.norm_name(rel["dst_name"]))
                if not src or not dst:
                    continue
                dg.add_relation(conn, rev_id, src_entity_id=src, dst_entity_id=dst,
                                kind=rel["kind"], mention=rel["mention"],
                                commit=False, with_schema=False)
                relations_written += 1
            conn.commit()
            # Derived-факты и их входы — сразу после записи графа: иначе
            # `dependents` не знал бы, из какого чанка выведена связь.
            from . import provenance as provenance_mod

            derived = provenance_mod.build_derived(conn, rev_id)
            # Каскад устаревания (X7) — там же, где появились рёбра: у
            # пометки и у вывода один и тот же вход.
            cascade = provenance_mod.auto_cascade(conn, rev_id)
            return {"entities": len(entities), "mentions": mentions_written,
                    "relations": relations_written, "derived_facts": derived["derived"],
                    "derivation_edges": derived["edges"],
                    "stale_marked": cascade.get("marked", 0)}
        except Exception:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    # -- прогон ---------------------------------------------------------

    def run(self, rev_id: str, *, force: bool = False) -> dict[str, Any]:
        """Выполнить проход. Отказ провайдера — названный код, а не пустой слой."""
        started = time.perf_counter()
        current = dg.current_fingerprint(self.values,
                                         llm_config=self.client.config)
        state = self.client.status()
        rejected: dict[str, int] = {}
        result: dict[str, Any] = {
            "op": "docgraph_build",
            "index_rev": rev_id,
            "capability": "schema",
            "collected": False,
            "skipped": False,
            "code": "",
            "reason": "",
            "llm": state,
            "fingerprint": {"current": current["fingerprint"], "recorded": "",
                            "matches": False, "parts": current},
            "stats": {"chunks": 0, "chunks_sent": 0, "calls": 0, "cache_hits": 0,
                      "truncated": 0, "entities": 0, "mentions": 0, "relations": 0,
                      "would_write": {"entities": 0, "mentions": 0, "relations": 0},
                      "rejected": rejected},
            "notes": [],
            "seconds": 0.0,
        }

        if not rev_id:
            result.update(code="no_revision",
                          reason="в корпусе нет активной ревизии: сначала `ragraf index`")
            return result

        recorded = self._recorded(rev_id)
        result["fingerprint"]["recorded"] = recorded
        result["fingerprint"]["matches"] = bool(recorded) and recorded == current["fingerprint"]
        built = self._layer_size(rev_id)
        if not force and result["fingerprint"]["matches"] and built["entities"] > 0:
            result.update(
                collected=True, skipped=True,
                reason="отпечаток совпадает с записанным, слой не пересобирался",
                stats=dict(result["stats"], entities=built["entities"],
                           mentions=built["mentions"], relations=built["relations"]),
            )
            # Темы считаются без модели и стоят копейки, поэтому досчитываются и
            # при пропуске: слой мог быть собран до появления тем, и тогда
            # «тем нет» означало бы «не построены», а не «не бывает».
            theme_stats = self._build_themes(rev_id, only_if_empty=True)
            result["stats"]["themes"] = int(theme_stats.get("stored") or 0)
            if theme_stats.get("reason"):
                result["notes"].append("темы: " + str(theme_stats["reason"]))
            result["notes"].append("проход пропущен: вход не изменился, обращений к "
                                   "провайдеру не было (0 вызовов)")
            result["seconds"] = round(time.perf_counter() - started, 3)
            return result

        rows = self.chunks(rev_id)
        result["stats"]["chunks"] = len(rows)
        if not rows:
            result.update(code="no_chunks",
                          reason="документных чанков в ревизии нет: извлекать не из чего")
            result["seconds"] = round(time.perf_counter() - started, 3)
            return result

        entities: dict[tuple[str, str], dict[str, Any]] = {}
        relations: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            text = row.text
            if len(text) > self.max_chars:
                text = text[: self.max_chars]
                result["stats"]["truncated"] += 1
            result["stats"]["chunks_sent"] += 1
            try:
                answer = self.client.complete(self.instruction, input_text=text,
                                              purpose=f"docgraph:{row.chunk_id}")
            except llm_mod.LLMError as exc:
                # Слой остаётся прежним: запись идёт одной транзакцией ПОСЛЕ
                # цикла, поэтому здесь писать нечего.
                result.update(
                    collected=False, code=exc.code,
                    reason=(f"извлечение остановлено на чанке "
                            f"{result['stats']['chunks_sent']} из {len(rows)}: "
                            f"{exc.message or exc.code}"),
                )
                result["stats"]["would_write"] = {
                    "entities": len(entities),
                    "mentions": sum(len(v["mentions"]) for v in entities.values()),
                    "relations": len(relations),
                }
                result["seconds"] = round(time.perf_counter() - started, 3)
                return result
            if answer.get("cached"):
                result["stats"]["cache_hits"] += 1
            result["stats"]["calls"] = int(answer.get("calls") or 0)
            try:
                payload = parse_answer(str(answer.get("text") or ""))
            except ExtractionError as exc:
                rejected[exc.code] = rejected.get(exc.code, 0) + 1
                continue
            chunk_entities, chunk_relations = self._facts_of(row, payload, rejected)
            for key, slot in chunk_entities.items():
                slot_existing = entities.setdefault(key, {"name": slot["name"],
                                                          "kind": slot["kind"],
                                                          "mentions": []})
                slot_existing["mentions"].extend(slot["mentions"])
            for rel in chunk_relations:
                key = (dg.norm_name(rel["src_name"]), dg.norm_name(rel["dst_name"]),
                       rel["kind"])
                if key in relations:
                    relations[key]["weight"] += 1.0
                    continue
                relations[key] = dict(rel, weight=1.0)

        result["stats"]["would_write"] = {
            "entities": len(entities),
            "mentions": sum(len(v["mentions"]) for v in entities.values()),
            "relations": len(relations),
        }
        if not entities and not relations:
            result.update(
                collected=True, code="",
                reason="фактов не найдено: модель не назвала ни одной сущности, "
                       "процитированной из чанка",
            )
            result["notes"].append("отказов разбора: " + (
                ", ".join(f"{k}={v}" for k, v in sorted(rejected.items())) or "нет"))
            result["seconds"] = round(time.perf_counter() - started, 3)
            return result

        try:
            written = self._write(rev_id, entities, relations)
        except Exception as exc:  # noqa: BLE001 — отказ записи обязан быть текстом
            result.update(code="write_failed",
                          reason=f"запись слоя не удалась: {type(exc).__name__}: {exc}")
            result["seconds"] = round(time.perf_counter() - started, 3)
            return result

        result["stats"].update(written)
        theme_stats = self._build_themes(rev_id)
        result["stats"]["themes"] = int(theme_stats.get("stored") or 0)
        if theme_stats.get("reason"):
            result["notes"].append("темы: " + str(theme_stats["reason"]))
        elif result["stats"]["themes"]:
            result["notes"].append(
                f"тем построено: {result['stats']['themes']} "
                f"(порог веса {theme_stats.get('min_weight')}, предел размера "
                f"{theme_stats.get('max_size')})")
        recorded_info = dg.record_fingerprint(
            self.cfg, rev_id, values=self.values,
            llm_config=self.client.config,
        )
        result["fingerprint"].update({
            "recorded": recorded_info.get("fingerprint", ""),
            "matches": True,
            "changed": recorded_info.get("changed"),
            "drift_entry": recorded_info.get("drift_entry"),
            "rev_json": recorded_info.get("rev_json"),
        })
        result.update(collected=True, code="", reason="", capability="built")
        if rejected:
            result["notes"].append("отказов разбора: " + ", ".join(
                f"{k}={v}" for k, v in sorted(rejected.items())))
        if result["stats"]["truncated"]:
            result["notes"].append(
                f"чанков, обрезанных до {self.max_chars} знаков: "
                f"{result['stats']['truncated']}")
        result["seconds"] = round(time.perf_counter() - started, 3)
        return result

    # -- служебное ------------------------------------------------------

    def _recorded(self, rev_id: str) -> str:
        from .revisions import ControlStore

        try:
            control = ControlStore(self.cfg)
        except Exception:  # noqa: BLE001
            return ""
        try:
            return dg.stored_fingerprint(control.conn, rev_id)
        finally:
            control.close()

    def _build_themes(self, rev_id: str, *, only_if_empty: bool = False) -> dict[str, Any]:
        """Построить темы слоя: детерминированно, без обращений к провайдеру."""
        from . import themes as themes_mod

        try:
            conn = store.connect(self.cfg.lexvec_db)
        except (OSError, sqlite3.Error) as exc:
            return {"stored": 0, "reason": f"соединение со слоем не открылось: {exc}"}
        try:
            if only_if_empty:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM theme WHERE rev_id = ?", (rev_id,)
                ).fetchone()
                if row is not None and int(row["c"] if hasattr(row, "keys") else row[0]):
                    return {"stored": 0, "reason": ""}
            return themes_mod.build(conn, rev_id)
        except Exception as exc:  # noqa: BLE001 — сборка тем не имеет права ронять проход
            return {"stored": 0, "reason": f"{type(exc).__name__}: {exc}"}
        finally:
            conn.close()

    def _layer_size(self, rev_id: str) -> dict[str, int]:
        out = {"entities": 0, "mentions": 0, "relations": 0}
        try:
            conn = store.connect(self.cfg.lexvec_db, readonly=True)
        except (OSError, sqlite3.Error):
            return out
        try:
            for key, table in (("entities", "entity"), ("mentions", "entity_mention"),
                               ("relations", "entity_relation")):
                if not store.table_exists(conn, table):
                    continue
                row = conn.execute(
                    f"SELECT COUNT(*) AS c FROM {table} WHERE rev_id = ?", (rev_id,)
                ).fetchone()
                out[key] = int(row["c"] if hasattr(row, "keys") else row[0])
        except sqlite3.Error:
            return out
        finally:
            conn.close()
        return out


def run(cfg, rev_id: str, *, limit: int | None = None,
        client: llm_mod.LLMClient | None = None,
        values: dict[str, Any] | None = None,
        force: bool = False) -> dict[str, Any]:
    """Короткая форма для индексатора и CLI: создать `Extractor` и пройти.

    Одна точка входа на два потребителя: `ragraf index --doc-graph` и
    `ragraf docgraph build`. Два пути с разной логикой разошлись бы так же, как
    когда-то разошлись ключи `callee_name`/`calleeName`.
    """
    return Extractor(cfg, client=client, values=values, limit=limit).run(
        rev_id, force=force)


def describe(result: dict[str, Any]) -> str:
    """Строка для отчёта прогона: числа и причина, без обещаний."""
    """Строка для отчёта прогона: числа и причина, без обещаний."""
    stats = result.get("stats") or {}
    if result.get("skipped"):
        return ("документный граф: проход пропущен (вход не изменился), "
                f"в слое сущностей {stats.get('entities', 0)}, "
                f"упоминаний {stats.get('mentions', 0)}, "
                f"отношений {stats.get('relations', 0)}")
    if not result.get("collected"):
        return f"документный граф не собран: {result.get('reason') or result.get('code')}"
    return (f"документный граф: чанков {stats.get('chunks', 0)}, "
            f"вызовов {stats.get('calls', 0)}, из кэша {stats.get('cache_hits', 0)}, "
            f"сущностей {stats.get('entities', 0)}, "
            f"упоминаний {stats.get('mentions', 0)}, "
            f"отношений {stats.get('relations', 0)}, "
            f"отказов разбора {sum((stats.get('rejected') or {}).values())}")
