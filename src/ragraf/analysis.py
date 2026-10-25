"""Структурный анализ корпуса: семейства файлов, точки входа, достижимость, мёртвый код (P1-2).

Зачем отдельный модуль. До него в RAGRAF были только операции «про один
символ» (symbol, callers, references) и «про один файл» (code, live_read).
На вопрос, который на реальной разработке задаётся первым, ответить было
нечем:

  * какие файлы — копии друг друга (`Files.php` … `Files7.php`), какой из
    них рабочий, а какие можно удалять;
  * какие файлы вообще подключены к точке входа, а какие лежат мёртвым
    грузом (`import1.pl`, `import2.pl`, `import_stdin.pl` рядом с `import.pl`);
  * где точки входа: роуты, endpoint'ы, `action=`, `src=`, CLI-скрипты.

Это класс задач, который агент закрывал листингами и чтением JS вручную
(замер HARNESS: 1 вместо 4–6 обращений на файл). Поэтому здесь собраны
ТРИ источника признака файла:

  1. **граф** — рёбра `@file:` (include/require), `reads_superglobal`,
     вызовы (даёт «есть ли входящие ссылки из кода»);
  2. **разметка и скрипты** — литеральные пути внутри текста файлов
     (`BWO_FILES_ENDPOINT = 'php/Wizard/Files7.php'`, `src="lib/x.js"`);
     это НЕ ребро графа, и именно поэтому такие связи раньше не находились;
  3. **реестр маршрутов** (`web_route`) — заполняется извлекателями (P1-3).

Разделение видов ссылок сохраняется в ответе, потому что это разные степени
доверия: ребро include — факт, литерал в разметке — тоже факт, но о другом
(«на этот файл ссылаются как на ресурс»), а совпадение имён — вывод.

Модуль ничего не пишет: он только читает граф и текст чанков.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from dataclasses import dataclass, field

from . import util
from .extract.php_ast import ENDPOINT_ATTR_RE, ENDPOINT_CALL_RE

#: Расширения, которые считаются исполняемыми точками входа при наличии shebang.
CLI_EXT = (".pl", ".cgi", ".py", ".sh", ".php", ".rb")
#: Слово, похожее на путь к файлу: имя с расширением, возможно с каталогами.
PATH_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-/\\]{2,}\.[A-Za-z0-9]{1,8}")


def _norm(path: str) -> str:
    """POSIX-путь без ведущих `./` и лишних слэшей."""
    p = util.to_posix(path).strip()
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def family_key(path: str) -> str:
    """Имя семейства копий: путь без служебных номеров у последнего сегмента.

    `php/Wizard/Files7.php` → `php/Wizard/Files.php`;
    `cgi-bin/import1.pl`    → `cgi-bin/import.pl`;
    `cgi-bin/import_stdin.pl` → `cgi-bin/import_stdin.pl` (это другое имя,
    а не копия с номером).

    Почему по имени, а не по содержимому. Копии в живых проектах расходятся:
    `Files.php` и `Files7.php` — не побайтовые клоны, но одно семейство,
    которое агент обязан увидеть вместе. Сравнение только по хэшу такие
    группы пропускает целиком (штатное схлопывание дублей именно так и
    работает — оно для контрольной суммы, а не для ответа человеку).
    """
    p = _norm(path)
    head, tail = posixpath.split(p)
    stem, dot, ext = tail.rpartition(".")
    if not dot:
        stem, ext = tail, ""
    stem = re.sub(r"[_\-]?\d+$", "", stem)
    tail = f"{stem}.{ext}" if ext else stem
    return f"{head}/{tail}" if head else tail


def _similarity(a: str, b: str) -> float:
    """Доля общих строк без учёта порядка и повторов (мера Жаккара).

    Сравниваются НОРМАЛИЗОВАННЫЕ строки: обрезанные пробелы и отброшенные
    пустые. Порядок не учитывается осознанно: у копий, разошедшихся
    правками, блоки часто переставлены, и построчное сравнение «в лоб»
    показало бы почти полное различие там, где файлы почти одинаковы.
    """
    sa = {ln.strip() for ln in a.splitlines() if ln.strip()}
    sb = {ln.strip() for ln in b.splitlines() if ln.strip()}
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    return inter / len(sa | sb)


@dataclass
class FileInfo:
    path: str
    lang: str = ""
    encoding: str = "utf-8"
    sha256: str = ""
    lines: int = 0


@dataclass
class Ref:
    path: str            # файл, где встретилась ссылка
    line: int
    kind: str            # include | literal | attribute | route | superglobal
    target: str          # на что ссылаются (путь)
    text: str = ""       # доказательство: фрагмент строки


@dataclass
class Knowledge:
    """Знание о файлах ревизии: файлы, ссылки, точки входа, достижимость."""

    files: dict[str, FileInfo] = field(default_factory=dict)
    refs: dict[str, list[Ref]] = field(default_factory=dict)      # target -> ссылки
    by_source: dict[str, list[Ref]] = field(default_factory=dict)  # source -> ссылки
    entrypoints: dict[str, list[Ref]] = field(default_factory=dict)
    reachable: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    def incoming(self, path: str) -> list[Ref]:
        return self.refs.get(path, [])

    def is_entrypoint(self, path: str) -> bool:
        return path in self.entrypoints


def _prefix_range(prefix: str) -> tuple[str, str]:
    """Диапазон значений для поиска строк с данным префиксом ПО ИНДЕКСУ.

    `LIKE '@file:%'` индекс не использует: LIKE по умолчанию регистронезависим,
    и SQLite не может превратить его в диапазон — запрос читает все рёбра
    ревизии (на BWO это 1,3 млн строк и около двух секунд). Сравнение
    `dst >= '@file:' AND dst < '@filf'` даёт тот же результат (префикс
    `@file:` меньше `@filf`, потому что ':' < 'f') и идёт по индексу
    `idx_edge_dst(rev_id, dst, kind)`.
    """
    if not prefix:
        return "", "\uffff"
    return prefix, prefix[:-1] + chr(ord(prefix[-1]) + 1)


def files_index(graph, rev: str) -> dict[str, FileInfo]:
    """Файлы ревизии: путь, язык, кодировка, хэш, число строк.

    Колонка `encoding` появилась вместе с определением кодировок (P1-1), и на
    уже построенных корпусах её может не быть: догоняющая миграция выполняется
    при ЗАПИСИ (индексация), а операции чтения открывают базу только для
    чтения. Поэтому наличие колонки проверяется, а её отсутствие даёт честное
    значение по умолчанию `utf-8` — то же, чем читались файлы до этой правки.
    """
    cols = {str(r[1]) for r in graph.execute("PRAGMA table_info(file)").fetchall()}
    enc_expr = "COALESCE(encoding, 'utf-8')" if "encoding" in cols else "'utf-8'"
    rows = graph.execute(
        f"SELECT path, lang, lines, sha256, {enc_expr} AS encoding FROM file WHERE rev_id = ?",
        (rev,),
    ).fetchall()
    out: dict[str, FileInfo] = {}
    for r in rows:
        out[str(r["path"])] = FileInfo(
            path=str(r["path"]),
            lang=str(r["lang"] or ""),
            encoding=str(r["encoding"] or "utf-8"),
            sha256=str(r["sha256"] or ""),
            lines=int(r["lines"] or 0),
        )
    return out


def _node_paths(graph, rev: str) -> dict[str, str]:
    """node_id → путь файла, к которому узел относится.

    Нужно, чтобы ребро от функции к файлу прочиталось как связь ФАЙЛОВ:
    точка входа в проекте — это часто не файл целиком, а функция в нём.
    """
    rows = graph.execute(
        "SELECT node_id, path FROM node WHERE rev_id = ? AND path IS NOT NULL",
        (rev,),
    ).fetchall()
    return {str(r["node_id"]): str(r["path"]) for r in rows}


def graph_refs(graph, rev: str, files: dict[str, FileInfo],
               targets: set[str] | None = None) -> tuple[dict[str, list[Ref]], dict[str, list[Ref]]]:
    """Ссылки «файл → файл» из графа: include/require и литеральные пути.

    Ребро `@file:<путь>` ставит PHP-извлекатель на include/require, а
    разметка и скрипты добавляют такие же рёбра на пути, упомянутые строкой
    (`BWO_FILES_ENDPOINT = "/php/Wizard/Files7.php"`). Цель в ребре записана
    так, как стоит в КОДЕ, и потому её надо привести к пути корпуса: сначала
    как есть, потом относительно каталога источника, потом по уникальному
    базовому имени. Неоднозначное имя НЕ разрешается — как и в наследовании,
    выдуманная связь здесь опаснее отсутствующей.

    Параметр `targets` сужает работу до указанных файлов. Это не мелочь:
    на BWO рёбер `@file:` 142 тысячи, и разрешение всех их занимает около
    полутора секунд — при том что для вопроса о копиях нужны ссылки лишь на
    несколько найденных файлов. Отбор идёт запросом с образцами имён
    (`LIKE '%/Files7.php'`), а полный разбор остаётся путём по умолчанию.
    """
    by_name: dict[str, list[str]] = {}
    for p in files:
        by_name.setdefault(posixpath.basename(p).lower(), []).append(p)

    def resolve(src_path: str, raw: str) -> str | None:
        cand = _norm(raw.strip().strip("'\""))
        if not cand:
            return None
        if cand.startswith("//"):
            return None
        if cand.startswith("/"):
            cand = cand[1:]
        variants = [cand, posixpath.normpath(posixpath.join(posixpath.dirname(src_path), cand))]
        for v in variants:
            if v in files:
                return v
        base = posixpath.basename(cand).lower()
        hits = by_name.get(base, [])
        return hits[0] if len(hits) == 1 else None

    lo, hi = _prefix_range("@file:")
    sql = ("SELECT src, dst, kind, evidence_path, evidence_line FROM edge"
           " WHERE rev_id = ? AND dst >= ? AND dst < ?")
    params: list = [rev, lo, hi]
    if targets and len(targets) <= 80:
        patterns: list[str] = []
        for t in sorted(targets):
            patterns.append(f"%{posixpath.basename(t)}")
            patterns.append(t)
        chunk = 60
        rows: list = []
        for start in range(0, len(patterns), chunk):
            part = patterns[start:start + chunk]
            sub = sql + " AND (" + " OR ".join("dst LIKE ?" for _ in part) + ")"
            rows.extend(graph.execute(sub, tuple(params + part)).fetchall())
    else:
        rows = graph.execute(sql, tuple(params)).fetchall()

    # Таблица узлов читается ТОЛЬКО если адреса ребра не хватило: её полный
    # обход (на BWO — сотни тысяч строк) не должен оплачиваться на каждом вызове.
    node_path: dict[str, str] | None = None
    refs: dict[str, list[Ref]] = {}
    by_source: dict[str, list[Ref]] = {}
    for r in rows:
        # Файл-источник берётся из `evidence_path` ребра: это адрес места, где
        # найдена ссылка, и он уже лежит в строке ребра. Обращение к таблице
        # узлов тут не нужно и дорого: на BWO это 470 тысяч строк на каждый
        # вызов. `src` остаётся запасным путём — он указывает на файл-владельца
        # ребра, если адрес по какой-то причине не записан.
        src_path = _norm(str(r["evidence_path"] or ""))
        if src_path not in files:
            if node_path is None:
                node_path = _node_paths(graph, rev)
            src_path = node_path.get(str(r["src"])) or ""
        if not src_path or src_path not in files:
            continue
        target = resolve(src_path, str(r["dst"])[len("@file:"):])
        if not target or target == src_path:
            continue
        ref = Ref(path=src_path, line=int(r["evidence_line"] or 0), kind="include",
                  target=target, text=str(r["kind"]))
        refs.setdefault(target, []).append(ref)
        by_source.setdefault(src_path, []).append(ref)
    return refs, by_source


def superglobal_sources(graph, rev: str, files: dict[str, FileInfo]) -> dict[str, list[int]]:
    """Файлы, читающие суперглобальные переменные: это HTTP-точки входа.

    Признак доказан ребром `reads_superglobal`, которое ставит PHP-извлекатель
    на конкретной строке. `$_GET`/`$_POST`/`$_SERVER['REQUEST_METHOD']` в файле
    означают, что файл обслуживает запрос, — то есть является точкой входа,
    даже если его никто не подключает.

    Файл берётся из `evidence_path` самого ребра, а не через таблицу узлов:
    поиск node_id → path требует чтения ВСЕХ узлов ревизии (на BWO это 470
    тысяч строк и около секунды), тогда как ребро уже несёт адрес места, где
    обращение найдено. Отбор идёт по префиксу `dst` — это диапазонный поиск
    по индексу, а не проход по миллиону рёбер.
    """
    out: dict[str, list[int]] = {}
    lo, hi = _prefix_range("@ext:superglobal:")
    try:
        rows = graph.execute(
            "SELECT evidence_path, evidence_line FROM edge"
            " WHERE rev_id = ? AND dst >= ? AND dst < ?",
            (rev, lo, hi),
        ).fetchall()
    except Exception:
        return out
    for r in rows:
        p = _norm(str(r["evidence_path"] or ""))
        if p in files:
            out.setdefault(p, []).append(int(r["evidence_line"] or 0))
    return out


def routes(graph, rev: str, targets: set[str] | None = None) -> list[dict]:
    """Маршруты из web_route (заполняется извлекателями при индексации).

    Параметр `targets` сужает выборку образцами имён: на BWO реестр маршрутов
    содержит 141 тысячу записей, и разбор всех их на каждый вопрос стоил бы
    секунд. Образец `%Files7.php` отбирает только то, что относится к делу.
    """
    params: list = [rev]
    sql = ("SELECT route, method, node_id, evidence_path, evidence_line"
           " FROM web_route WHERE rev_id = ?")
    if targets and len(targets) <= 80:
        patterns = [f"%{posixpath.basename(t)}" for t in sorted(targets)]
        chunk = 60
        rows: list = []
        try:
            for start in range(0, len(patterns), chunk):
                part = patterns[start:start + chunk]
                sub = sql + " AND (" + " OR ".join("route LIKE ?" for _ in part) + ")"
                rows.extend(graph.execute(sub, tuple(params + part)).fetchall())
        except Exception:
            return []
        return [dict(r) for r in rows]
    try:
        rows = graph.execute(sql, tuple(params)).fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


def route_endpoints(graph, rev: str, files: dict[str, FileInfo],
                    targets: set[str] | None = None) -> dict[str, list[Ref]]:
    """Точки входа из реестра `web_route` (заполняется извлекателями, P1-3).

    Различаются два случая, и путать их нельзя:
      * маршрут ЗАРЕГИСТРИРОВАН в коде (`Route::get('/x')`, метод непустой) —
        точкой входа является файл, где он объявлен;
      * файл лишь УПОМЯНУТ как адрес (`action="upload.php"`, `fetch('...')`,
        `<!--#include virtual-->`) — точкой входа является тот файл, на
        который ссылаются, а не шаблон, который на него ссылается.
    """
    node_path: dict[str, str] | None = None
    by_name: dict[str, list[str]] = {}
    for p in files:
        by_name.setdefault(posixpath.basename(p), []).append(p)
    def resolve(raw: str, src: str) -> str | None:
        cand = _norm(raw.split("?")[0].split("&")[0])
        if not cand or "://" in cand or cand.startswith("//"):
            return None
        if cand.startswith("/"):
            cand = cand[1:]
        if cand in files:
            return cand
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(src), cand))
        if joined in files:
            return joined
        hits = by_name.get(posixpath.basename(cand), [])
        return hits[0] if len(hits) == 1 else None

    out: dict[str, list[Ref]] = {}
    for r in routes(graph, rev, targets=targets):
        # Файл-источник — из адреса записи реестра; таблица узлов не читается
        # (её полный обход стоит секунды на крупном корпусе).
        src = _norm(str(r.get("evidence_path") or ""))
        if src not in files:
            if node_path is None:
                node_path = _node_paths(graph, rev)
            src = node_path.get(str(r.get("node_id") or "")) or ""
        route = str(r.get("route") or "")
        method = str(r.get("method") or "")
        line = int(r.get("evidence_line") or 0)
        if method and src in files:
            out.setdefault(src, []).append(
                Ref(path=src, line=line, kind="route", target=route, text=method)
            )
            continue
        target = resolve(route, src) if src else None
        if target:
            out.setdefault(target, []).append(
                Ref(path=src, line=line, kind="endpoint", target=target, text=route)
            )
    return out


@dataclass
class TextChunk:
    path: str
    start_line: int
    end_line: int
    text: str


def chunks_of(conn, rev: str, paths: list[str] | None = None,
              langs: list[str] | None = None, max_start_line: int | None = None) -> list[TextChunk]:
    """Текст чанков ревизии (по путям, языкам и/или началу).

    Берётся ИЗ ИНДЕКСА, а не с диска: во-первых, это тот же текст, по
    которому ищет `search` (ответы обязаны совпадать), во-вторых, диск
    может быть уже изменён, и «мёртвый код» вычислялся бы по будущему
    состоянию проекта.

    Фильтры `langs`/`max_start_line` не украшение: полный проход по тексту
    всего корпуса BWO — это 102 тысячи чанков, и без сужения до разметки и
    скриптов разбор точек входа стоил бы секунд на каждый вызов.
    """
    sql = (
        "SELECT t.path, t.text, COALESCE(m.start_line, 1) AS start_line,"
        " COALESCE(m.end_line, 1) AS end_line"
        " FROM chunk_text t LEFT JOIN chunk_map m"
        " ON m.rev_id = t.rev_id AND m.chunk_id = t.chunk_id"
        " WHERE t.rev_id = ?"
    )
    params: list = [rev]
    if paths:
        placeholders = ",".join("?" for _ in paths)
        sql += f" AND t.path IN ({placeholders})"
        params.extend(paths)
    if langs:
        placeholders = ",".join("?" for _ in langs)
        sql += f" AND m.lang IN ({placeholders})"
        params.extend(langs)
    if max_start_line is not None:
        sql += " AND COALESCE(m.start_line, 1) <= ?"
        params.append(max_start_line)
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [
        TextChunk(path=str(r["path"]), start_line=int(r["start_line"]),
                  end_line=int(r["end_line"]), text=str(r["text"] or ""))
        for r in rows
    ]


#: Языки, в которых ссылка на файл встречается в разметке или скрипте.
#: Для остальных (markdown, yaml, sql) упоминание пути — это текст, а не
#: подключение, и считать его точкой входа нельзя.
ENDPOINT_LANGS = ["html", "php", "javascript", "typescript", "css"]


def literal_refs(conn, graph, rev: str, paths: list[str], *,
                 limit_per_target: int = 20, fts_candidates: int = 200) -> dict[str, list[Ref]]:
    """Ссылки на файлы по литеральному упоминанию пути в тексте других файлов.

    Это главный источник для вопроса «какой из копий рабочий»: в шаблоне
    `templates/Files.html` стоит `BWO_FILES_ENDPOINT = 'php/Wizard/Files7.php'`,
    и никакого ребра графа между ними нет — есть строка. Раньше такие связи
    находились только чтением JS вручную.

    Как ищется. Сначала дешёвый предварительный отбор по FTS (токены имени
    файла, связка И), затем ПРОВЕРКА точной подстрокой по сохранённому тексту
    чанка. Проверка обязательна: FTS хранит токены, а не текст, и его
    совпадение само по себе подстроку не доказывает. Если предварительный
    отбор не дал ничего, выполняется прямой запрос LIKE — но только для тех
    целей, где FTS промолчал: измерено на корпусе BWO (102 тысячи чанков),
    что LIKE на файл стоит 0,66 с, а фразовый запрос FTS — 8 мс, и делать
    LIKE по каждой из 25 копий значило бы 25 секунд на ответ (приёмка P1-1 —
    2 секунды).
    """
    files = files_index(graph, rev)
    # Базовые имена нужны, когда путь упомянут без каталогов (`Files7.php`).
    by_name: dict[str, list[str]] = {}
    for p in files:
        by_name.setdefault(posixpath.basename(p), []).append(p)

    out: dict[str, list[Ref]] = {}
    for target in paths:
        needles = {target}
        base = posixpath.basename(target)
        if len(by_name.get(base, [])) == 1:
            needles.add(base)
        hits: list[Ref] = []
        for needle in sorted(needles):
            if len(needle) < 4:
                continue
            hits.extend(
                _refs_via_fts(conn, rev, needle, target, limit_per_target, fts_candidates)
            )
        if not hits:
            # Запасной путь: FTS токенизирует текст и может не дать совпадения
            # там, где подстрока есть (например, имя с цифрой или дефисом).
            for needle in sorted(needles):
                if len(needle) < 4:
                    continue
                hits.extend(_refs_via_like(conn, rev, target, needle, limit_per_target))
        if hits:
            out[target] = _dedupe_refs(hits)
    return out


def _fts_tokens(needle: str) -> list[str]:
    """Токены имени для предварительного отбора — тем же токенизатором, что индекс."""
    from .lexvec import tokenize_for_fts

    seen: set[str] = set()
    out: list[str] = []
    for tok in tokenize_for_fts(needle).split():
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out[:12]


def _refs_via_fts(conn, rev: str, needle: str, target: str,
                  limit: int, candidates: int) -> list[Ref]:
    """Кандидаты по FTS, проверенные точной подстрокой."""
    tokens = _fts_tokens(needle)
    if not tokens:
        return []
    query = " ".join(tokens)
    try:
        rows = conn.execute(
            "SELECT f.chunk_id, f.path, COALESCE(m.start_line, 1) AS start_line"
            " FROM ft_chunk f LEFT JOIN chunk_map m"
            " ON m.rev_id = f.rev_id AND m.chunk_id = f.chunk_id"
            " WHERE f.rev_id = ? AND ft_chunk MATCH ? LIMIT ?",
            (rev, query, candidates),
        ).fetchall()
    except Exception:
        return []
    if not rows:
        return []
    meta = {
        str(r["chunk_id"]): (str(r["path"] or ""), int(r["start_line"] or 1))
        for r in rows
    }
    texts = _chunk_texts(conn, rev, list(meta))
    hits: list[Ref] = []
    for chunk_id, text in texts.items():
        path, start = meta[chunk_id]
        if path == target:
            continue
        pos = text.find(needle)
        if pos < 0:
            continue
        hits.append(
            Ref(path=path, line=util.text_line(text, pos, start), kind="literal",
                target=target, text=_line_around(text, pos))
        )
        if len(hits) >= limit:
            break
    return hits


def _refs_via_like(conn, rev: str, target: str, needle: str, limit: int) -> list[Ref]:
    """Прямой проход LIKE по тексту чанков (запасной путь, дорогой)."""
    try:
        rows = conn.execute(
            "SELECT t.path, t.text, COALESCE(m.start_line, 1) AS start_line"
            " FROM chunk_text t LEFT JOIN chunk_map m"
            " ON m.rev_id = t.rev_id AND m.chunk_id = t.chunk_id"
            " WHERE t.rev_id = ? AND t.path <> ? AND t.text LIKE ? LIMIT ?",
            (rev, target, f"%{needle}%", limit),
        ).fetchall()
    except Exception:
        return []
    hits: list[Ref] = []
    for r in rows:
        text = str(r["text"] or "")
        pos = text.find(needle)
        if pos < 0:
            continue
        hits.append(
            Ref(path=str(r["path"]),
                line=util.text_line(text, pos, int(r["start_line"])),
                kind="literal", target=target, text=_line_around(text, pos))
        )
    return hits


def _chunk_texts(conn, rev: str, chunk_ids: list[str], batch: int = 400) -> dict[str, str]:
    """Тексты чанков по идентификаторам — пакетами."""
    out: dict[str, str] = {}
    uniq = list(dict.fromkeys(str(c) for c in chunk_ids if c))
    for start in range(0, len(uniq), batch):
        part = uniq[start:start + batch]
        marks = ",".join("?" for _ in part)
        try:
            rows = conn.execute(
                f"SELECT chunk_id, text FROM chunk_text"
                f" WHERE rev_id = ? AND chunk_id IN ({marks})",
                (rev, *part),
            ).fetchall()
        except Exception:
            return out
        for r in rows:
            out[str(r["chunk_id"])] = str(r["text"] or "")
    return out


def _line_around(text: str, pos: int, width: int = 120) -> str:
    """Строка текста вокруг позиции совпадения (доказательство, а не весь чанк)."""
    if pos < 0:
        return ""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end < 0:
        end = len(text)
    line = text[start:end].strip()
    return line[:width]


# ---------------------------------------------------------------------------
# Разбор SQL по тексту: «код ↔ база» (db_cross)
# ---------------------------------------------------------------------------

#: Полные SQL-конструкции, в которых имя таблицы стоит в ЛИТЕРАЛЕ.
#: Каждая ветвь — законченная конструкция, а не отдельное слово: поиск
#: `from имя` без контекста дал 73 ложные находки на 86 таблиц (в «таблицы»
#: попадали `__future__` из строк документации Python и `ack` — внутренняя
#: таблица самого RAGRAF). Замер записан в ловушке 31.
#:
#: ЭТИМ ВЫРАЖЕНИЕМ ИМЕНА БОЛЬШЕ НЕ ИЩУТСЯ. Замер 14.10.2026 на BWO: прогон
#: `SELECT … [^;]{0,400}? FROM` по 17 944 чанкам занимал 34 с — крупные чанки
#: (файл целиком) с сотнями `SELECT` давали квадратичный перебор, и операция
#: «перед тем как писать SQL» становилась непригодной. Имена ищет посимвольный
#: сканер `sql_table_names`, а это выражение остаётся ЭТАЛОНОМ СМЫСЛА: по нему
#: проверяется, какие конструкции считаются полными (см. `tests/test_db_cross.py`).
SQL_TABLE_LITERAL_RE = re.compile(
    r"(?is)(?:"
    r"\bSELECT\b[^;]{0,400}?\bFROM\s+[`\"\[]?([A-Za-z_][A-Za-z0-9_]{1,60})"
    r"|\bINSERT\s+INTO\s+[`\"\[]?([A-Za-z_][A-Za-z0-9_]{1,60})"
    r"|\bDELETE\s+FROM\s+[`\"\[]?([A-Za-z_][A-Za-z0-9_]{1,60})"
    r"|\bUPDATE\s+[`\"\[]?([A-Za-z_][A-Za-z0-9_]{1,60})[^;]{0,200}?\bSET\b"
    r"|\bJOIN\s+[`\"\[]?([A-Za-z_][A-Za-z0-9_]{1,60})"
    r")")

#: Слова, которые стоят на месте имени таблицы, но таблицами не являются.
#: Список пополнен по замеру на BWO: без него в «таблицы» попадали `insert`,
#: `delete`, `update` (из `INSERT INTO`, `DELETE FROM`, `ON DUPLICATE KEY UPDATE`),
#: а также английские `all`, `any`, `the`, `and` из прозы в строке запроса.
SQL_RESERVED = ("select", "where", "values", "set", "as", "on", "dual",
                "table", "lateral", "unnest", "generate_series",
                "from", "into", "join", "update", "delete", "insert",
                "if", "not", "exists", "only", "all", "any", "some",
                "the", "and", "or")

#: Ключевые слова, после которых может стоять имя таблицы.
_SQL_KEYWORDS = ("from", "insert", "into", "update", "delete", "join")
_SQL_NAME_AT_RE = re.compile(r"([a-z_][a-z0-9_]{1,60})")
_SQL_SKIP_CHARS = " \t\r\n`\"'[]"

#: Место имени таблицы, где стоит НЕ литерал: `FROM $t`, `FROM {$t}`,
#: `FROM " . $t . "`, `FROM ?`. Это честно названная слепая зона статического
#: разбора: имя таблицы собирается во время выполнения, и ни ребро, ни
#: текстовый образец его не знают. Агенту важно видеть ЭТИ места, а не считать,
#: что «раз имени нет, значит таблица не используется».
SQL_DYNAMIC_RE = re.compile(
    r"(?is)\b(?:FROM|INTO|JOIN|UPDATE)\s+"
    r"(?:\$[A-Za-z_]\w*"                    # PHP/Perl: FROM $table
    r"|\{\s*\$?[A-Za-z_]\w*\s*\}"           # PHP: FROM {$table}
    r"|[:@?]"                               # FROM :name / @name / ?
    r"|[\"']\s*\.\s*\$?[A-Za-z_]"           # "SELECT … FROM " . $t
    r"|\.\s*[\"']?\s*\$[A-Za-z_]\w*"        # $t . " WHERE …"
    r")")


#: Служебный заголовок чанка: `function indexed_hashes (src/ragraf/watch.py:151)`.
#: Ловушка 20: строка 0 чанка — заголовок, а код начинается со строки 1 и
#: соответствует `chunk_map.start_line`. Заголовок несёт и путь, и первую
#: строку, поэтому читатель текста может назвать `path:line` без обращения
#: к графу.
CHUNK_HEADER_RE = re.compile(r"\((?P<path>[^()\n]{1,240}):(?P<line>\d+)\)\s*$")


def chunk_origin(text: str) -> tuple[str, int]:
    """Путь и первая строка кода по служебному заголовку чанка.

    Возврат `("", 1)`, если заголовок разобрать не удалось: это не ошибка,
    а честное «адрес неизвестен» — такие чанки в доказательства не попадают.
    """
    if not text:
        return "", 1
    first = text.split("\n", 1)[0]
    match = CHUNK_HEADER_RE.search(first)
    if not match:
        return "", 1
    path = match.group("path").strip()
    try:
        line = int(match.group("line"))
    except ValueError:
        line = 1
    return path, max(1, line)


def _text_line(text: str, pos: int, start_line: int) -> int:
    """Номер строки файла по позиции в тексте чанка.

    Делегирует общему правилу `util.text_line`: у чанка-символа есть служебный
    заголовок, у чанка-файла его нет, и считать это в двух местах по-разному
    нельзя — два ответа об одном коде назвали бы разные строки.
    """
    return util.text_line(text, pos, start_line)


def _read_sql_name(low: str, original: str, pos: int) -> tuple[str, int]:
    """Имя таблицы после позиции `pos`: НАПИСАНИЕ — из исходного текста.

    Сканер идёт по тексту в нижнем регистре (так дешевле сравнения), но имя
    возвращается в ТОМ НАПИСАНИИ, как оно записано в коде: `StringData`, а не
    `stringdata`. Иначе ответ терял бы регистр, а вместе с ним и подсказку о
    расхождении регистра с базой (`case_mismatch` — отдельная находка).

    Пропускается обрамление (`"`, `'`, `` ` ``, `[`): пишут и `FROM "Users"`,
    и ``FROM `users` ``, и `FROM [Users]`. Пустое имя означает, что имени здесь
    нет — стоит выражение (`FROM (SELECT …)`) или параметр (`FROM ?`);
    выдумывать имя в этом случае нельзя, такие места называет
    `dynamic_sql_sites`.
    """
    i = pos
    while i < len(low) and low[i] in _SQL_SKIP_CHARS:
        i += 1
    match = _SQL_NAME_AT_RE.match(low, i)
    if not match:
        return "", i
    return original[i:i + (match.end() - match.start())], i


def sql_table_names(text: str, start_line: int = 1, *,
                    lang: str = "") -> list[dict[str, Any]]:
    """Имена таблиц из литералов SQL — с номером строки и текстом конструкции.

    Три правила отбора, каждое из которых выросло из замера (14.10.2026,
    корпус BWO: 168 имён, среди них `al`, `all`, `and`, `access`):

      1. **Конструкция, а не слово.** `FROM` засчитывается только после
         `SELECT` в пределах 400 символов; `INTO` — после `INSERT`;
         `UPDATE имя` — только если дальше есть `SET`; `DELETE` — только с
         `FROM`. Иначе в «таблицы» попадали `__future__` и `ack` (ловушка 31).
      2. **Строка, а не комментарий.** Строка, начинающаяся с `//`, `*`, `#`,
         `--`, отбрасывается: `// On create update all the other records` давала
         имя `all`.
      3. **Литерал, а не проза.** В строке обязана быть кавычка (`'`, `"`, `` ` ``),
         либо язык — SQL, либо выше в том же чанке открыт блочный литерал
         (`<<<SQL`, `<<'SQL'`). Без этого имя бралось из английского текста
         (`* update the existing rows` → `and`).
      4. **Служебные слова.** `INSERT INTO`, `DELETE FROM`, `ON DUPLICATE KEY
         UPDATE` ставят на место имени слово: они отброшены `SQL_RESERVED`.

    Пустой список означает «имён таблиц-литералов нет», а не ошибку.
    """
    if not text:
        return []
    low = text.lower()
    # Дешёвая отсечка до разбора: поиск ключевых слов подстрокой (уровень C) на
    # порядок быстрее обхода по словам — замер на BWO: `str.find` 0,64 с против
    # `re.finditer` 7,56 с на тех же 273 млн символов. Второй отсечкой идёт
    # проверка «есть ли вообще чем заняться».
    positions: list[int] = []
    for word in _SQL_KEYWORDS:
        start = 0
        while True:
            pos = low.find(word, start)
            if pos < 0:
                break
            positions.append(pos)
            start = pos + len(word)
    if not positions:
        return []
    positions.sort()
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    heredoc = "<<" in text and lang.lower() in ("php", "perl", "ruby", "sql")
    for start in positions:
        # Слово должно быть ОТДЕЛЬНЫМ и целиком совпасть: `fromage` и `inserted`
        # ключевыми словами не являются.
        word = ""
        for candidate in _SQL_KEYWORDS:
            if not low.startswith(candidate, start):
                continue
            left_ok = start == 0 or not low[start - 1].isalnum()
            after_try = start + len(candidate)
            right_ok = after_try >= len(low) or not low[after_try].isalnum()
            if left_ok and right_ok:
                word = candidate
                break
        if not word or word in ("select", "into"):
            continue
        after = start + len(word)
        if word == "from":
            # `FROM` без `SELECT` перед ним — это либо `DELETE FROM`
            # (обрабатывается ветвью `delete`), либо предлог в тексте.
            if "select" not in low[max(0, start - 400):start]:
                continue
        if word == "update" and "set" not in low[after:after + 200]:
            continue
        if word == "delete":
            tail = low[after:after + 12]
            from_pos = tail.find("from")
            if from_pos < 0:
                continue
            name, _pos = _read_sql_name(low, text, after + from_pos + 4)
        elif word == "insert":
            # Имя стоит после `INTO`, а не сразу за `INSERT`: без пропуска
            # служебного слова в «таблицы» попадало `into` (замер на BWO:
            # 3701 упоминание — самое частое ложное имя).
            tail = low[after:after + 12]
            into_pos = tail.find("into")
            if into_pos < 0:
                continue
            name, _pos = _read_sql_name(low, text, after + into_pos + 4)
        else:
            name, _pos = _read_sql_name(low, text, after)
        if not name or name in SQL_RESERVED or name.startswith("_"):
            continue
        line = _text_line(text, start, start_line)
        key = (name, line)
        if key in seen:
            continue
        shown = _line_around(text, start, width=160)
        stripped = shown.lstrip()
        if stripped.startswith(("//", "#", "*", "--", "/*")):
            continue
        if lang.lower() != "sql" and not any(q in shown for q in ("'", '"', "`")):
            if not heredoc:
                continue
        seen.add(key)
        out.append({"name": name, "line": line, "sql": shown})
    return out


def dynamic_sql_sites(text: str, start_line: int = 1, *,
                      lang: str = "") -> list[dict[str, Any]]:
    """Места, где имя таблицы НЕ литерал, а значение времени выполнения.

    Нужны, чтобы слепая зона была названа: `FROM $table` не даёт ни имени для
    сверки с базой, ни ребра в графе. Агент, увидев такое место, знает, где
    статический разбор бессилен, и не делает вывода «таблица не используется».

    Литеральные конструкции (`FROM users`) сюда НЕ попадают: они дают имя, а не
    пробел. Строки-комментарии отбрасываются тем же правилом, что и в
    `sql_table_names`.
    """
    if not text:
        return []
    # Дешёвая отсечка: если в тексте нет ни переменной, ни фигурной скобки, ни
    # параметра, то и «имя таблицы во время выполнения» взяться неоткуда.
    # Замер: без неё разбор слепых зон на BWO занимал 15 с.
    if not any(ch in text for ch in ("$", "{", "?", ":")):
        return []
    out: list[dict[str, Any]] = []
    for match in SQL_DYNAMIC_RE.finditer(text):
        shown = _line_around(text, match.start(), width=160)
        if shown.lstrip().startswith(("//", "#", "*", "--", "/*")):
            continue
        out.append({
            "line": _text_line(text, match.start(), start_line),
            "kind": match.group(0).split()[0].upper(),
            "fragment": shown,
        })
    return out



def _dedupe_refs(refs: list[Ref]) -> list[Ref]:
    seen: set[tuple[str, int, str]] = set()
    out: list[Ref] = []
    for r in refs:
        key = (r.path, r.line, r.kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    out.sort(key=lambda r: (r.path, r.line, r.kind))
    return out


def detect_entrypoints(graph, conn, rev: str, files: dict[str, FileInfo],
                       refs: dict[str, list[Ref]], *, text_scan: bool = True,
                       root=None, targets: set[str] | None = None) -> dict[str, list[Ref]]:
    """Точки входа ревизии: роут, HTTP-обработчик, CLI-скрипт, endpoint в разметке.

    Виды разделены, потому что у них разная сила доказательства:
      * `route`   — маршрут зарегистрирован в коде;
      * `http`    — файл читает `$_GET/$_POST/$_SERVER` (доказано ребром);
      * `endpoint`— путь файла упомянут в `action=`/`src=`/`href=`/`fetch(...)`
                    другого файла (это ссылка РАЗМЕТКИ, а не вызов кода);
      * `cli`     — исполняемый скрипт: shebang или каталог `cgi-bin/`, `bin/`.

    Файл может быть точкой входа сразу по нескольким основаниям — все они
    сохраняются: по ним и решается, «подключён ли» файл.

    Параметр `text_scan=False` выключает САМУЮ ДОРОГУЮ часть — проход по
    разметке и скриптам. Это нужно операции `search_multi`: ей активность
    нужна для нескольких файлов, и платить за полный проход по корпусу
    ради них нельзя (приёмка — ответ за 2 секунды). Признаки роутов,
    суперглобальных переменных и CLI при этом остаются: они берутся
    из графа и из запросов, а не из чтения текста.
    """
    out: dict[str, list[Ref]] = {}
    for path, lines in superglobal_sources(graph, rev, files).items():
        if targets is not None and path not in targets:
            continue
        for ln in lines:
            out.setdefault(path, []).append(
                Ref(path=path, line=ln, kind="http", target="", text="reads superglobal")
            )
    for path, items in route_endpoints(graph, rev, files, targets=targets).items():
        out.setdefault(path, []).extend(items)

    # Реестр точек входа, заполненный при индексации (P1-3), делает проход по
    # тексту ненужным: те же образцы (`action=`, `src=`, `fetch(...)`) уже
    # разобраны извлекателями и лежат в `web_route`. Проход остаётся
    # ЗАПАСНЫМ путём для корпусов, построенных до этой правки: там реестр пуст,
    # и без него точек входа не было бы вовсе.
    if text_scan and not routes(graph, rev):
        # Разметка и скрипты: ищем в тексте ссылки на существующие файлы корпуса.
        # Индекс базовых имён строится ОДИН раз: без него разбор каждой ссылки
        # перебирал бы все файлы корпуса (на BWO это 4600 × число ссылок).
        by_name: dict[str, list[str]] = {}
        for p in files:
            by_name.setdefault(posixpath.basename(p), []).append(p)

        def resolve(raw: str, src_path: str) -> str | None:
            cand = _norm(raw.split("?")[0].split("#")[0])
            if not cand or "://" in raw or cand.startswith("//"):
                return None
            if cand.startswith("/"):
                cand = cand[1:]
            if cand in files:
                return cand
            joined = posixpath.normpath(posixpath.join(posixpath.dirname(src_path), cand))
            if joined in files:
                return joined
            hits = by_name.get(posixpath.basename(cand), [])
            return hits[0] if len(hits) == 1 else None

        for chunk in chunks_of(conn, rev, langs=ENDPOINT_LANGS):
            for rx in (ENDPOINT_ATTR_RE, ENDPOINT_CALL_RE):
                for m in rx.finditer(chunk.text):
                    target = resolve(m.group(1), chunk.path)
                    if not target or target == chunk.path:
                        continue
                    line = chunk.start_line + chunk.text.count("\n", 0, m.start())
                    out.setdefault(target, []).append(
                        Ref(path=chunk.path, line=line, kind="endpoint", target=target,
                            text=m.group(0)[:120])
                    )

    # CLI: shebang в начале файла или точка входа Python (`if __name__ == ...`).
    # Признак берётся ИЗ ФАЙЛА (два первых байта) для языков со shebang и из
    # индекса — для `__main__`. Почему не из чанков: запрос «чанки, у которых
    # начало ≤ 2 строки» читает таблицу текста целиком (на BWO это 100 тысяч
    # чанков и около секунды), а двух байт достаточно.
    #
    # Правило «любой файл из bin/, tools/, scripts/ — точка входа» здесь
    # СОЗНАТЕЛЬНО не применяется: замер на собственном корпусе дал по нему
    # 313 «точек входа» — то есть все разовые скрипты каталога `tools/`,
    # многие из которых давно отработали. Точка входа — это признак файла
    # (shebang, запуск модуля), а не место, куда его положили.
    mains = _main_module_paths(
        conn, rev, sorted(targets) if targets is not None else None
    )
    for path, info in files.items():
        if targets is not None and path not in targets:
            continue
        if not path.lower().endswith(CLI_EXT):
            continue
        if root:
            shebang = _file_starts_with_shebang(root, path)
        else:
            # Корня нет (вызов из проверки на извлечении): смотрим начало
            # чанков — единственный доступный источник.
            heads = _head_texts(conn, rev, [path])
            shebang = any(t.lstrip().startswith("#!") for t in heads)
        if shebang:
            out.setdefault(path, []).append(
                Ref(path=path, line=1, kind="cli", target="", text="shebang")
            )
        elif path in mains:
            out.setdefault(path, []).append(
                Ref(path=path, line=1, kind="cli", target="",
                    text="точка входа модуля: __main__")
            )
    for path, items in out.items():
        out[path] = _dedupe_refs(items)
    return out


def _head_texts(conn, rev: str, paths: list[str]) -> list[str]:
    """Текст чанков, начинающихся с первых двух строк указанных файлов."""
    if not paths:
        return []
    marks = ",".join("?" for _ in paths)
    try:
        rows = conn.execute(
            "SELECT t.text FROM chunk_text t JOIN chunk_map m"
            " ON m.rev_id = t.rev_id AND m.chunk_id = t.chunk_id"
            f" WHERE t.rev_id = ? AND COALESCE(m.start_line, 1) <= 2"
            f" AND t.path IN ({marks})",
            (rev, *paths),
        ).fetchall()
    except Exception:
        return []
    return [str(r["text"] or "") for r in rows]


def _file_starts_with_shebang(root, path: str) -> bool:
    """Начинается ли файл с `#!`. Читаются ДВА байта, а не файл.

    Нужно потому, что чанк с первой строкой существует не всегда: у Perl
    первый символ начинается с `sub`, и строки 1 в индексе может не быть
    вовсе. Признак «исполняемый скрипт» при этом обязан работать.
    """
    if not root:
        return False
    try:
        from pathlib import Path as _Path

        with open(_Path(root) / path, "rb") as fh:
            return fh.read(2) == b"#!"
    except OSError:
        return False


def _main_module_paths(conn, rev: str, paths: list[str] | None = None) -> set[str]:
    """Файлы с признаком запуска как программы (`if __name__ == "__main__"`).

    Нужно для языков без shebang: у консольной утилиты на Python признак
    запуска — этот блок, и без него `tools/ragraf.py` не считался бы точкой
    входа, а весь корпус — недостижимым.

    При переданных `paths` запрос ограничен этими файлами: полный проход по
    тексту корпуса (на BWO — 100 тысяч чанков) нужен только тогда, когда
    спрашивают обо всех файлах сразу.
    """
    try:
        if paths:
            marks = ",".join("?" for _ in paths)
            rows = conn.execute(
                "SELECT DISTINCT path FROM chunk_text"
                f" WHERE rev_id = ? AND text LIKE '%__main__%' AND path IN ({marks})",
                (rev, *paths),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT path FROM chunk_text"
                " WHERE rev_id = ? AND text LIKE '%__main__%'",
                (rev,),
            ).fetchall()
    except Exception:
        return set()
    return {str(r["path"]) for r in rows}


def _resolve_target(raw: str, src_path: str, files: dict[str, FileInfo]) -> str | None:
    """Привести ссылку разметки к пути корпуса (или None, если это не файл корпуса)."""
    cand = _norm(raw.split("?")[0].split("#")[0])
    if not cand or "://" in raw:
        return None
    if cand.startswith("/"):
        cand = cand[1:]
    if cand in files:
        return cand
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(src_path), cand))
    if joined in files:
        return joined
    base = posixpath.basename(cand)
    hits = [p for p in files if posixpath.basename(p) == base]
    return hits[0] if len(hits) == 1 else None


def reachable_files(files: dict[str, FileInfo], refs: dict[str, list[Ref]],
                    entrypoints: dict[str, list[Ref]], by_source: dict[str, list[Ref]] | None = None) -> set[str]:
    """Обход «от точек входа по ссылкам»: какие файлы вообще используются.

    Направление обхода — по ссылкам вперёд: точка входа подключает файл,
    тот подключает следующий. Файл без пути от точки входа — кандидат в
    мёртвый код, но окончательный приговор выносит `dead_code`: он ещё
    проверяет наличие входящих ссылок.
    """
    edges: dict[str, set[str]] = {}
    for target, items in refs.items():
        for r in items:
            edges.setdefault(r.path, set()).add(target)
    if by_source:
        for src, items in by_source.items():
            for r in items:
                edges.setdefault(src, set()).add(r.target)
    seen: set[str] = set()
    expanded: set[str] = set()
    queue = [p for p in sorted(entrypoints) if p in files]
    # Точка входа сама в «достижимые» НЕ попадает: вопрос достижимости — «на
    # файл можно прийти от точки входа», а не «он и есть точка входа». Иначе
    # любой скрипт с shebang оказывался достижимым сам собой, и `dead_code`
    # не мог назвать его неподключённым (измерено на собственном корпусе:
    # 313 «точек входа» давали 313 достижимых файлов и ноль кандидатов).
    while queue:
        cur = queue.pop()
        if cur in expanded:
            continue
        expanded.add(cur)
        for nxt in sorted(edges.get(cur, ())):
            if nxt in files and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def exact_groups(files: dict[str, FileInfo]) -> dict[str, list[str]]:
    """Группы байт-идентичных файлов (по sha256)."""
    by_hash: dict[str, list[str]] = {}
    for path, info in files.items():
        if info.sha256:
            by_hash.setdefault(info.sha256, []).append(path)
    return {h: sorted(v) for h, v in by_hash.items() if len(v) > 1}


def family_groups(files: dict[str, FileInfo]) -> dict[str, list[str]]:
    """Группы файлов одного имени с разными номерами (`Files.php`…`Files7.php`)."""
    by_family: dict[str, list[str]] = {}
    for path in files:
        by_family.setdefault(family_key(path), []).append(path)
    return {k: sorted(v) for k, v in by_family.items() if len(v) > 1}


def pick_primary(members: list[str], knowledge: Knowledge) -> str:
    """Главный файл семейства — детерминированно.

    Порядок предпочтений: ЕСТЬ входящие ссылки → точка входа → достижимый из
    точки входа → больше входящих ссылок → путь по возрастанию.

    Первым стоит наличие ссылок, а не признак точки входа, и это не
    перестановка «для красоты»: в BWO все восемь копий `Files*.php` читают
    `$_GET`, то есть все восемь формально точки входа, и выбор по этому
    признаку дал бы главным первый по алфавиту файл вместо того, на который
    ссылается шаблон. Последний критерий (путь) обязателен: без него выбор
    зависел бы от порядка обхода каталога, а недетерминированный «главный
    файл» ломает воспроизводимость ответа (НФТ-5) — это уже было причиной
    дрожащей контрольной суммы корпуса.
    """
    def rank(path: str) -> tuple:
        incoming = knowledge.incoming(path)
        return (
            0 if incoming else 1,
            0 if knowledge.is_entrypoint(path) else 1,
            0 if path in knowledge.reachable else 1,
            -len(incoming),
            path,
        )

    return min(members, key=rank)


def file_text(conn, rev: str, path: str) -> str:
    """Полный текст файла из индекса (склейка чанков по строкам)."""
    chunks = chunks_of(conn, rev, [path])
    if not chunks:
        return ""
    if len(chunks) == 1:
        return chunks[0].text
    chunks.sort(key=lambda c: c.start_line)
    return "\n".join(c.text for c in chunks)


def structure_notes(files: dict[str, FileInfo], refs: dict[str, list[Ref]]) -> dict:
    """Сводка по корпусу: сколько файлов, семейств, без входящих ссылок."""
    return {
        "files": len(files),
        "with_incoming_refs": sum(1 for p in files if refs.get(p)),
        "families": len(family_groups(files)),
    }


def sha_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _in_scope(path: str, scope: str | None) -> bool:
    if not scope:
        return True
    s = _norm(scope).rstrip("/")
    return path == s or path.startswith(s + "/")


def knowledge(graph, conn, rev: str, *, with_entrypoints: bool = True, root=None,
              targets: set[str] | None = None) -> Knowledge:
    """Собрать знание о ревизии для операций P1-2.

    Порядок шагов неслучаен: файлы → ссылки из графа → точки входа →
    достижимость. Точки входа — самая дорогая часть (проход по тексту
    разметки и скриптов), поэтому она включается параметром: `duplicates`
    обходится без неё, а `dead_code` — нет.
    """
    kn = Knowledge(files=files_index(graph, rev))
    refs, by_source = graph_refs(graph, rev, kn.files, targets=targets)
    kn.refs, kn.by_source = refs, by_source
    if with_entrypoints:
        kn.entrypoints = detect_entrypoints(graph, conn, rev, kn.files, refs, root=root)
        kn.reachable = reachable_files(kn.files, refs, kn.entrypoints, by_source)
        kn.notes.append(
            "достижимость считается от точек входа по рёбрам include и ссылкам разметки"
        )
    return kn


def duplicates_report(graph, conn, rev: str, scope: str | None = None,
                      min_similarity: float = 0.5, limit: int = 100, root=None) -> dict:
    """Группы копий: байт-идентичные и «одно имя с номерами».

    В ответе на каждую группу:
      * `kind` — `identical` (совпал sha256) или `family` (одно имя, разные номера);
      * `primary` — главный файл: точка входа → достижимый → больше ссылок → путь;
      * `members` — все файлы группы с ролью (`primary`/`copy`), similarity к
        главному и числом различающихся строк;
      * `evidence` — ЧЕМ доказана активность главного: ссылки графа и
        литеральные упоминания в разметке/скриптах (`path:line` + строка).
        Без этого «рабочий файл» был бы мнением, а не фактом.
    """
    kn = knowledge(graph, conn, rev, with_entrypoints=False, root=root)
    files = {p: i for p, i in kn.files.items() if _in_scope(p, scope)}
    exact = exact_groups(files)
    families = family_groups(files)

    groups: list[dict] = []
    # Сначала байт-идентичные: это самый сильный признак копии.
    for digest, members in sorted(exact.items()):
        groups.append(
            _group_payload(graph, conn, rev, kn, members, kind="identical", similarity=1.0,
                           digest=digest)
        )
    # Затем семейства: те же имена с разными номерами (`Files.php` … `Files7.php`).
    exact_sets = {tuple(v) for v in exact.values()}
    for key, members in sorted(families.items()):
        if tuple(members) in exact_sets:
            continue
        groups.append(_group_payload(graph, conn, rev, kn, members, kind="family", similarity=None,
                                     digest="", family=key))
    # Сходство считается по тексту ИЗ ИНДЕКСА: диск мог уйти вперёд.
    texts = {m: file_text(conn, rev, m) for m in
             (m for g in groups for m in g["members"] if not g.get("similarity_table"))}
    for g in groups:
        if "similarity_table" in g:
            continue
        primary = g["primary"]
        ptext = texts.get(primary, "")
        sims = {}
        for m in g["members"]:
            if m == primary:
                continue
            sims[m] = round(_similarity(ptext, texts.get(m, "")), 3)
        g["similarity"] = sims
        for item in g["member_details"]:
            item["similarity_to_primary"] = sims.get(item["path"])
            item["lines_only_here"] = _line_delta(texts.get(item["path"], ""), ptext)
            item["lines_only_in_primary"] = _line_delta(ptext, texts.get(item["path"], ""))
        # Группа «одно имя, но совсем разный текст» — это не копии, а версии
        # разных файлов; называть её копиями нельзя.
        if g["kind"] == "family":
            best = max(sims.values()) if sims else 0.0
            g["kind"] = "family" if best >= min_similarity else "same_name"
            g["note"] = (
                "структурно похожи" if g["kind"] == "family"
                else "одно имя с номерами, но текст различается: это не копии"
            )
    groups.sort(key=lambda g: (g["kind"] != "identical", g["primary"]))
    truncated = max(0, len(groups) - limit)
    return {
        "scope": scope or "весь корпус",
        "groups": groups[:limit],
        "groups_total": len(groups),
        "truncated": truncated,
        "files_checked": len(files),
    }


def _line_delta(a: str, b: str) -> int:
    """Сколько строк есть в `a` и нет в `b` (без учёта порядка)."""
    sa = {ln.strip() for ln in a.splitlines() if ln.strip()}
    sb = {ln.strip() for ln in b.splitlines() if ln.strip()}
    return len(sa - sb)


def _group_payload(graph, conn, rev: str, kn: Knowledge, members: list[str], *,
                   kind: str, similarity: float | None, digest: str,
                   family: str = "") -> dict:
    """Описание одной группы копий, включая доказательства активности главного."""
    primary = pick_primary(members, kn)
    details = []
    for m in members:
        incoming = kn.incoming(m)
        details.append(
            {
                "path": m,
                "role": "primary" if m == primary else "copy",
                "incoming_refs": len(incoming),
                "is_entrypoint": kn.is_entrypoint(m),
                "sha256": (kn.files.get(m).sha256[:12] if kn.files.get(m) else ""),
                "lines": (kn.files.get(m).lines if kn.files.get(m) else 0),
            }
        )
    evidence: dict[str, list[dict]] = {}
    for m in members:
        refs = list(kn.incoming(m))
        # Литеральные упоминания ищем только для главного: это запрос LIKE на
        # каждый файл, и делать его для всех копий — платить за вывод, который
        # уже доказан активностью главного.
        if m == primary:
            refs.extend(literal_refs(conn, graph, rev, [m]).get(m, []))
        if refs:
            evidence[m] = [
                {"path": r.path, "line": r.line, "kind": r.kind, "text": r.text}
                for r in refs[:10]
            ]
    out = {
        "kind": kind,
        "primary": primary,
        "members": members,
        "member_details": details,
        "evidence": evidence,
        "sha256_12": digest[:12],
    }
    if family:
        out["family"] = family
    if similarity is not None:
        out["similarity"] = similarity
        out["similarity_table"] = True
    return out


def dead_code_report(graph, conn, rev: str, scope: str | None = None, limit: int = 200,
                     check_limit: int = 300, root=None) -> dict:
    """Файлы и подпрограммы без входящих ссылок и вне достижимости.

    Приговор выносится по ДВУМ независимым проверкам, и обе обязаны совпасть:

      1. в графе нет входящих рёбер (include, вызов, обращение к таблице) —
         файл никто не подключает и не вызывает;
      2. в тексте других файлов нет литерального упоминания пути — на файл
         не ссылаются ни разметка, ни скрипт (`src=`, `fetch(...)`,
         `BWO_FILES_ENDPOINT = '...'`).

    Отдельно проверяется достижимость от точек входа. Файл, у которого нет
    входящих ссылок, но который сам является точкой входа (`cgi-bin/import.pl`
    с shebang), мёртвым НЕ объявляется: это как раз рабочий сценарий запуска.

    Ограничение `check_limit` названо в ответе полем `checked`: если файлов
    больше, часть остаётся непроверенной, и ответ говорит об этом, а не
    выдаёт «проверено всё».
    """
    kn = knowledge(graph, conn, rev, with_entrypoints=True, root=root)
    scoped = [p for p in sorted(kn.files) if _in_scope(p, scope)]

    def _cli_only(path: str) -> bool:
        """Точка входа ровно одним способом: исполняемый скрипт (shebang/__main__).

        Такой файл запускают руками или планировщиком, а не подключают из
        кода, поэтому отсутствие входящих ссылок само по себе не приговор —
        но и не оправдание. Он попадает в проверку, а причина называется
        честно: «исполняемый скрипт без единой ссылки».
        """
        kinds = {r.kind for r in kn.entrypoints.get(path, [])}
        return kinds == {"cli"}

    candidates = [
        p for p in scoped
        if not kn.incoming(p)
        and p not in kn.reachable
        and (not kn.entrypoints.get(p) or _cli_only(p))
    ]
    checked = candidates[:check_limit]
    remaining = len(candidates) - len(checked)

    refuted: dict[str, list[Ref]] = {}
    for p in checked:
        hits = literal_refs(conn, graph, rev, [p]).get(p, [])
        if hits:
            refuted[p] = hits

    dead_files = []
    for p in checked:
        if p in refuted:
            continue
        info = kn.files.get(p)
        kinds = sorted({r.kind for r in kn.entrypoints.get(p, [])})
        if kinds:
            reason = (
                "исполняемый скрипт ("
                + ", ".join(kinds)
                + "), но ни одной ссылки на него нет: возможно, разовый или мёртвый"
            )
        else:
            reason = (
                "нет входящих ссылок ни в графе, ни в разметке; "
                "из точек входа не достижим"
            )
        dead_files.append(
            {
                "path": p,
                "lang": info.lang if info else "",
                "lines": info.lines if info else 0,
                "entrypoint_kinds": kinds,
                "reason": reason,
                "checked": [
                    "рёбра графа с целью этого файла",
                    "литеральные упоминания пути в тексте других файлов",
                    "достижимость от точек входа",
                ],
            }
        )

    dead_symbols = _dead_symbols(graph, rev, kn, set(scoped), limit=limit)
    return {
        "scope": scope or "весь корпус",
        "files": dead_files[:limit],
        "files_candidates": len(candidates),
        "files_checked": len(checked),
        "files_not_checked": remaining,
        "refuted": [
            {"path": p, "referenced_by": [
                {"path": r.path, "line": r.line, "kind": r.kind, "text": r.text}
                for r in refs[:5]
            ]}
            for p, refs in sorted(refuted.items())
        ][:limit],
        "symbols": dead_symbols,
        "entrypoints": len(kn.entrypoints),
        "reachable_files": len(kn.reachable),
        "notes": kn.notes,
    }


def _dead_symbols(graph, rev: str, kn: Knowledge, scoped: set[str], limit: int = 100) -> list[dict]:
    """Подпрограммы без входящих вызовов.

    Проверяется только вызываемость по имени: подпрограмма, которую вызывает
    внешняя система (точка входа платформы, hook, обработчик события),
    входящих рёбер не имеет и мёртвой НЕ является. Поэтому имена-обработчики
    и файлы-точки входа исключаются сразу, а в ответе прямо сказано, что
    проверка статическая.
    """
    rows = graph.execute(
        "SELECT node_id, kind, name, path, start_line, fqn FROM node"
        " WHERE rev_id = ? AND kind IN ('function', 'method')",
        (rev,),
    ).fetchall()
    called: dict[str, int] = {}
    edge_rows = graph.execute(
        "SELECT dst, COUNT(*) AS n FROM edge WHERE rev_id = ?"
        " AND kind IN ('calls', 'calls_heuristic', 'calls_builtin') GROUP BY dst",
        (rev,),
    ).fetchall()
    for r in edge_rows:
        called[str(r["dst"])] = int(r["n"])

    out = []
    for r in rows:
        node_id = str(r["node_id"])
        path = str(r["path"] or "")
        if path not in scoped or called.get(node_id):
            continue
        name = str(r["name"] or "")
        if kn.entrypoints.get(path):
            continue
        out.append(
            {
                "path": path,
                "line": int(r["start_line"] or 0),
                "name": name,
                "kind": str(r["kind"]),
                "fqn": str(r["fqn"] or ""),
                "reason": "нет входящих вызовов в графе; проверка статическая",
            }
        )
        if len(out) >= limit:
            break
    return out

