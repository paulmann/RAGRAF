"""Общие утилиты SQLite: подключения, схемы, порядок обхода.

Три хранилища (архитектура §4):
  graph.sqlite   — узлы, рёбра, CPG, покрытие
  lexvec.sqlite  — FTS5 (лексика) + vec0 (вектор)
  control.sqlite — ревизии, ledger, архивы (append-only)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import SCHEMA_VERSION

# Причины отказов векторного слоя. Отказ был молчаливым (`return False`), и
# покрытие сообщало «sqlite-vec не загрузился» без причины — разбор упирался
# в догадки. Замер 16.10.2026: расширение загружалось нормально, а отказ
# приходил от записи в vec0; при этом диагностика не видела разницы. Поэтому
# каждый отказ называет себя, а вызывающий код обязан его показать.
LAST_EXTENSION_ERROR = ""
LAST_VEC_TABLE_ERROR = ""


def connect(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Открыть SQLite-соединение с нужными прагмами.

    Про режим WAL и чтение. На соединении `mode=ro` прагма `journal_mode=WAL`
    не выставляется: это запись, и там, где каталог базы недоступен для записи,
    она падает с `unable to open database file` (измерено 15.10.2026 на
    `MD.Viewer\\.ragraf\\control.sqlite`: копия базы читается, а
    открыть её на месте нельзя). База уже в WAL — читать её можно, менять режим
    для чтения не нужно. Прежняя редакция выставляла WAL и на `readonly`,
    поэтому ЧТЕНИЕ требовало права записи, и панель не могла показать
    статистику по чужой разработке.

    Про запасной путь `immutable=1`. Если база не открывается даже так,
    она открывается как неизменяемая: SQLite тогда не создаёт `-shm`/`-wal` и
    не берёт блокировок, поэтому чтение возможно и в каталоге без права записи.
    Цена названа честно: `immutable` запрещает замечать чужие изменения, и если
    индекс прямо сейчас пишет другой процесс, читатель увидит состояние на
    момент открытия. Для панели, которая показывает размеры и статистику, это
    допустимо, а для агента — нет, поэтому запасной путь включается ТОЛЬКО на
    явно read-only соединении.
    """
    p = Path(path)
    if readonly:
        conn = _connect_readonly(p)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    # Таймаут ожидания ставится ПЕРВЫМ: смена режима журнала требует
    # исключительной блокировки, и без уже установленного обработчика занятости
    # она падает сразу, а не ждёт писателя (AGENTS.md: соединение с WAL и
    # busy_timeout — контракт, а не оптимизация; порядок установки здесь и есть
    # часть контракта).
    conn.execute("PRAGMA busy_timeout=10000")
    if readonly:
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def _connect_readonly(path: Path) -> sqlite3.Connection:
    """Соединение только для чтения. Запасной путь — `immutable=1`.

    Соединение ПРОВЕРЯЕТСЯ пробой. `sqlite3.connect()` ленив: он не открывает
    файл до первого обращения, поэтому попытка поймать отказ вокруг самого
    вызова бесполезна — исключение приходит позже, у вызывающего кода
    (измерено 15.10.2026: запасной путь не срабатывал, и панель продолжала
    показывать `OperationalError`). Проба `PRAGMA schema_version` открывает
    файл и делает отказ видимым здесь.
    """
    attempts = (
        f"file:{path.as_posix()}?mode=ro",
        f"file:{path.as_posix()}?immutable=1",
    )
    last: sqlite3.OperationalError | None = None
    for uri in attempts:
        try:
            conn = sqlite3.connect(uri, uri=True)
        except sqlite3.OperationalError as exc:
            last = exc
            continue
        try:
            conn.execute("PRAGMA schema_version").fetchone()
            return conn
        except sqlite3.OperationalError as exc:
            # Именно здесь проявляется недоступность каталога: WAL требует
            # `-shm`, который нельзя создать без права записи.
            last = exc
            conn.close()
    # Сообщение `${path} не существует` отличает «индекс не собран» от «нет
    # права записи»: обе причины дают один и тот же текст SQLite
    # `unable to open database file`, а подсказки у них разные. Замерено
    # 15.10.2026 на разработке AutoClaw: есть только `control.sqlite`, баз
    # графа и лексики нет вовсе, и панель советовала проверить права.
    if not path.exists():
        raise sqlite3.OperationalError(f"{path} не существует")
    raise last if last else sqlite3.OperationalError("cannot open database")


