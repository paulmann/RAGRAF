"""Лексико-векторное хранилище: FTS5 (BM25) + vec0 + переиспользование эмбеддингов.

Ключевые свойства:
  * chunk_id выводится из текста, а не из ревизии → при переиндексации неизменённые
    чанки не требуют повторного кодирования (главная экономия НФТ-3);
  * FTS5 и vec0 живут в одном файле (архитектура §4.2);
  * отсутствие vec0 — не ошибка: лексика работает, векторный слой помечается.
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass
from typing import Iterable

from . import store, util
from .config import ProjectConfig
from .extract.php_ast import ChunkRec

# Токенизация: идентификаторы кода и SQL разбиваются, чтобы «OutgoingLetters»
# находился и по «Outgoing», и по «Letters» (лексика обязательна для точных имён).
_WORD = re.compile(r"[\w\u0400-\u04FF]+", re.UNICODE)
# Разбор camelCase: последовательности ПРОПИСНЫХ перед строчной, затем обычные слова.
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def tokenize_for_fts(text: str) -> str:
    """Текст в поток токенов для FTS5.

    Идентификаторы делятся по camelCase/snake_case, чтобы «OutgoingLetters» находился и
    по «outgoing», и по «letters», и целиком. ВАЖНО: применяется findall, а не split:
    сплит по словам давал токены из отдельных букв («p ipeline e ngine») — ошибка
    выявлена диагностикой поиска по русскому запросу.
    """
    out: list[str] = []
    for word in _WORD.findall(text):
        low = word.lower()
        parts = _CAMEL.findall(word)
        if len(parts) > 1:
            out.extend(p.lower() for p in parts)
        out.append(low)
    return " ".join(out)


def extract_sql_tokens(text: str) -> str:
    """Из SQL-строк — имена таблиц/колонок, чтобы искать по ним."""
    parts = re.findall(r"[`\"']?([A-Za-z_][A-Za-z0-9_]{2,})[`\"']?", text)
    return " ".join(sorted({p.lower() for p in parts}))


@dataclass
class SearchHit:
    chunk_id: str
    path: str
    node_id: str | None
    start_line: int | None
    end_line: int | None
    source_kind: str
    score: float
    why: str
    snippet: str | None = None
    # Роль документа: заполняется для чанков из документации (ТЗ, правила
    # агента, описание, отчёт). Для чанков кода остаётся пустой строкой.
    doc_role: str = ""


class LexVecStore:
    """Запись чанков, лексический и векторный поиск, слияние результатов."""

    def __init__(self, cfg: ProjectConfig) -> None:
        self.cfg = cfg
        self.conn = store.connect(cfg.lexvec_db)
        # Загрузка расширения ОБЯЗАТЕЛЬНА ДО проверки vec0: без неё pragma_module_list
        # не содержит vec0 и векторный слой молча отключался (выявлено сверкой с
        # selftest, где расширение грузилось явно).
        self.extension_loaded = store.try_load_extension(self.conn, "vec0")
        store.init_lexvec(self.conn)
        self.vec_ok = store.ensure_vec_table(self.conn)
        # Догоняющая миграция: роль документа появилась позже самой таблицы.
        # Без неё на существующих корпусах (существующих корпусах) колонки нет,
        # и запись чанков упала бы с OperationalError. Тот же приём уже
        # применён для props в таблице edge.
        self._migrate_doc_role()
        self.conn.commit()

    def _migrate_doc_role(self) -> None:
        """Добавить колонку роли документа, если её ещё нет."""
        try:
            cols = {
                str(r["name"])
                for r in self.conn.execute("PRAGMA table_info(chunk_map)").fetchall()
            }
        except sqlite3.Error:
            return
        if not cols:
            return
        if "doc_role" not in cols:
            try:
                self.conn.execute("ALTER TABLE chunk_map ADD COLUMN doc_role TEXT")
            except sqlite3.Error:
                # Колонка могла появиться параллельно: это не ошибка.
                pass

    # ---------------- запись ----------------

    def known_embeddings(self, model: str) -> dict[str, tuple[str, str]]:
        """text_hash → (chunk_id, ...) по всем ревизиям: источник переиспользования."""
        rows = self.conn.execute(
            "SELECT chunk_id, text_hash FROM vec_meta WHERE model = ?", (model,)
        ).fetchall()
        return {str(r["text_hash"]): str(r["chunk_id"]) for r in rows}

    def reusable_vectors(self, model: str, text_hashes: Iterable[str]) -> dict[str, list[float]]:
        """Готовые векторы для неизменённого текста (без обращения к модели)."""
        if not self.vec_ok:
            return {}
        wanted = list(dict.fromkeys(text_hashes))
        if not wanted:
            return {}
        found: dict[str, list[float]] = {}
        CH = 400
        for start in range(0, len(wanted), CH):
            part = wanted[start : start + CH]
            marks = ",".join("?" for _ in part)
            rows = self.conn.execute(
                f"""SELECT vm.text_hash AS th, v.embedding AS emb
                    FROM vec_meta vm JOIN vec_chunk v ON v.chunk_id = vm.chunk_id
                    WHERE vm.model = ? AND vm.text_hash IN ({marks})
                    GROUP BY vm.text_hash""",
                (model, *part),
            ).fetchall()
            for r in rows:
                blob = r["emb"]
                found[str(r["th"])] = _blob_to_vec(blob)
        return found

    def add_chunks(self, rev_id: str, chunks: list[ChunkRec], vectors: dict[str, list[float]] | None = None) -> int:
        """Записать чанки в FTS5, chunk_map, vec_meta и (при наличии) vec0."""
        if not chunks:
            return 0
        fts_rows = []
        map_rows = []
        meta_rows = []
        vec_rows = []
        text_rows = []
        # Роль документа считается один раз на файл: чанков из одного файла
        # бывает много, а роль у них одна. Разбор заголовка — тоже на файл,
        # и читается он только если имя файла роль не дало.
        doc_roles: dict[str, str] = {}
        for ch in chunks:
            import math

            tok_n = int(math.ceil(len(ch.text) / 4.2))
            ch.tokens = tok_n
            sql_part = ""
            if "SELECT" in ch.text.upper() or "INSERT" in ch.text.upper() or "UPDATE" in ch.text.upper():
                sql_part = extract_sql_tokens(ch.text)
            fts_rows.append(
                (
                    rev_id,
                    ch.chunk_id,
                    ch.path,
                    tokenize_for_fts(ch.text),
                    tokenize_for_fts(ch.text),
                    sql_part,
                    "",
                )
            )
            # Роль документа — только для текстовых файлов документации.
            # Для чанков-символов кода роли нет и быть не должно: они не
            # документы. Пустое значение тут честнее выдуманного.
            role = ""
            if ch.source_kind in ("file", "doc") and ch.path.lower().endswith(
                (".md", ".markdown", ".rst", ".txt")
            ):
                if ch.path not in doc_roles:
                    from .docrole import classify

                    doc_roles[ch.path] = classify(
                        ch.path, head_text=ch.text[:1200]
                    ).role
                role = doc_roles[ch.path]
            map_rows.append(
                (
                    rev_id,
                    ch.chunk_id,
                    ch.source_kind,
                    ch.path,
                    ch.node_id,
                    ch.start_line,
                    ch.end_line,
                    ch.lang,
                    tok_n,
                    role,
                )
            )
            meta_rows.append(
                (
                    rev_id,
                    ch.chunk_id,
                    self.cfg.embedding.model,
                    None,
                    ch.chunk_id,
                    ch.text_hash,
                )
            )
            # Сырой текст чанка: то, что поиск отдаёт агенту фрагментом.
            # Пишется в отдельную таблицу — FTS-индекс должен остаться
            # неизменным (обоснование — store.LEXVEC_DDL_CHUNK_TEXT).
            text_rows.append((rev_id, ch.chunk_id, ch.path, ch.text))
            if vectors and ch.text_hash in vectors and self.vec_ok:
                vec_rows.append((ch.chunk_id, rev_id, _vec_to_blob(vectors[ch.text_hash])))

        store.executemany_chunked(
            self.conn,
            "INSERT OR REPLACE INTO ft_chunk(rev_id, chunk_id, path, symbol_tokens,"
            " code_tokens, sql_tokens, text_tokens) VALUES(?,?,?,?,?,?,?)",
            fts_rows,
            batch=self.cfg.index.batch_size,
        )
        store.executemany_chunked(
            self.conn,
            "INSERT OR REPLACE INTO chunk_map(rev_id, chunk_id, source_kind, path, node_id,"
            " start_line, end_line, lang, tokens, doc_role)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            map_rows,
            batch=self.cfg.index.batch_size,
        )
        store.executemany_chunked(
            self.conn,
            "INSERT OR REPLACE INTO vec_meta(rev_id, chunk_id, model, model_version,"
            " chunk_sha256, text_hash) VALUES(?,?,?,?,?,?)",
            meta_rows,
            batch=self.cfg.index.batch_size,
        )
        store.executemany_chunked(
            self.conn,
            "INSERT OR REPLACE INTO chunk_text(rev_id, chunk_id, path, text)"
            " VALUES(?,?,?,?)",
            text_rows,
            batch=self.cfg.index.batch_size,
        )
        if vec_rows and self.vec_ok:
            try:
                store.executemany_chunked(
                    self.conn,
                    "INSERT OR REPLACE INTO vec_chunk(chunk_id, rev_id, embedding)"
                    " VALUES(?,?,?)",
                    vec_rows,
                    batch=self.cfg.index.batch_size,
                )
            except sqlite3.Error:
                self.vec_ok = False
        self.conn.commit()
        return len(chunks)

    # ---------------- поиск ----------------

    def search_lexical(self, rev_id: str, query: str, limit: int = 20) -> list[SearchHit]:
        tokens = tokenize_for_fts(query)
        if not tokens:
            return []
        fts_query = " OR ".join(f'"{t}"' for t in tokens.split()[:12])
        # bm25() требует РОВНО один вес на каждую колонку таблицы: их здесь семь
        # (rev_id, chunk_id, path, symbol_tokens, code_tokens, sql_tokens, text_tokens).
        # Неверное число весов → sqlite3.OperationalError, и поиск молча возвращал пусто.
        try:
            rows = self.conn.execute(
                """SELECT f.chunk_id, f.path, m.node_id, m.start_line, m.end_line,
                          m.source_kind, m.doc_role,
                          bm25(ft_chunk, 0.0, 0.0, 4.0, 3.0, 1.0, 1.5, 0.5) AS score
                   FROM ft_chunk f
                   LEFT JOIN chunk_map m ON m.rev_id = f.rev_id AND m.chunk_id = f.chunk_id
                   WHERE f.rev_id = ? AND ft_chunk MATCH ?
                   ORDER BY score LIMIT ?""",
                (rev_id, fts_query, limit),
            ).fetchall()
        except sqlite3.Error:
            return []
        out: list[SearchHit] = []
        for r in rows:
            score = -float(r["score"]) if r["score"] is not None else 0.0
            out.append(
                SearchHit(
                    chunk_id=str(r["chunk_id"]),
                    path=str(r["path"]),
                    node_id=r["node_id"],
                    start_line=r["start_line"],
                    end_line=r["end_line"],
                    source_kind=str(r["source_kind"] or "symbol"),
                    score=score,
                    why="лексическое совпадение (BM25/FTS5)",
                    doc_role=str(r["doc_role"] or ""),
                )
            )
        return out

    def search_vector(self, rev_id: str, vector: list[float], limit: int = 20) -> list[SearchHit]:
        """Векторный поиск с КОРРЕКТНЫМ переводом расстояния в оценку.

        Дефект, найденный замером на полигоне пилотный PHP-портал (tools/probe_vec_metric.py):
        векторы модели НЕ нормированы (норма ≈ 3.5 у чанков, 4.19 у запроса),
        поэтому L2-расстояние от vec0 лежит в диапазоне ~3.9…4.5. Формула
        score = 1 - dist (а также cos ≈ 1 - dist²/2, годная только для
        единичных векторов) давала ровно 0.000 для ВСЕХ результатов. Порядок
        от vec0 при этом был верным — совпадал с ручным косинусом, — но оценка
        теряла смысл, и гибрид RRF поднимал шум выше точной лексики.

        Решение: косинус считается ТОЧНО по фактическим векторам:
            cos = (|q|² + |c|² - dist²) / (2·|q|·|c|)
        Для этого у найденных кандидатов читается их вектор из vec_chunk.
        Объём мал: limit*3 записей по 384 float — единицы сотен килобайт.

        Нормировка при записи — отдельное улучшение (чтобы ANN-индексы в будущем
        работали сразу как косинусные); здесь же корректность обеспечивается
        независимо от того, нормированы векторы или нет.
        """
        if not self.vec_ok or not vector:
            return []
        try:
            rows = self.conn.execute(
                "SELECT chunk_id, distance, embedding FROM vec_chunk"
                " WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (_vec_to_blob(vector), limit * 3),
            ).fetchall()
        except sqlite3.Error:
            return []

        q_norm = math.sqrt(sum(x * x for x in vector))
        out: list[SearchHit] = []
        for r in rows:
            meta = self.conn.execute(
                """SELECT path, node_id, start_line, end_line, source_kind, doc_role
                   FROM chunk_map
                   WHERE rev_id = ? AND chunk_id = ?""",
                (rev_id, r["chunk_id"]),
            ).fetchone()
            if meta is None:
                continue

            chunk_vec = _blob_to_vec(r["embedding"])
            c_norm = math.sqrt(sum(x * x for x in chunk_vec))
            if q_norm > 0.0 and c_norm > 0.0:
                dist = float(r["distance"])
                cos = (q_norm ** 2 + c_norm ** 2 - dist ** 2) / (2.0 * q_norm * c_norm)
            else:
                cos = 0.0
            cos = max(-1.0, min(1.0, cos))

            out.append(
                SearchHit(
                    chunk_id=str(r["chunk_id"]),
                    path=str(meta["path"]),
                    node_id=meta["node_id"],
                    start_line=meta["start_line"],
                    end_line=meta["end_line"],
                    source_kind=str(meta["source_kind"] or "symbol"),
                    score=round((cos + 1.0) / 2.0, 4),
                    why="семантическая близость (эмбеддинг)",
                    doc_role=str(meta["doc_role"] or ""),
                )
            )
            if len(out) >= limit:
                break
        return out

    def search_hybrid(
        self, rev_id: str, query: str, query_vector: list[float] | None, limit: int = 20
    ) -> list[SearchHit]:
        """Взвешенное RRF-слияние лексики и вектора.

        Две поправки по итогам замеров на полигоне (обоснование — не подгонка
        под набор задач, а свойства самих каналов):

        1. ВЕСА КАНАЛОВ. Классический RRF складывает вклады 1/(k+rank), из-за чего
           запись, попавшая в ОБА списка на средних позициях, обходит запись с
           высоким рангом в одном канале. Для поиска по коду это неверно: точные
           термины (имена функций, классы, ключи настроек) надёжнее ловит лексика,
           а вектор даёт смысловую близость и «размазывает» оценку. Поэтому
           лексика весит 1.0, вектор — 0.6.

           Замер это подтверждает: в выдаче на запрос про настройки первым шёл
           файл-пример конфига, попавший в оба списка, а точное совпадение
           (Setting.php, высокий ранг лексики) шло вторым.

        2. ШТРАФ ЗА ЧАНКИ ВИДА file. Чанк «весь файл или окно» размыт по смыслу и
           слабо совпадает со всем подряд; символьные чанки конкретны. Файловые
           чанки получают множитель 0.7.

        Значения взяты из соображений баланса, а не подогнаны до максимального
        попадания: набор задач остаётся контрольным, его нельзя использовать для
        настройки — иначе замер пользы теряет смысл.
        """
        lex = self.search_lexical(rev_id, query, limit=limit * 2)
        vec: list[SearchHit] = []
        if query_vector:
            vec = self.search_vector(rev_id, query_vector, limit=limit * 2)
            # Отбрасываем векторный список, если он не различает результаты:
            # при ошибке метрики (все оценки равны) RRF получал неразличимые ранги.
            if len(vec) > 1:
                scores = [h.score for h in vec]
                if max(scores) - min(scores) < 1e-6:
                    vec = []

        if not vec:
            return lex[:limit]

        lex_weight = 1.0
        vec_weight = 0.6
        file_penalty = 0.7
        k = 60.0

        fused: dict[str, float] = {}
        why: dict[str, list[str]] = {}
        payload: dict[str, SearchHit] = {}

        for rank, hit in enumerate(lex):
            fused[hit.chunk_id] = fused.get(hit.chunk_id, 0.0) + lex_weight / (k + rank + 1)
            why.setdefault(hit.chunk_id, []).append("лексика")
            payload[hit.chunk_id] = hit
        for rank, hit in enumerate(vec):
            fused[hit.chunk_id] = fused.get(hit.chunk_id, 0.0) + vec_weight / (k + rank + 1)
            why.setdefault(hit.chunk_id, []).append("вектор")
            payload.setdefault(hit.chunk_id, hit)

        out: list[SearchHit] = []
        for chunk_id, score in sorted(fused.items(), key=lambda kv: -kv[1]):
            hit = payload[chunk_id]
            if hit.source_kind == "file":
                score *= file_penalty
            hit.score = round(score * 1000, 4)
            hit.why = "совпало по: " + ", ".join(sorted(set(why[chunk_id])))
            out.append(hit)

        # Штраф мог изменить порядок — сортируем окончательно и обрезаем.
        out.sort(key=lambda h: -h.score)
        return out[:limit]

        k = 60.0
        fused: dict[str, float] = {}
        why: dict[str, list[str]] = {}
        payload: dict[str, SearchHit] = {}
        for rank, hit in enumerate(lex):
            fused[hit.chunk_id] = fused.get(hit.chunk_id, 0.0) + 1.0 / (k + rank + 1)
            why.setdefault(hit.chunk_id, []).append("лексика")
            payload[hit.chunk_id] = hit
        for rank, hit in enumerate(vec):
            fused[hit.chunk_id] = fused.get(hit.chunk_id, 0.0) + 1.0 / (k + rank + 1)
            why.setdefault(hit.chunk_id, []).append("вектор")
            payload.setdefault(hit.chunk_id, hit)

        out: list[SearchHit] = []
        for chunk_id, score in sorted(fused.items(), key=lambda kv: -kv[1])[:limit]:
            hit = payload[chunk_id]
            hit.score = round(score * 1000, 4)
            hit.why = "совпало по: " + ", ".join(sorted(set(why[chunk_id])))
            out.append(hit)
        return out

    def chunk_texts(self, rev_id: str, chunk_ids: list[str]) -> dict[str, str]:
        """Сырой текст чанков по идентификаторам — пакетом, одним обращением к базе.

        Нужен `search --with-text`: без него на каждый хит потребовался бы
        отдельный запрос, и текст пришлось бы брать с диска. Отсутствие таблицы
        или отдельной строки — не ошибка: возврат меньшего словаря означает
        «для этих чанков текста в индексе нет», и вызывающий обязан это пережить
        (прежние ревизии, чужой корпус).
        """
        wanted = [str(c) for c in chunk_ids if c]
        if not wanted or not store.table_exists(self.conn, "chunk_text"):
            return {}
        out: dict[str, str] = {}
        uniq = list(dict.fromkeys(wanted))
        CH = 400
        for start in range(0, len(uniq), CH):
            part = uniq[start : start + CH]
            marks = ",".join("?" for _ in part)
            try:
                rows = self.conn.execute(
                    f"SELECT chunk_id, text FROM chunk_text"
                    f" WHERE rev_id = ? AND chunk_id IN ({marks})",
                    (rev_id, *part),
                ).fetchall()
            except sqlite3.Error:
                return out
            for r in rows:
                out[str(r["chunk_id"])] = str(r["text"] or "")
        return out

    def text_for_span(self, rev_id: str, path: str, start_line: int,
                      end_line: int) -> tuple[int, str] | None:
        """Полный текст спана ИЗ ИНДЕКСА: чанки сшиваются по абсолютным строкам.

        Зачем сшивание. Крупный символ режется на окна по 120 строк с перехлёстом
        10 строк (extract/generic.py), поэтому одного чанка для спана часто мало:
        у `_column_has_default` (67 строк) чтение первого попавшегося чанка дало бы
        только 120-строчное окно, а у символа на 400 строк — 120 из 400,
        БЕЗ признака обрезки. Это ровно тот класс дефекта, который в проекте
        запрещён: молчаливая потеря вместо честного ответа.

        Сшивание идёт по НОМЕРАМ СТРОК, а не конкатенацией текстов: перехлёст
        окон (10 строк) в конкатенации превратился бы в повтор строк, а агент
        увидел бы код дважды и решил, что в файле есть дубль. Сборка в словаре
        «абсолютная строка → её текст» убирает перехлёст точно.

        Возврат: (номер первой строки собранного текста, текст) или None, если
        в индексе нет ни одного пересекающегося чанка. Строки, которых нет ни в
        одном чанке (например модульный код между символами), просто не попадают
        в результат — поэтому вызывающий обязан сверить число строк с запросом.
        """
        if not store.table_exists(self.conn, "chunk_text") or not store.table_exists(
            self.conn, "chunk_map"
        ):
            return None
        try:
            rows = self.conn.execute(
                """SELECT m.start_line AS cstart, m.end_line AS cend, t.text AS ctext
                   FROM chunk_map m JOIN chunk_text t
                     ON t.rev_id = m.rev_id AND t.chunk_id = m.chunk_id
                   WHERE m.rev_id = ? AND m.path = ?
                     AND m.end_line >= ? AND m.start_line <= ?
                   ORDER BY m.start_line""",
                (rev_id, util.to_posix(path), int(start_line), int(end_line)),
            ).fetchall()
        except sqlite3.Error:
            return None
        by_line: dict[int, str] = {}
        for r in rows:
            cstart = int(r["cstart"])
            # Формат текста чанка (замерено на rev-0031, `chunk_id 5c9a57c8`):
            # ПЕРВАЯ строка — служебный заголовок («function indexed_hashes
            # (src/ragraf/watch.py:151)»), и только со второй начинается код,
            # соответствующий файловой строке `chunk_map.start_line`.
            # Проверено: text[1] == файловая строка start_line. Без сдвига на
            # единицу весь спан уезжал бы вверх на строку и вместо функции
            # агент получал бы хвост предыдущей — молчаливая ошибка на единицу,
            # неотличимая от верного ответа.
            for offset, line in enumerate(str(r["ctext"] or "").splitlines()):
                if offset == 0:
                    continue
                by_line.setdefault(cstart + offset - 1, line)
        if not by_line:
            return None
        first = min(by_line)
        last = max(by_line)
        out_lines = [by_line.get(n, "") for n in range(first, last + 1)]
        return first, "\n".join(out_lines)

    def text_for_nodes(self, rev_id: str, keys: list[tuple[str, int]]) -> dict[tuple[str, int], tuple[int, str]]:
        """Тексты чанков по адресу (путь, строка начала спана).

        Ключ — (path, start_line), потому что именно так описан спан в графе, а
        чанк в хранилище имеет свой `start_line`: они совпадают не всегда
        (крупный символ режется на окна по 120 строк — extract/generic.py).
        Поэтому для каждого ключа берётся САМЫЙ РАННИЙ чанк этого пути, чей
        диапазон содержит запрошенную строку, а вместе с текстом возвращается
        его `start_line` — тогда вызывающий отсчитывает окно от реальной
        границы чанка, а не от строки спана. Без этого окно уезжало бы на
        разницу между ними и агент получал бы не те строки.

        Пустой словарь — честный ответ «для этих спанов текста в индексе нет»;
        вызывающий обязан пережить это (прежние ревизии, чужой корпус).
        """
        if not keys or not store.table_exists(self.conn, "chunk_text"):
            return {}
        out: dict[tuple[str, int], tuple[int, str]] = {}
        cm_ok = store.table_exists(self.conn, "chunk_map")
        if not cm_ok:
            return {}
        for path, start_line in keys:
            try:
                row = self.conn.execute(
                    """SELECT m.start_line AS cstart, t.text AS ctext
                       FROM chunk_map m JOIN chunk_text t
                         ON t.rev_id = m.rev_id AND t.chunk_id = m.chunk_id
                       WHERE m.rev_id = ? AND m.path = ?
                         AND m.start_line <= ? AND m.end_line >= ?
                       ORDER BY m.start_line LIMIT 1""",
                    (rev_id, util.to_posix(path), int(start_line), int(start_line)),
                ).fetchone()
            except sqlite3.Error:
                continue
            if row is None or row["ctext"] is None:
                continue
            out[(util.to_posix(path), int(start_line))] = (
                int(row["cstart"]),
                str(row["ctext"]),
            )
        return out

    def snippet(self, rev_id: str, hit: SearchHit, width: int = 160) -> str | None:
        """Сырой фрагмент чанка по границам строк (для ответа агенту).

        Причина правки — замер ревизии rev-0030: прежняя редакция читала
        `snippet(ft_chunk, 4, ...)`, то есть колонку с НОМЕРОМ 4. Колонка с этим
        номером — `code_tokens`, токенизированная форма («def op code self fqn
        path style limit env make env»), а не исходный код: fts5 разбивает текст
        на токены, sqlite3 удаляет колонки UNINDEXED, поэтому в таблице
        физически лежат `symbol_tokens, code_tokens, sql_tokens, text_tokens`.
        Позвать эту функцию для `search` не мог никто: `envelope.to_dict()`
        не сериализует объект SearchHit, и сниппет пропадал молча.

        Теперь берётся сырой текст из хранилища чанков и отдаётся BEGINNING
        ЧАНКА ОГРАНИЧЕННОЙ ДЛИНЫ. Это честный сниппет, а не «окно вокруг
        совпадения»: позиции совпадения здесь нет, и назвать это окном значило
        бы выдумать точность, которой нет.
        """
        if not store.table_exists(self.conn, "chunk_text"):
            return None
        try:
            row = self.conn.execute(
                """SELECT text FROM chunk_text WHERE rev_id = ? AND chunk_id = ?""",
                (rev_id, hit.chunk_id),
            ).fetchone()
        except sqlite3.Error:
            return None
        if not row or not row["text"]:
            return None
        text = str(row["text"])
        # Обрезка по границе строки: первая строка целиком, без обрыва посреди
        # слова и без выдуманного многоточия внутри строки кода.
        first = text.splitlines()[0] if text else ""
        if len(first) > width:
            cut = first.rfind(" ", 0, width)
            first = first[: cut if cut > width // 2 else width] + "…"
        return first

    def counts(self, rev_id: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in ("ft_chunk", "chunk_map", "vec_meta", "chunk_text"):
            try:
                row = self.conn.execute(
                    f"SELECT COUNT(*) AS c FROM {table} WHERE rev_id = ?", (rev_id,)
                ).fetchone()
                out[table] = int(row["c"]) if row else 0
            except sqlite3.Error:
                out[table] = 0
        if self.vec_ok:
            try:
                # В vec0 обычные колонки не хранятся, фильтр по rev_id невозможен:
                # считаем общее число векторов и отдельно пересечение с текущей ревизией.
                total = int(
                    self.conn.execute("SELECT COUNT(*) AS c FROM vec_chunk").fetchone()["c"]
                )
                rev_count = int(
                    self.conn.execute(
                        """SELECT COUNT(*) AS c FROM vec_meta vm
                           JOIN vec_chunk v ON v.chunk_id = vm.chunk_id
                           WHERE vm.rev_id = ?""",
                        (rev_id,),
                    ).fetchone()["c"]
                )
                out["vec_chunk"] = rev_count
                out["vec_chunk_total"] = total
            except sqlite3.Error:
                out["vec_chunk"] = 0
                out["vec_chunk_total"] = 0
        else:
            out["vec_chunk"] = 0
            out["vec_chunk_total"] = 0
        return out

    def purge_rev(self, rev_id: str) -> None:
        """Удаление данных ревизии. vec0 удаляется по chunk_id из vec_meta."""
        try:
            ids = [
                str(r["chunk_id"])
                for r in self.conn.execute(
                    "SELECT chunk_id FROM vec_meta WHERE rev_id = ?", (rev_id,)
                ).fetchall()
            ]
        except sqlite3.Error:
            ids = []
        for table in ("ft_chunk", "chunk_map", "vec_meta", "chunk_text"):
            try:
                self.conn.execute(f"DELETE FROM {table} WHERE rev_id = ?", (rev_id,))
            except sqlite3.Error:
                pass
        if self.vec_ok and ids:
            for start in range(0, len(ids), 400):
                part = ids[start : start + 400]
                marks = ",".join("?" for _ in part)
                try:
                    self.conn.execute(
                        f"DELETE FROM vec_chunk WHERE chunk_id IN ({marks})", tuple(part)
                    )
                except sqlite3.Error:
                    break
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


# --------------------------------------------------------------------------
# Преобразование векторов
# --------------------------------------------------------------------------


def _vec_to_blob(vec: list[float]) -> bytes:
    import struct

    return struct.pack(f"<{len(vec)}f", *vec)


def _blob_to_vec(blob) -> list[float]:
    import struct

    if isinstance(blob, memoryview):
        blob = blob.tobytes()
    if isinstance(blob, (bytes, bytearray)):
        n = len(blob) // 4
        return list(struct.unpack(f"<{n}f", blob[: n * 4]))
    if isinstance(blob, str):
        return [float(x) for x in util.loads(blob)]
    return [float(x) for x in blob]