def try_load_extension(conn: sqlite3.Connection, library_name: str) -> bool:
    """Попытка загрузить расширение SQLite (sqlite-vec). Пустая = только лексика.

    Причина отказа запоминается в `LAST_EXTENSION_ERROR`: молчаливый False
    превращал любую поломку в догадку, а в покрытии оставалась неверная фраза
    «sqlite-vec не загрузился». Исключение не подавляется — оно становится
    диагностикой.
    """
    global LAST_EXTENSION_ERROR
    LAST_EXTENSION_ERROR = ""
    try:
        if not hasattr(conn, "enable_load_extension"):
            LAST_EXTENSION_ERROR = "модуль sqlite3 без поддержки загрузки расширений"
            return False
        conn.enable_load_extension(True)
        try:
            import sqlite_vec  # type: ignore

            sqlite_vec.load(conn)
        except Exception as exc:
            first = f"sqlite_vec.load: {type(exc).__name__}: {exc}"
            try:
                conn.load_extension(library_name)
            except Exception as exc2:
                LAST_EXTENSION_ERROR = (
                    f"{first}; запасной путь ({library_name}): "
                    f"{type(exc2).__name__}: {exc2}"
                )
                return False
        finally:
            conn.enable_load_extension(False)
        return True
    except Exception as exc:
        LAST_EXTENSION_ERROR = f"{type(exc).__name__}: {exc}"
        return False


def vec_available(conn: sqlite3.Connection) -> bool:
    """Проверка, доступен ли vec0 (тесты M4/НФТ: векторный слой опционален)."""
    try:
        row = conn.execute(
            "SELECT 1 FROM pragma_module_list WHERE name = 'vec0'"
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? LIMIT 1", (name,)
    ).fetchone()
    return row is not None


#: Состояния, в которых запись ревизии описывает РЕАЛЬНО отданный индекс.
#: `building` — числа ещё не записаны; `failed` — прогон не состоялся;
#: `aborted` — прерван по бюджету или валидатору; `rotated` живёт только в истории
#: (`rev_history`), в `rev` его быть не должно, но проверка не должна на это надеяться.
SEALED_STATES: tuple[str, ...] = ("ready", "sealed")

#: Состояния, которые означают «запись есть, отдавать нечего».
UNSEALED_STATES: tuple[str, ...] = ("building", "failed", "aborted")


def active_revision(conn: sqlite3.Connection,
                    *, table: str = "rev",
                    states: tuple[str, ...] = SEALED_STATES,
                    skip: tuple[str, ...] = UNSEALED_STATES,
                    ) -> tuple[dict | None, list[dict]]:
    """Активная (запечатанная) ревизия и записи новее неё, которые не запечатаны.

    Возвращает пару:

      * словарь активной ревизии или `None`, если запечатанных нет вовсе;
      * список незапечатанных записей, чей номер ВЫШЕ активной, — по убыванию номера.
        Пустой список означает «последняя запись и есть активная», и это не мелочь:
        скрывать пропущенную ревизию нельзя, иначе факт «прогон прерван» исчезает из
        ответа, и пользователь видит спокойное «актуально» при незаконченной сборке.

    `skip` — состояния, которые считаются незапечатанными и докладываются. Записи с
    другими состояниями (неизвестными) не считаются активными, но и не докладываются:
    выдумывать про них нечего.
    """
    marks = ",".join("?" * len(states))
    placeholders = ",".join("?" * len(skip))

    columns = "*"
    try:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        names = {str(row[1]) for row in info}
    except sqlite3.Error:
        return None, []
    if not names:
        return None, []

    # Колонки, которых может не быть в старой базе, запрашиваются по факту схемы.
    wanted = [name for name in ("rev_id", "state", "file_count", "node_count", "edge_count",
                                "capability", "created_at", "corpus_hash", "config_hash",
                                "parent_rev") if name in names]
    if "rev_id" not in wanted:
        return None, []
    columns = ", ".join(wanted)

    def as_dict(cursor, row) -> dict:
        # Словарь строится по описанию курсора, а не через `dict(row)`: соединение
        # может быть открыто БЕЗ `row_factory = sqlite3.Row` (так открывает снимок
        # ревизии), и `dict(row)` на кортеже из восьми полей падает — измерено
        # 29.10.2026 первым же прогоном после правки.
        names = [item[0] for item in cursor.description]
        return dict(zip(names, row))

    try:
        cursor = conn.execute(
            f"SELECT {columns} FROM {table} WHERE state IN ({marks}) "
            f"ORDER BY rev_id DESC LIMIT 1", states)
        row = cursor.fetchone()
        active = as_dict(cursor, row) if row is not None else None
    except sqlite3.Error:
        return None, []

    skipped: list[dict] = []
    try:
        bound = str(active["rev_id"]) if active else ""
        if bound:
            cursor = conn.execute(
                f"SELECT {columns} FROM {table} WHERE state IN ({placeholders}) "
                f"AND rev_id > ? ORDER BY rev_id DESC", (*skip, bound))
        else:
            cursor = conn.execute(
                f"SELECT {columns} FROM {table} WHERE state IN ({placeholders}) "
                f"ORDER BY rev_id DESC", skip)
        skipped = [as_dict(cursor, item) for item in cursor.fetchall()]
    except sqlite3.Error:
        skipped = []

    return active, skipped


def describe_skipped(skipped: list[dict]) -> str:
    """Короткая строка «что пропущено» для ответа операции: ревизия и состояние."""
    if not skipped:
        return ""
    parts = [f"{item.get('rev_id')} ({item.get('state')})" for item in skipped[:4]]
    tail = "" if len(skipped) <= 4 else f" и ещё {len(skipped) - 4}"
    return "новее активной, но не запечатаны: " + ", ".join(parts) + tail


def executemany_chunked(
    conn: sqlite3.Connection, sql: str, rows: Iterable[Sequence[Any]], batch: int = 2000
) -> int:
    """executemany порциями: большие корпуса не должны держать всё в памяти."""
    total = 0
    buf: list[Sequence[Any]] = []
    for row in rows:
        buf.append(row)
        if len(buf) >= batch:
            conn.executemany(sql, buf)
            total += len(buf)
            buf.clear()
    if buf:
        conn.executemany(sql, buf)
        total += len(buf)
    return total


def edge_columns(conn: sqlite3.Connection) -> set[str]:
    """Колонки таблицы `edge` (пустое множество, если таблицы нет)."""
    try:
        return {str(r[1]) for r in conn.execute("PRAGMA table_info(edge)")}
    except sqlite3.Error:
        return set()


def insert_edges(
    conn: sqlite3.Connection,
    rows: Iterable[Sequence[Any]],
    batch: int = 2000,
) -> int:
    """Единственный вход записи рёбер графа.

    Зачем один вход. Рёбра пишут три места: разбор деревом (через
    `RevisionWriter.add_edges`), разрешение имён (тоже через него) и слияние
    CPG (`cpg.merge_calls` — своим запросом). Пока их два, вид доказательства
    (этап 8) пришлось бы вычислять в каждом, и правила разошлись бы так же, как
    когда-то разошлись ключи `callee_name`/`calleeName`.

    Вид вычисляется здесь, если производитель его не передал: строка из
    11 полей дополняется двенадцатым, строка из 12 полей пишется как есть.
    Если в схеме колонки ещё нет (своя DDL в проверках, старая база без
    миграции) — пишутся 11 полей: отсутствие колонки не имеет права ломать
    индексацию.

    Список колонок строится по факту `PRAGMA table_info`, а не по версии
    схемы: так одно и то же работает и на мигрированной базе, и на самодельной
    схеме проверок.
    """
    from . import evidence as evidence_mod

    cols = edge_columns(conn)
    has_evidence = "evidence_kind" in cols
    base_cols = ("rev_id, edge_uid, src, dst, kind, provenance, "
                 "evidence_path, evidence_line, confidence, sources, props")
    if has_evidence:
        sql = (f"INSERT OR REPLACE INTO edge({base_cols}, evidence_kind) "
               "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
    else:
        sql = (f"INSERT OR REPLACE INTO edge({base_cols}) "
               "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")

    #: Внешние узлы ревизии: их определение лежит вне корпуса, поэтому ребро к
    #: ним — «цель вне корпуса», а не «вывод по имени о коде проекта». Список
    #: берётся ИЗ ТАБЛИЦЫ УЗЛОВ и кэшируется по ревизии: узлы пишутся раньше
    #: рёбер (`indexer._build_facts` → add_nodes, затем add_edges), поэтому на
    #: момент записи он уже полон. Замер 31.10.2026 (rev-0986): признак по
    #: адресу `@…` поймал только 183 ребра из 55 815 — у остальных цель была
    #: хэшем узла `external` (`python:print`, `python:get`), и вид доказательства
    #: «выведено по имени» описывал вызов библиотеки.
    _external_cache: dict[str, set[str]] = {}

    def _is_external(rev_id: str, dst: str) -> bool:
        if str(dst).startswith("@"):
            return True
        known = _external_cache.get(rev_id)
        if known is None:
            try:
                known = {
                    str(r[0])
                    for r in conn.execute(
                        "SELECT node_id FROM node WHERE rev_id = ? AND kind = 'external'",
                        (rev_id,),
                    ).fetchall()
                }
            except sqlite3.Error:
                # Своя DDL в проверках может не иметь колонки `kind`: отсутствие
                # сведений не имеет права ломать запись.
                known = set()
            _external_cache[rev_id] = known
        return str(dst) in known

    def prepared() -> Iterable[Sequence[Any]]:
        for row in rows:
            row = tuple(row)
            if not has_evidence:
                yield row[:11]
                continue
            if len(row) >= 12:
                yield row[:12]
                continue
            # kind, provenance, confidence, props, evidence_path/line — позиции
            # в строке ребра; координата нужна, чтобы «подтверждено» не
            # оказалось ребром, которое нельзя открыть и проверить.
            # Цель-заглушка (`@ext:`/`@file:`/`@php:`/`@table:`) — это внешний
            # адрес: определения в корпусе нет. Признак вычисляется по САМОМУ
            # адресу (row[3] — dst), а не запросом к таблице узлов: заглушки
            # заводятся позже записи рёбер, и запрос вернул бы пусто.
            yield row + (evidence_mod.classify(
                row[4], row[5], row[10], row[8], row[6], row[7],
                target_external=_is_external(str(row[0]), str(row[3])),
            ),)

    return executemany_chunked(conn, sql, prepared(), batch=batch)


# --------------------------------------------------------------------------
# DDL
# --------------------------------------------------------------------------

GRAPH_DDL = f"""
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rev (
  rev_id TEXT PRIMARY KEY,
  parent_rev TEXT,
  created_at TEXT NOT NULL,
  corpus_hash TEXT NOT NULL DEFAULT '',
  config_hash TEXT NOT NULL DEFAULT '',
  extractor_versions TEXT NOT NULL DEFAULT '{{}}',
  state TEXT NOT NULL DEFAULT 'building',   -- building|ready|failed
  capability TEXT NOT NULL DEFAULT 'degraded',  -- full|degraded
  joern_used INTEGER NOT NULL DEFAULT 0,
  file_count INTEGER NOT NULL DEFAULT 0,
  node_count INTEGER NOT NULL DEFAULT 0,
  edge_count INTEGER NOT NULL DEFAULT 0,
  schema_version INTEGER NOT NULL DEFAULT {SCHEMA_VERSION}
);

CREATE TABLE IF NOT EXISTS file (
  rev_id TEXT NOT NULL,
  path TEXT NOT NULL,
  lang TEXT,
  lines INTEGER,
  sha256 TEXT NOT NULL,
  parse_status TEXT NOT NULL,     -- ok|partial|failed|skipped
  parse_note TEXT,
  -- Кодировка исходного файла (utf-8|cp1251|utf-16|cp1252). Нужна, чтобы
  -- ответ поиска объяснял, ПОЧЕМУ строка нашлась в файле, который читается
  -- не как UTF-8: без этого признака «нашлось в cp1251» выглядело бы так же,
  -- как «нашлось в utf-8», и потерю кодировки нельзя было бы заметить.
  encoding TEXT NOT NULL DEFAULT 'utf-8',
  PRIMARY KEY (rev_id, path)
);

CREATE TABLE IF NOT EXISTS node (
  rev_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  name TEXT,
  fqn TEXT,
  path TEXT,
  start_line INTEGER,
  end_line INTEGER,
  signature TEXT,
  props TEXT,
  layer TEXT NOT NULL DEFAULT 'structural',  -- structural|text
  PRIMARY KEY (rev_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_node_fqn ON node(rev_id, fqn);
CREATE INDEX IF NOT EXISTS idx_node_path ON node(rev_id, path);
CREATE INDEX IF NOT EXISTS idx_node_kind ON node(rev_id, kind);

CREATE TABLE IF NOT EXISTS edge (
  rev_id TEXT NOT NULL,
  edge_uid TEXT NOT NULL,
  src TEXT NOT NULL,
  dst TEXT NOT NULL,
  kind TEXT NOT NULL,
  provenance TEXT NOT NULL,       -- stated|derived
  evidence_path TEXT,
  evidence_line INTEGER,
  confidence REAL NOT NULL DEFAULT 1.0,
  sources TEXT NOT NULL DEFAULT '[]',
  -- Свойства ребра (JSON): признаки, которые иначе теряются. Пример —
  -- вид доступа к таблице write/access для ребра kind='queries' (U4).
  -- Двойные скобки: весь DDL — f-строка с подстановкой версии схемы,
  -- поэтому литеральные скобки требуют удвоения.
  props TEXT NOT NULL DEFAULT '{{}}',
  -- Вид доказательства связи (этап 8): cpg_call | signature_match |
  -- name_inference | structural | unknown. У рёбер, построенных до этапа 8,
  -- значение пустое: вид восстанавливается классификатором `evidence.classify`
  -- по полям самого ребра, поэтому переиндексация не обязательна.
  evidence_kind TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (rev_id, edge_uid)
);
CREATE INDEX IF NOT EXISTS idx_edge_src ON edge(rev_id, src, kind);
CREATE INDEX IF NOT EXISTS idx_edge_dst ON edge(rev_id, dst, kind);

CREATE TABLE IF NOT EXISTS text_chunk (
  rev_id TEXT NOT NULL,
  chunk_id TEXT NOT NULL,
  owner_node_id TEXT,
  path TEXT NOT NULL,
  start_line INTEGER,
  end_line INTEGER,
  text_kind TEXT NOT NULL,        -- comment|docblock|md|commit
  raw TEXT NOT NULL,
  PRIMARY KEY (rev_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_text_owner ON text_chunk(rev_id, owner_node_id);
CREATE INDEX IF NOT EXISTS idx_text_path ON text_chunk(rev_id, path);

CREATE TABLE IF NOT EXISTS cfg_edge (
  rev_id TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL,
  func_node_id TEXT NOT NULL, edge_order INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cfg_func ON cfg_edge(rev_id, func_node_id);

CREATE TABLE IF NOT EXISTS dfg_edge (
  rev_id TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL,
  var_name TEXT, evidence_line INTEGER
);
CREATE INDEX IF NOT EXISTS idx_dfg_src ON dfg_edge(rev_id, src);

CREATE TABLE IF NOT EXISTS db_table (
  rev_id TEXT NOT NULL, table_id TEXT NOT NULL, table_name TEXT NOT NULL,
  engine TEXT, row_estimate INTEGER, source TEXT NOT NULL,
  PRIMARY KEY (rev_id, table_id)
);

CREATE TABLE IF NOT EXISTS db_column (
  rev_id TEXT NOT NULL, table_id TEXT NOT NULL, column_name TEXT NOT NULL,
  data_type TEXT, is_nullable INTEGER, column_key TEXT, ordinal INTEGER,
  -- P1-4: признаки, по которым решается «можно ли писать» и «это ключ».
  -- Отдельные колонки, а не строка `extra`: разбор строки в каждом читателе
  -- уже дал измеренный дефект (колонка считалась генерируемой по подстроке
  -- "GENERATED", из-за чего 92 обычные колонки объявлялись нерехзаписываемыми).
  is_unique INTEGER NOT NULL DEFAULT 0,
  auto_increment INTEGER NOT NULL DEFAULT 0,
  generated_kind TEXT NOT NULL DEFAULT '',
  has_default INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (rev_id, table_id, column_name)
);

CREATE TABLE IF NOT EXISTS db_fk (
  rev_id TEXT NOT NULL, from_table TEXT NOT NULL, from_column TEXT NOT NULL,
  to_table TEXT NOT NULL, to_column TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS web_route (
  rev_id TEXT NOT NULL, route_id TEXT NOT NULL, node_id TEXT, route TEXT NOT NULL,
  method TEXT, evidence_path TEXT, evidence_line INTEGER, confidence REAL DEFAULT 1.0,
  PRIMARY KEY (rev_id, route_id)
);

CREATE TABLE IF NOT EXISTS invalidated (
  rev_id TEXT NOT NULL, path TEXT, node_id TEXT, reason TEXT NOT NULL, ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coverage (
  rev_id TEXT NOT NULL, metric TEXT NOT NULL, value REAL, note TEXT,
  PRIMARY KEY (rev_id, metric)
);

CREATE TABLE IF NOT EXISTS stage_timing (
  rev_id TEXT NOT NULL, stage TEXT NOT NULL, seconds REAL NOT NULL,
  items INTEGER, note TEXT,
  PRIMARY KEY (rev_id, stage)
);
"""


LEXVEC_DDL_FTS = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE VIRTUAL TABLE IF NOT EXISTS ft_chunk USING fts5(
  rev_id UNINDEXED,
  chunk_id UNINDEXED,
  path UNINDEXED,
  symbol_tokens,
  code_tokens,
  sql_tokens,
  text_tokens,
  tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS chunk_map (
  rev_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
  source_kind TEXT NOT NULL,       -- symbol|file|table|route|doc
  path TEXT, node_id TEXT, start_line INTEGER, end_line INTEGER,
  lang TEXT, tokens INTEGER,
  PRIMARY KEY (rev_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_chunkmap_path ON chunk_map(rev_id, path);
CREATE INDEX IF NOT EXISTS idx_chunkmap_node ON chunk_map(rev_id, node_id);

CREATE TABLE IF NOT EXISTS vec_meta (
  rev_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
  model TEXT NOT NULL, model_version TEXT, chunk_sha256 TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  PRIMARY KEY (rev_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_vecmeta_hash ON vec_meta(text_hash);
"""

# Сырой текст чанка — отдельное хранилище, а не колонка FTS-таблицы.
#
# Зачем отдельно. Ответ агенту должен уметь отдать фрагмент за ОДИН запрос:
# до этой правки `search` возвращал только адрес хита (path, строки) и признак
# совпадения, а текст приходилось забирать вторым вызовом с диска. Замерено:
# 20 хитов на `search "corpus_hash"` требовали 20 обращений к файлам.
#
# Почему НЕ колонка ft_chunk. У fts5 число весов bm25() обязано равняться числу
# колонок: добавление восьмой колонки молча сдвинуло бы все веса, а номер
# колонки-цели в snippet() (lexvec.py:458) указывал бы на другое поле — то есть
# поиск перестал бы ранжировать по смыслу и никто бы этого не заметил
# (гейта на схему FTS в проекте нет). Отдельная таблица оставляет FTS-индекс
# нетронутым: поиск работает как раньше, а текст лежит рядом.
#
# Токенизированная форма в ft_chunk остаётся нужной и НЕ дублирует эту таблицу:
# она разбита по токенам для BM25 (camelCase/snake_case), по ней считается
# совпадение и строится snippet() через FTS. Сырой текст для ранжирования
# непригоден — он именно для показа.
LEXVEC_DDL_CHUNK_TEXT = """
CREATE TABLE IF NOT EXISTS chunk_text (
  rev_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
  path TEXT, text TEXT NOT NULL,
  PRIMARY KEY (rev_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_chunktext_path ON chunk_text(rev_id, path);
"""


CONTROL_DDL = f"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS rev_history (
  rev_id TEXT PRIMARY KEY,
  parent_rev TEXT,
  project TEXT NOT NULL,
  state TEXT NOT NULL,              -- ready|failed|rotated
  capability TEXT NOT NULL,
  created_at TEXT NOT NULL,
  sealed_at TEXT,
  heartbeat_at TEXT,              -- время последнего признака живости прогона
  corpus_hash TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  files INTEGER, nodes INTEGER, edges INTEGER,
  joern_used INTEGER NOT NULL DEFAULT 0,
  schema_version INTEGER NOT NULL DEFAULT {SCHEMA_VERSION}
);

-- Журнал расхождений: append-only (ФТ-26). Нет путей удаления записей.
CREATE TABLE IF NOT EXISTS drift_entry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  index_rev TEXT NOT NULL,
  ts TEXT NOT NULL,
  path TEXT NOT NULL,
  kind TEXT NOT NULL,               -- add|modify|delete|rename|schema_change|slice_boundary
  old_hash TEXT, new_hash TEXT,
  symbols_added TEXT, symbols_removed TEXT, symbols_changed TEXT,
  edges_invalidated INTEGER DEFAULT 0,
  detected_by TEXT NOT NULL,
  commit_sha TEXT,
  ack TEXT,
  note TEXT,
  dedupe_key TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_drift_rev ON drift_entry(index_rev, id);
CREATE INDEX IF NOT EXISTS idx_drift_path ON drift_entry(index_rev, path);

CREATE TABLE IF NOT EXISTS stale_edge (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  index_rev TEXT NOT NULL,
  edge_uid TEXT NOT NULL,
  path TEXT,
  reason TEXT NOT NULL,
  drift_entry_id INTEGER NOT NULL,
  acked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_stale_rev ON stale_edge(index_rev, path);

CREATE TABLE IF NOT EXISTS drift_archive (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project TEXT NOT NULL,
  from_rev TEXT NOT NULL,
  to_rev TEXT NOT NULL,
  archived_at TEXT NOT NULL,
  entries INTEGER NOT NULL,
  report_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calibration (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL, kind TEXT NOT NULL,
  params TEXT NOT NULL, sample_size INTEGER NOT NULL, note TEXT
);

CREATE TABLE IF NOT EXISTS ack (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  drift_entry_id INTEGER NOT NULL, by_whom TEXT NOT NULL,
  ts TEXT NOT NULL, note TEXT
);

CREATE TABLE IF NOT EXISTS event_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT
);
"""


def init_graph(conn: sqlite3.Connection) -> None:
    conn.executescript(GRAPH_DDL)
    _migrate_graph(conn)


def _migrate_graph(conn: sqlite3.Connection) -> None:
    """Догоняющие изменения схемы графа для уже существующих баз.

    Зачем: `CREATE TABLE IF NOT EXISTS` не добавляет новые колонки в существующую
    таблицу. Без этого шага новый признак (props у рёбер, U4) появился бы только
    на чистой базе, а на рабочем корпусе молча отсутствовал — и операция
    table_usage снова не отдавала бы вид доступа.

    Каждое изменение проверяется по PRAGMA table_info и применяется только
    при отсутствии колонки: повторный запуск безопасен.
    """
    migrations: list[tuple[str, str, str]] = [
        # DDL передаётся отдельной строкой без f-подстановки: в тексте есть
        # фигурные скобки (значение по умолчанию '{}'), а f-строка их ломает.
        ("edge", "props", "TEXT NOT NULL DEFAULT '" + "{}" + "'"),
        # Кодировка файла появилась с P1-1. На уже построенных корпусах
        # колонки нет, и `INSERT INTO file(..., encoding)` упал бы с
        # «no such column» — то есть первый же `ensure` после обновления
        # сломал бы индексацию. Значение по умолчанию 'utf-8' выбрано как
        # честное «не знаем»: до этой правки всё и читалось как UTF-8.
        ("file", "encoding", "TEXT NOT NULL DEFAULT 'utf-8'"),
        # Признаки колонок схемы из миграций (P1-4). На существующих базах
        # колонок нет, а вставка уже их перечисляет — без этой миграции
        # первый же прогон упал бы с «no such column».
        ("db_column", "is_unique", "INTEGER NOT NULL DEFAULT 0"),
        ("db_column", "auto_increment", "INTEGER NOT NULL DEFAULT 0"),
        ("db_column", "generated_kind", "TEXT NOT NULL DEFAULT ''"),
        ("db_column", "has_default", "INTEGER NOT NULL DEFAULT 0"),
        # Вид доказательства ребра (этап 8). Изменение аддитивное: колонка с
        # пустым значением по умолчанию, читатели восстанавливают вид по полям
        # ребра. Пустое значение означает «построено до этапа 8», а не
        # «доказательств нет» — разница видна в ответе (`evidence_source`).
        ("edge", "evidence_kind", "TEXT NOT NULL DEFAULT ''"),
    ]
    for table, column, ddl in migrations:
        try:
            cols = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue
        if not cols or column in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        except sqlite3.Error:
            # Схема могла быть занята другим процессом: это не повод падать,
            # следующая индексация повторит попытку.
            pass
    conn.commit()
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )


def init_lexvec(conn: sqlite3.Connection) -> None:
    conn.executescript(LEXVEC_DDL_FTS)
    # Догоняющая миграция FTS-хранилища: таблица сырого текста появилась позже
    # самой схемы. CREATE TABLE IF NOT EXISTS на существующих корпусах (z, revoap,
    # md.viewer) просто создаёт таблицу; чанки прежних ревизий в неё не попадают,
    # и читатель честно уходит на диск — данные не подделываются.
    conn.executescript(LEXVEC_DDL_CHUNK_TEXT)
    # Таблицы документного слоя (этап «документы», фаза A). Лежат в том же
    # файле, что и чанки кода: второй SQLite-файл означал бы второй поиск, а
    # «документ ищется отдельно от кода» — не то, что нужно агенту.
    #
    # DDL аддитивен и идемпотентен (`CREATE TABLE IF NOT EXISTS`): на
    # существующих корпусах он только создаёт отсутствующие таблицы. Второго
    # `schema_version` он не пишет — версия схемы остаётся одной на файл, иначе
    # читатель не смог бы отличить «старую базу» от «базу без документов».
    from .docindex import DOCUMENT_DDL

    conn.executescript(DOCUMENT_DDL)

    # Документный граф (этап 1 плана «Агентские интеграции»): таблицы слоя
    # фактов и кэш ответов LLM. Тот же файл и те же правила, что у документного
    # слоя: второй SQLite-файл означал бы вторую точку состояния, которую надо
    # не забыть перенести и резервировать.
    #
    # Таблицы слоя несут `rev_id`, поэтому попадают под политику хранения
    # автоматически (`storage.tables_with_rev` считает список по схеме, а не по
    # рукописному перечню). У `llm_cache` `rev_id` нет намеренно: кэш
    # адресуется содержимым и переживает ротацию ревизий — иначе повторный
    # прогон того же корпуса платил бы заново.
    from .docgraph import DOCGRAPH_DDL
    from .llm import LLM_CACHE_DDL

    conn.executescript(DOCGRAPH_DDL)
    conn.executescript(LLM_CACHE_DDL)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )


def init_control(conn: sqlite3.Connection) -> None:
    conn.executescript(CONTROL_DDL)
    # Догоняющая миграция: признак живости прогона (этап 12, находка 2).
    # Прежние журналы не требуют удаления: колонка добавляется на месте, а
    # старое значение `NULL` читается как «признак не выставлялся».
    rev_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(rev_history)")}
    if rev_cols and "heartbeat_at" not in rev_cols:
        conn.execute("ALTER TABLE rev_history ADD COLUMN heartbeat_at TEXT")
    # Версия схемы пишется только при РАСХОЖДЕНИИ. Раньше здесь стоял
    # безусловный `INSERT OR REPLACE`, то есть КАЖДОЕ открытие журнала было
    # записью и требовало блокировки записи. Замерено 13.10.2026: пока другой
    # процесс переиндексировал корпус (он держит блокировку десятки секунд),
    # создание фасада во втором процессе падало с
    # `OperationalError: database is locked` — то есть читатель не мог даже
    # ОТКРЫТЬ журнал рядом с писателем, ради чего и заведён WAL. Проверка
    # версии блокировки записи не требует, поэтому она идёт первой, а запись
    # остаётся там, где значение действительно меняется.
    row = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    if row is not None and str(row[0]) == str(SCHEMA_VERSION):
        return
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    # Коммит здесь, а не у вызывающего: иначе открытая транзакция живёт до
    # первой удачной записи этого соединения и всё это время держит блокировку
    # записи журнала — то есть второй процесс (CLI рядом с MCP, сторож при
    # живом сервере) писал в него не может вовсе.
    conn.commit()


def ensure_vec_table(conn: sqlite3.Connection) -> bool:
    """Создать vec0-таблицу, если расширение доступно. False — вектор недоступен.

    Причина отказа запоминается в `LAST_VEC_TABLE_ERROR` (см.
    `try_load_extension`): без неё нельзя отличить «расширение не загрузилось»
    от «таблица не создалась».
    """
    global LAST_VEC_TABLE_ERROR
    LAST_VEC_TABLE_ERROR = ""
    if not vec_available(conn):
        LAST_VEC_TABLE_ERROR = (
            "vec0 отсутствует в pragma_module_list: расширение не загружено "
            "на соединении"
        )
        return False
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunk USING vec0("
            "chunk_id TEXT PRIMARY KEY, rev_id TEXT, embedding FLOAT[384])"
        )
        return True
    except sqlite3.Error as exc:
        LAST_VEC_TABLE_ERROR = f"CREATE vec_chunk: {type(exc).__name__}: {exc}"
        return False
