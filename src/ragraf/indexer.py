"""Конвейер индексации (архитектура §9).

Стадии: scan → ast → load → resolve → lexvec → seal.
M1/M2: scan, ast, load, resolve, lexvec, seal. M3 добавит стадию CPG (Joern).

Каждая стадия изолирована: её сбой не отменяет уже собранные факты, но обязательно
попадает в stage_timing, coverage и rev.capability (ФТ-11, ФТ-12).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from . import util
from .config import ProjectConfig
from .corpus import CorpusScan
from .embeddings import Embedder, EmbeddingUnavailable, truncate_for_embedding
from .extract.css_html import MultiExtractor
from .extract.php_ast import ChunkRec, PhpExtractor, build_chunks
from .lexvec import LexVecStore
from .pipeline import StageContext
from .revisions import ControlStore, GraphWriter


@dataclass
class IndexReport:
    rev_id: str
    ok: bool
    capability: str
    seconds: float
    stages: list[dict] = field(default_factory=list)
    files: int = 0
    nodes: int = 0
    edges: int = 0
    chunks: int = 0
    vectors: int = 0
    vectors_reused: int = 0
    vectors_created: int = 0
    embedding_available: bool = False
    embedding_note: str | None = None
    notes: list[str] = field(default_factory=list)
    counts_by_kind: dict[str, int] = field(default_factory=dict)
    counts_by_edge: dict[str, int] = field(default_factory=dict)
    parse_status: dict[str, int] = field(default_factory=dict)
    resolved_by_name: int = 0

    def to_dict(self) -> dict:
        return {
            "rev_id": self.rev_id,
            "ok": self.ok,
            "capability": self.capability,
            "seconds": self.seconds,
            "stages": self.stages,
            "files": self.files,
            "nodes": self.nodes,
            "edges": self.edges,
            "chunks": self.chunks,
            "vectors": self.vectors,
            "vectors_reused": self.vectors_reused,
            "vectors_created": self.vectors_created,
            "embedding_available": self.embedding_available,
            "embedding_note": self.embedding_note,
            "notes": self.notes,
            "counts_by_kind": self.counts_by_kind,
            "counts_by_edge": self.counts_by_edge,
            "parse_status": self.parse_status,
            "resolved_by_name": self.resolved_by_name,
        }


class Indexer:
    def __init__(self, cfg: ProjectConfig) -> None:
        self.cfg = cfg
        self.control = ControlStore(cfg)
        self.graph = GraphWriter(cfg)
        self.lexvec = LexVecStore(cfg)
        # Единый извлекатель по расширению: PHP, CSS, разметка. Нужен,
        # чтобы индекс не терял слои оформления и разметки (см. extract/css_html.py).
        self.extractor = MultiExtractor()
        self.php_extractor = self.extractor.php
        self.embedder = Embedder(cfg, allow_download=True)  # индексация может получить модель
        self.report = IndexReport(rev_id="", ok=False, capability="degraded", seconds=0.0)
        self._stages: list[dict] = []

    # ------------------------------------------------------------------
    # Полный прогон
    # ------------------------------------------------------------------

    def run(self, *, with_vectors: bool = True, limit_files: int | None = None) -> IndexReport:
        t_start = time.perf_counter()
        cfg = self.cfg
        cfg.ensure_dirs()
        # --vectors явно разрешает получение модели; без флага — только из кэша.
        self.embedder.allow_download = bool(with_vectors)

        rev_id = self.control.next_rev_id()
        parent_rev = self.control.current_rev()
        self.report.rev_id = rev_id
        ctx = StageContext(cfg=cfg, rev_id=rev_id, parent_rev=parent_rev)

        # --- стадия 1: скан ---
        t0 = time.perf_counter()
        try:
            scan = self._scan(limit_files)
        except Exception as exc:
            self._abort(rev_id, f"скан корпуса не удался: {type(exc).__name__}: {exc}", t_start)
            return self.report
        ctx.scan = scan
        self._stage(rev_id, "scan", round(time.perf_counter() - t0, 3), len(scan.files))

        ctx.note_versions()
        capability = "degraded"
        joern_used = False
        self.graph.begin_rev(
            rev_id, parent_rev, scan.corpus_hash, cfg.config_hash,
            ctx.extractor_versions, capability, joern_used,
        )
        self.control.register_rev(
            rev_id, parent_rev, scan.corpus_hash, cfg.config_hash,
            capability, joern_used, cfg.project,
        )

        # --- стадия 2: AST и символы ---
        t0 = time.perf_counter()
        built = self._build_facts(rev_id, scan)
        self._stage(rev_id, "ast", round(time.perf_counter() - t0, 3), len(scan.files))
        if built["ast_notes"]:
            self.report.notes.append(
                f"файлов с неполным разбором: {len(built['ast_notes'])} "
                f"(первые: {', '.join(built['ast_notes'][:3])})"
            )

        # --- стадия 3: загрузка ---
        t0 = time.perf_counter()
        self.graph.add_files(built["file_rows"])
        self.graph.add_nodes(built["node_rows"])
        self.graph.add_edges(built["edge_rows"])
        self.graph.add_text_chunks(built["text_rows"])
        self.graph.commit()
        self._stage(
            rev_id, "load", round(time.perf_counter() - t0, 3),
            len(built["file_rows"]) + len(built["node_rows"]) + len(built["edge_rows"])
            + len(built["text_rows"]),
        )

        # --- стадия 4: разрешение внешних имён ---
        t0 = time.perf_counter()
        resolved_cnt, ambiguous_cnt = self._resolve_external_names(rev_id)
        # Рёбра наследования разрешаются отдельно и ДО общих счётчиков не
        # подмешиваются: у них своя природа (класс, а не метод) и своя
        # достоверность. Замер: на myproject все 29 рёбер `extends` вели на
        # заглушки, то есть наследование в графе было невидимым.
        inherit_resolved, inherit_ambiguous = self._resolve_inheritance_names(rev_id)
        self._stage(rev_id, "resolve", round(time.perf_counter() - t0, 3), resolved_cnt)
        if inherit_resolved or inherit_ambiguous:
            self.report.notes.append(
                f"наследование: привязано к классам {inherit_resolved} рёбер, "
                f"оставлено внешними из-за нескольких кандидатов {inherit_ambiguous}"
            )
        if ambiguous_cnt:
            self.report.notes.append(
                f"не разрешено из-за неоднозначности имени метода: {ambiguous_cnt} рёбер "
                f"(оставлены как внешние ссылки, а не как выдуманные связи)"
            )

        # --- стадия 4б: внешние контракты (схема БД из миграций) ---
        # Замер на полигоне показал, что без этой стадии слой схемы пуст и операции
        # schema/table_usage бесполезны, хотя конфигурация обещает источник схемы.
        t0 = time.perf_counter()
        from . import schema_db

        schema_info = schema_db.write_schema(cfg, rev_id, self.graph)
        self._stage(rev_id, "schema", round(time.perf_counter() - t0, 3),
                    schema_info["tables"], schema_info["note"])
        self.report.notes.append(
            f"схема БД: таблиц {schema_info['tables']}, колонок {schema_info['columns']}, "
            f"связей {schema_info['fks']} (источник: {schema_info['files']} файлов миграций)"
        )

        # --- стадия 5: CPG (межпроцедурные связи и потоки данных) ---
        t0 = time.perf_counter()
        cpg_info = self._build_cpg(rev_id)

        # --- стадия 5б: перенос графа свойств кода из предыдущей ревизии ---
        # Зачем. В быстром режиме построение CPG пропускается ради скорости
        # (16 с против 21 при полном прогоне). Но при этом терялись уже
        # ПОСТРОЕННЫЕ связи: замерено, что после быстрой переиндексации число
        # рёбер от Joern падало с 693 до 0, а рёбер вида `calls` — с 3 386
        # до 2 693. То есть 16 секунд работы пропадали, а операция `slice`
        # перестала работать, хотя код не менялся.
        #
        # Что делается: рёбра с источником joern-php2cpg переносятся из самой
        # свежей ревизии, где они есть. Переносятся только те, у которых ОБА
        # конца есть в текущей ревизии: если файл удалён или функция переименована,
        # ребро не переносится, и связь честно исчезает. Подмена узла из старой
        # ревизии не делается — иначе в графе появились бы связи на несуществующие
        # объекты.
        if not cpg_info.get("ok"):
            carried = self._carry_cpg_edges(rev_id)
            if carried:
                cpg_info["carried_from_prev"] = carried
                self.report.notes.append(
                    f"граф свойств кода перенесён из предыдущей ревизии: "
                    f"рёбер {carried} (построение пропущено ради скорости)"
                )
            else:
                self.report.notes.append(
                    "граф свойств кода не построен и переносить нечего: "
                    "операция slice недоступна"
                )

        self._stage(
            rev_id, "cpg", round(time.perf_counter() - t0, 3),
            cpg_info.get("calls_added", 0), cpg_info.get("note"),
        )
        if cpg_info["ok"]:
            # ВНИМАНИЕ: значение берётся ИЗ РЕЗУЛЬТАТА стадии, а не выставляется
            # жёстко в `full`. Стадия умеет вернуть `partial` — случай, когда
            # обходной путь дал записи, но после фильтрации не осталось ни
            # одной, и подтверждённые вызовы в графе есть только от разбора.
            # Жёсткое `full` здесь затирало бы это значение, и клиент получал
            # бы «операции среза доступны» там, где их нет: `slice` проверяет
            # именно `capability == "full"` и падал бы уже на своей стороне.
            cap = str(cpg_info.get("capability") or "full")
            capability = cap
            joern_used = cap == "full"
            self.report.notes.append(cpg_info["note"])
            if cap != "full":
                self.report.notes.append(
                    f"режим определён как {cap}: операции, требующие полных "
                    "свойств кода, недоступны"
                )
        elif cpg_info.get("carried_from_prev"):
            # Связи перенесены из предыдущей ревизии — режим полноценный.
            # Признак capability означает НАЛИЧИЕ межпроцедурных связей в графе,
            # а не факт запуска Joern. Раньше он зависел от факта запуска,
            # и после быстрой переиндексации режим понижался до degraded,
            # хотя все 693 связи были на месте: операция slice отказывалась
            # работать без причины. Найдено tests/test_cpg_carry.py.
            capability = "full"
            joern_used = True
            self.report.notes.append(
                f"граф свойств кода перенесён: рёбер "
                f"{cpg_info['carried_from_prev']}, режим полный"
            )
        else:
            self.report.notes.append(f"CPG не подключена: {cpg_info['note']}")
            if cpg_info.get("stderr_tail"):
                self.report.notes.append(f"вывод Joern: {cpg_info['stderr_tail'][-300:]}")
            # Если связей в графе нет вообще — только тогда режим неполный.
            try:
                have_cpg = self.graph.conn.execute(
                    "SELECT COUNT(*) AS c FROM edge WHERE rev_id = ? AND sources LIKE ?",
                    (rev_id, "%joern%"),
                ).fetchone()["c"]
            except Exception:
                have_cpg = 0
            if have_cpg:
                capability = "full"
                joern_used = True

        # --- стадия 6: лексика и вектор ---
        t0 = time.perf_counter()
        lex = self._build_lexvec(rev_id, built["chunks"], with_vectors)
        self._stage(rev_id, "lexvec", round(time.perf_counter() - t0, 3), len(built["chunks"]))
        if lex["note"]:
            self.report.notes.append(f"векторный слой: {lex['note']}")

        # --- покрытие и запечатывание ---
        self._write_coverage(rev_id, scan, built, resolved_cnt, ambiguous_cnt, lex, cpg_info)
        # В журнал пишется ЧИСЛО УНИКАЛЬНЫХ УЗЛОВ, а не число строк.
        #
        # Дефект учёта, найденный замером (tools/measure_node_dupes2.py):
        # раньше здесь стояло len(built["node_rows"]) — длина списка строк.
        # Но ключ таблицы node — (rev_id, node_id), и строки с одним node_id
        # перезаписывают друг друга. На корпусе z это давало 2227 в журнале
        # против 1290 фактически — расхождение 42%, из-за которого показалось,
        # будто индекс теряет узлы.
        #
        # Проверено раздельно: по узлам КОДА (функции, методы, классы)
        # схлопываний НОЛЬ. Все 920 схлопываний — это классы CSS (1441 строка
        # → 521 узел), и так задумано: имя класса — общий ключ без пути,
        # иначе определение в стилях и использование в разметке не сопоставить.
        #
        # Уникальность берётся из таблицы, а не из длины списка строк.
        #
        # Ключ таблицы node — (rev_id, node_id), и строки с одним node_id
        # перезаписывают друг друга. На корпусе z длина списка давала 2227
        # против 1290 фактических — расхождение 42%, из-за которого показалось,
        # будто индекс теряет узлы.
        #
        # Проверено раздельно (tools/measure_node_dupes2.py): по узлам КОДА
        # схлопываний НОЛЬ. Все 920 схлопываний — это классы CSS (1441 строка
        # → 521 узел), и так задумано: имя класса — общий ключ без пути, иначе
        # определение в стилях и использование в разметке не сопоставить.
        unique_nodes = self.graph.count_nodes(rev_id)
        # Число рёбер тоже берётся из таблицы, а не из длины списка.
        #
        # Второй дефект учёта: между подсчётом edge_rows и этой строкой
        # работают стадии, которые ДОБАВЛЯЮТ рёбра — разрешение внешних имён
        # (_resolve_external_names) и перенос графа свойств кода
        # (_carry_cpg_edges). На z журнал показывал 12499, а таблица
        # содержала 12956.
        actual_edges = self.graph.count_edges(rev_id)
        self.graph.finish_rev(
            rev_id, len(built["file_rows"]), unique_nodes,
            actual_edges, capability, joern_used,
        )
        self.control.seal_rev(
            rev_id, len(built["file_rows"]), unique_nodes,
            actual_edges, capability, joern_used,
        )
        rotated = self.control.rotate(cfg.index.retain_revs)
        if rotated:
            self.report.notes.append(
                f"ротация: данные ревизий {', '.join(rotated)} удалены из хранилищ "
                f"(история сохранена)"
            )

        self.report.ok = True
        self.report.capability = capability
        self.report.seconds = round(time.perf_counter() - t_start, 3)
        self.report.stages = list(self._stages)
        self.report.files = len(built["file_rows"])
        self.report.nodes = len(built["node_rows"])
        self.report.edges = len(built["edge_rows"])
        self.report.chunks = len(built["chunks"])
        self.report.vectors = lex["created"] + lex["reused"]
        self.report.vectors_reused = lex["reused"]
        self.report.vectors_created = lex["created"]
        self.report.embedding_available = lex["note"] is None
        self.report.embedding_note = lex["note"]
        self.report.counts_by_kind = built["kind_counts"]
        self.report.counts_by_edge = built["edge_counts"]
        self.report.parse_status = built["parse_status"]
        self.report.resolved_by_name = resolved_cnt
        return self.report

    # ------------------------------------------------------------------
    # Стадии
    # ------------------------------------------------------------------

    def _scan(self, limit_files: int | None) -> CorpusScan:
        from . import corpus

        result = corpus.scan(self.cfg)
        if limit_files is not None:
            result.files = result.files[:limit_files]
        return result

    def _build_facts(self, rev_id: str, scan: CorpusScan) -> dict:
        cfg = self.cfg
        file_rows: list[tuple] = []
        node_rows: list[tuple] = []
        edge_rows: list[tuple] = []
        text_rows: list[tuple] = []
        chunks: list[ChunkRec] = []
        kind_counts: dict[str, int] = {}
        edge_counts: dict[str, int] = {}
        parse_status: dict[str, int] = {}
        ast_notes: list[str] = []

        # Раньше здесь были два жёстких набора:
        #   PARSE_LANGS = {"php", "css", "html"}
        #   TEXT_LANGS  = {"markdown", "sql", "javascript"}
        # Всё остальное отбрасывалось со статусом "skipped". Это и была причина,
        # по которой .ps1, .sh, .bat, .cs, .pl, .py не попадали в индекс:
        # их языков просто не было ни в одном из наборов.
        #
        # Теперь решение принимается по факту: есть ли у языка извлекатель
        # с разбором дерева. Список языков с разбором живёт в одном месте —
        # в профилях extract/generic.py, поэтому расхождение между картой
        # расширений и индексатором больше невозможно.
        parse_langs = self._parse_languages()

        for cf in scan.files:
            try:
                data = cf.abs_path.read_bytes()
            except OSError as exc:
                file_rows.append(
                    (rev_id, cf.path, cf.lang, cf.lines, cf.sha256, "failed",
                     f"чтение: {exc}")
                )
                parse_status["failed"] = parse_status.get("failed", 0) + 1
                continue

            if cf.lang not in parse_langs:
                # Язык без разбора деревом: узлов и рёбер не даёт, но попадает
                # в поиск через текстовые чанки. Раньше такие файлы молча
                # отбрасывались, поэтому по документации, конфигам и скриптам
                # batch/SQL поиск ничего не находил.
                file_rows.append(
                    (rev_id, cf.path, cf.lang, cf.lines, cf.sha256, "text", None)
                )
                parse_status["text"] = parse_status.get("text", 0) + 1
                chunks.extend(self._text_chunks(cf.path, data, cfg, cf.lang))
                continue

            res = self.extractor.extract(cf.path, data)
            file_rows.append(
                (rev_id, cf.path, cf.lang, res.lines, res.sha256, res.parse_status, res.parse_note)
            )
            parse_status[res.parse_status] = parse_status.get(res.parse_status, 0) + 1

            for s in res.symbols:
                node_rows.append(
                    (
                        rev_id, s.node_id, s.kind, s.name, s.fqn, res.path,
                        s.start_line, s.end_line, s.signature, util.dumps(s.props), "structural",
                    )
                )
                kind_counts[s.kind] = kind_counts.get(s.kind, 0) + 1
            for e in res.edges:
                uid = util.stable_id(e.src, e.dst, e.kind, res.path, e.evidence_line)
                edge_rows.append(
                    (
                        rev_id, uid, e.src, e.dst, e.kind, e.provenance, res.path,
                        e.evidence_line, e.confidence, util.dumps(list(e.sources)),
                        # U4: свойства ребра обязательны к записи. Раньше признак
                        # вида доступа к таблице вычислялся и терялся здесь, из-за
                        # чего table_usage не мог отделить чтение от записи.
                        util.dumps(dict(getattr(e, "props", {}) or {})),
                    )
                )
                edge_counts[e.kind] = edge_counts.get(e.kind, 0) + 1
            for t in res.texts:
                text_rows.append(
                    (rev_id, t.chunk_id, t.owner_node_id, res.path, t.start_line, t.end_line,
                     t.text_kind, t.raw)
                )
            file_chunks = self.extractor.chunks(
                res, data,
                by_symbol=cfg.chunking.by_symbol,
                max_lines=cfg.chunking.max_chunk_lines,
                min_lines=cfg.chunking.min_chunk_lines,
            )
            # Извлекатель нового языка может вернуть пустой список чанков,
            # если структура не нашлась (например, файл без объявлений).
            # Тогда файл должен попасть в поиск текстовыми чанками — иначе
            # он окажется в индексе в виде одних рёбер без текста, и поиск
            # по содержимому его не найдёт.
            if not file_chunks:
                file_chunks = self._text_chunks(cf.path, data, cfg, cf.lang)
            chunks.extend(file_chunks)
            if res.parse_status != "ok":
                ast_notes.append(f"{res.path}: {res.parse_status}")

        return {
            "file_rows": file_rows,
            "node_rows": node_rows,
            "edge_rows": edge_rows,
            "text_rows": text_rows,
            "chunks": chunks,
            "kind_counts": kind_counts,
            "edge_counts": edge_counts,
            "parse_status": parse_status,
            "ast_notes": ast_notes,
        }

    def _carry_cpg_edges(self, rev_id: str) -> int:
        """Перенести рёбра графа свойств кода из свежей ревизии, где они есть.

        Почему перенос, а не пересборка. Построение графа свойств кода занимает
        около 16 с — это самая дорогая стадия. Ради экономии она пропускается
        при быстрой переиндексации. Но если связи просто не записать, то:
          * исчезают уже вычисленные 693 ребра (замер на полигоне);
          * число рёбер `calls` падает с 3 386 до 2 693 — теряются доказанные
            связи, а остаются только выведенные разбором дерева;
          * операция `slice` перестаёт работать, хотя код не менялся.
        Повторно строить граф — только чтобы вернуть то же — бессмысленно.

        Что переносится и что нет:
          * переносятся рёбра с источником `joern-php2cpg`;
          * ребро переносится ТОЛЬКО если оба его конца есть в текущей ревизии:
            при удалённом файле или переименованной функции связь честно исчезает;
          * идентификатор ребра сохраняется, поэтому повторный запуск
            не создаёт дубликатов (запись идёт через INSERT OR REPLACE).

        Возвращается число перенесённых рёбер.
        """
        try:
            # Ищется самая свежая ДРУГАЯ ревизия, в которой были рёбра от Joern.
            row = self.graph.conn.execute(
                """SELECT rev_id, COUNT(*) AS c FROM edge
                    WHERE rev_id <> ? AND sources LIKE '%joern%'
                    GROUP BY rev_id ORDER BY rev_id DESC LIMIT 1""",
                (rev_id,),
            ).fetchone()
            if not row or not row["c"]:
                return 0
            source_rev = str(row["rev_id"])

            # Узлы текущей ревизии: по ним проверяются концы рёбер.
            current_nodes = {
                str(r["node_id"])
                for r in self.graph.conn.execute(
                    "SELECT node_id FROM node WHERE rev_id = ?", (rev_id,)
                ).fetchall()
            }

            rows = self.graph.conn.execute(
                """SELECT edge_uid, src, dst, kind, provenance, evidence_path,
                          evidence_line, confidence, sources, props
                     FROM edge WHERE rev_id = ? AND sources LIKE '%joern%'""",
                (source_rev,),
            ).fetchall()

            new_rows: list[tuple] = []
            skipped = 0
            for r in rows:
                if str(r["src"]) not in current_nodes or str(r["dst"]) not in current_nodes:
                    # Один из концов исчез: связь переносить нельзя, иначе
                    # в графе появится ребро в никуда.
                    skipped += 1
                    continue
                new_rows.append(
                    (
                        rev_id, r["edge_uid"], r["src"], r["dst"], r["kind"],
                        r["provenance"], r["evidence_path"], r["evidence_line"],
                        r["confidence"], r["sources"], r["props"],
                    )
                )
            if new_rows:
                self.graph.add_edges(new_rows)
                self.graph.commit()
            if skipped:
                self.report.notes.append(
                    f"при переносе графа свойств кода пропущено рёбер {skipped}: "
                    f"один из концов отсутствует в новой ревизии"
                )
            return len(new_rows)
        except Exception as exc:
            self.report.notes.append(
                f"перенос графа свойств кода не выполнен: {type(exc).__name__}: {exc}"
            )
            return 0

    def _resolve_external_names(self, rev_id: str) -> tuple[int, int]:
        """Разрешение внешних имён (архитектура §6.2 — «динамический вызов»).

        `$obj->get()` без вывода типов даёт ребро на `@ext:method:get`. Такие рёбра
        привязываются к реальным одноимённым символам, но ВСЕГДА как derived с пониженной
        достоверностью: точный получатель неизвестен. Если имя слишком распространено,
        ребро остаётся неразрешённым — иначе агент получит шум вместо связей.

        Возвращает (разрешено, отброшено как неоднозначное).
        """
        MAX_CANDIDATES = 6
        NOISY_THRESHOLD = 12

        method_rows = self.graph.conn.execute(
            "SELECT edge_uid, src, dst, kind, evidence_path, evidence_line, props FROM edge"
            " WHERE rev_id = ? AND dst LIKE '@ext:method:%'",
            (rev_id,),
        ).fetchall()
        resolved = 0
        ambiguous = 0
        new_rows: list[tuple] = []
        # Заглушки, которые будут ЗАМЕНЕНЫ разрешёнными рёбрами: иначе в графе останутся
        # и внешняя ссылка, и разрешённая связь одновременно — задвоение факта.
        replaced_uids: list[str] = []

        if method_rows:
            by_name: dict[str, list] = {}
            for r in method_rows:
                nm = str(r["dst"]).split("@ext:method:", 1)[1]
                by_name.setdefault(nm, []).append(r)

            for nm, group in by_name.items():
                cands = self.graph.conn.execute(
                    "SELECT node_id FROM node WHERE rev_id = ? AND name = ?"
                    " AND kind IN ('method','property') LIMIT ?",
                    (rev_id, nm, NOISY_THRESHOLD + 1),
                ).fetchall()
                if not cands or len(cands) > NOISY_THRESHOLD or len(cands) > MAX_CANDIDATES:
                    ambiguous += len(group)
                    continue
                for er in group:
                    for cand in cands:
                        uid = util.stable_id(
                            er["src"], cand["node_id"], "calls_heuristic",
                            er["evidence_path"], er["evidence_line"],
                        )
                        # Свойства исходного ребра переносятся в новое.
                        try:
                            er_props = util.loads(er["props"]) if er["props"] else {}
                        except Exception:
                            er_props = {}
                        new_rows.append(
                            (
                                rev_id, uid, str(er["src"]), str(cand["node_id"]),
                                "calls_heuristic", "derived", er["evidence_path"],
                                er["evidence_line"], 0.4 if len(cands) > 1 else 0.6,
                                util.dumps(["tree-sitter-php", "name-resolver"]),
                                util.dumps(dict(er_props)),
                            )
                        )
                        resolved += 1
                    replaced_uids.append(str(er["edge_uid"]))

        # Символьные внешние ссылки: SomeClass::method и имена классов — связывание по fqn.
        const_rows = self.graph.conn.execute(
            "SELECT edge_uid, src, dst, kind, evidence_path, evidence_line, props FROM edge"
            " WHERE rev_id = ? AND dst LIKE '@ext:%' AND dst NOT LIKE '@ext:method:%'",
            (rev_id,),
        ).fetchall()
        for cr in const_rows:
            target = str(cr["dst"])[5:]
            if target.startswith(("superglobal:", "dynamic_func", "include_dynamic", "php:")):
                continue
            # Составное имя вида "App\Core\Config" для вызова App\Core\Config::get:
            # ищем класс И метод одновременно, иначе связь будет выдуманной.
            cls_part = None
            if "::" not in target and not target.startswith("method:"):
                cls_part = target
            hit = None
            if cls_part is None:
                hit = self.graph.conn.execute(
                    "SELECT node_id FROM node WHERE rev_id = ? AND fqn = ? LIMIT 1", (rev_id, target)
                ).fetchone()
                if hit is None:
                    hit = self.graph.conn.execute(
                        "SELECT node_id FROM node WHERE rev_id = ? AND fqn LIKE ?"
                        " ORDER BY LENGTH(fqn) LIMIT 1",
                        (rev_id, f"%{target}"),
                    ).fetchone()
            if hit is None:
                continue
            uid = util.stable_id(
                cr["src"], hit["node_id"], cr["kind"], cr["evidence_path"], cr["evidence_line"]
            )
            # Свойства ребра переносятся из прочитанной строки: без этого
            # признаки вроде вида доступа к таблице (U4) терялись бы при
            # переводе внешней ссылки в точную.
            try:
                base_props = util.loads(cr["props"]) if cr["props"] else {}
            except Exception:
                base_props = {}
            new_rows.append(
                (
                    rev_id, uid, str(cr["src"]), str(hit["node_id"]), str(cr["kind"]), "stated",
                    cr["evidence_path"], cr["evidence_line"],
                    1.0,
                    util.dumps(["tree-sitter-php", "fqn-resolver"]),
                    util.dumps(dict(base_props)),
                )
            )
            resolved += 1
            replaced_uids.append(str(cr["edge_uid"]))

        if new_rows:
            self.graph.add_edges(new_rows)
        if replaced_uids:
            unique = list(dict.fromkeys(replaced_uids))
            for start in range(0, len(unique), 500):
                part = unique[start : start + 500]
                marks = ",".join("?" for _ in part)
                self.graph.conn.execute(
                    f"DELETE FROM edge WHERE rev_id = ? AND edge_uid IN ({marks})",
                    (rev_id, *part),
                )
        if new_rows or replaced_uids:
            self.graph.commit()
        return resolved, ambiguous

    def _resolve_inheritance_names(self, rev_id: str) -> tuple[int, int]:
        """Разрешить рёбра наследования, ведущие на заглушки `@ext:…`.

        ЗАЧЕМ. Извлекатель наследования ставит ребро на заглушку
        (`@ext:<имя базового класса>`, см. `extract/php_ast.py`), потому что
        базовый класс может быть объявлен в другом файле или вообще вне корпуса.
        Замерено на myproject (PHP, rev-0034): все 29 рёбер `extends` указывали на
        заглушки, и **ни одно** не вело на настоящий узел класса. То есть факты
        о наследовании в графе были невидимы для операций: имя базы есть, цели нет.

        КАК разрешается. По последнему сегменту имени: в myproject классы записаны
        квалифицированно (`App\\Connectors\\AbstractConnector`), а извлекатель
        может дать и полное имя, и короткое (`AbstractConnector`). Сопоставление
        идёт по полю `name` узла класса, поэтому обе формы находят одну цель.

        ЗАЩИТА ОТ ШУМА — та же, что у разрешения вызовов, и по той же причине:
        имя базового класса короткое и распространённое (`Base`, `Handler`),
        поэтому при нескольких кандидатах ребро **остаётся неразрешённым**.
        Выдать один из кандидатов значило бы выдумать наследование — а по нему
        агент делает выводы о том, какие методы вообще существуют.

        Достоверность: `derived` и не выше 0.8. Связь наследования выведена из
        текста объявления, а не подтверждена разрешением типов: если класс с
        таким именем один, ошибка маловероятна, но это всё равно вывод.

        Возвращает (разрешено, отброшено как неоднозначное).
        """
        NOISY_THRESHOLD = 6

        rows = self.graph.conn.execute(
            "SELECT edge_uid, src, dst, kind, evidence_path, evidence_line, props FROM edge"
            " WHERE rev_id = ? AND kind IN ('extends', 'implements')"
            " AND dst LIKE '@ext:%'",
            (rev_id,),
        ).fetchall()
        if not rows:
            return 0, 0

        resolved = 0
        ambiguous = 0
        new_rows: list[tuple] = []
        replaced: list[str] = []

        for r in rows:
            # Доступ по ПОЗИЦИИ, а не по имени колонки: у обычного соединения
            # sqlite3 нет row_factory. Это уже четвёртый случай этой ошибки в
            # проекте (та же была в `load_graph` и в резолвере fqn), поэтому
            # правило одно: в новом коде строки читаются по индексу.
            raw = str(r[2])[5:]          # убрать @ext:   (колонка dst)
            edge_uid = str(r[0])
            src_id = str(r[1])
            kind = str(r[3])
            evidence_path = r[4]
            evidence_line = r[5]
            props_raw = r[6]
            # Имя класса — последний сегмент: `App\\Connectors\\Base` -> `Base`.
            short = raw.replace("\\", "/").rsplit("/", 1)[-1]
            short = short.rsplit("::", 1)[-1].strip()
            if not short:
                ambiguous += 1
                continue
            cands = self.graph.conn.execute(
                "SELECT node_id FROM node WHERE rev_id = ? AND name = ?"
                " AND kind IN ('class','interface','struct','enum') LIMIT ?",
                (rev_id, short, NOISY_THRESHOLD + 1),
            ).fetchall()
            if len(cands) != 1:
                # Ноль кандидатов — базовый класс вне корпуса, это нормально.
                # Больше одного — выбирать нельзя (см. докстроку).
                ambiguous += 1
                continue
            try:
                props = util.loads(props_raw) if props_raw else {}
            except Exception:
                props = {}
            props = dict(props)
            props["resolved_by"] = "inherit-name"
            uid = util.stable_id(
                src_id, str(cands[0][0]), kind, evidence_path, evidence_line,
            )
            new_rows.append(
                (
                    rev_id, uid, src_id, str(cands[0][0]),
                    kind, "derived", evidence_path,
                    evidence_line, 0.8,
                    util.dumps(["tree-sitter-php", "inherit-resolver"]),
                    util.dumps(props),
                )
            )
            resolved += 1
            replaced.append(edge_uid)

        if new_rows:
            self.graph.add_edges(new_rows)
        if replaced:
            unique = list(dict.fromkeys(replaced))
            for start in range(0, len(unique), 500):
                part = unique[start : start + 500]
                marks = ",".join("?" for _ in part)
                self.graph.conn.execute(
                    f"DELETE FROM edge WHERE rev_id = ? AND edge_uid IN ({marks})",
                    (rev_id, *part),
                )
        if new_rows or replaced:
            self.graph.commit()
        return resolved, ambiguous

    def _build_lexvec(self, rev_id: str, chunks: list[ChunkRec], with_vectors: bool) -> dict:
        """Лексика всегда; вектор — если доступны модель и расширение vec0."""
        note: str | None = None
        vectors: dict[str, list[float]] = {}
        reused = 0
        created = 0

        if with_vectors and chunks and self.lexvec.vec_ok:
            status = self.embedder.status()
            if not status.available:
                note = status.reason
            else:
                hashes = [c.text_hash for c in chunks]
                vectors = self.lexvec.reusable_vectors(self.cfg.embedding.model, hashes)
                reused = len(vectors)
                missing = [c for c in chunks if c.text_hash not in vectors]
                uniq: dict[str, str] = {}
                for c in missing:
                    uniq.setdefault(c.text_hash, truncate_for_embedding(c.text))
                if uniq:
                    keys = list(uniq.keys())
                    try:
                        new_vecs = self.embedder.embed([uniq[k] for k in keys])
                        for k, v in zip(keys, new_vecs):
                            vectors[k] = v
                        created = len(new_vecs)
                    except EmbeddingUnavailable as exc:
                        note = str(exc)
        elif with_vectors and not self.lexvec.vec_ok:
            note = "vec0 недоступен: sqlite-vec не загрузился"

        self.lexvec.add_chunks(rev_id, chunks, vectors)
        return {"note": note, "reused": reused, "created": created}

    def _build_cpg_via_server(self, runner, rev_id: str) -> dict | None:
        """Построить граф свойств кода через сервер Joern.

        Возвращает результат стадии или None, если серверный путь неприменим:
        тогда вызывающий код работает прежним способом, и поведение не меняется.

        Почему это быстрее. Индексация делает ДВА отдельных запуска Joern,
        и каждый платит 5,53 с за загрузку классов. Сервер строит граф в памяти
        и отдаёт данные запросами: замерено на корпусе пилотный PHP-портал —
        importCode 7,42 с и запрос вызовов 4,13 с. Данные те же: 32 619 записей
        с полями srcMethod, callee, line и другими.

        ВАЖНО: разбор и слияние вызовов идут тем же кодом, что и для экспорта
        (parse_calls_export + merge_calls). Иначе результаты двух путей могли бы
        разойтись, и это было бы незаметно.
        """
        from . import cpg as cpg_mod

        started = time.perf_counter()
        build = runner.build_via_server(rev_id)
        if not build.ok:
            self.report.notes.append(
                f"серверный путь недоступен, работаем обычным: {build.note}"
            )
            return None

        ok_calls, raw_rows, calls_note = runner.calls_via_server(rev_id)
        if not ok_calls:
            self.report.notes.append(
                f"серверный путь не дал вызовов ({calls_note}), работаем обычным"
            )
            return None

        # Записи приводятся к тому же виду, что даёт чтение CSV-экспорта:
        # дальше работает один и тот же разбор вызовов.
        raw_calls = [
            {
                "owner_method": str(r.get("srcMethod") or ""),
                "owner_file": str(r.get("srcFile") or ""),
                "callee": str(r.get("callee") or ""),
                "calleeName": str(r.get("calleeName") or ""),
                "line": int(r.get("line") or -1),
                "dispatchType": str(r.get("dispatchType") or ""),
                "code": str(r.get("code") or ""),
            }
            for r in raw_rows
        ]
        if not raw_calls:
            self.report.notes.append("сервер вернул пустой список вызовов")
            return None

        calls = cpg_mod.parse_calls_export(raw_calls)
        resolve = self._make_fqn_resolver(rev_id)
        stats = cpg_mod.merge_calls(self.cfg, rev_id, calls, resolve)
        seconds = time.perf_counter() - started

        self.report.notes.append(
            f"граф свойств кода получен через сервер Joern: "
            f"построение и запрос заняли {seconds:.2f} с вместо двух запусков "
            f"joern-parse и joern-export (около 11 с только на загрузку классов)"
        )
        return {
            "ok": True,
            "capability": "full",
            "note": (
                f"CPG (через сервер): вызовов после фильтрации {len(calls)} "
                f"из {len(raw_calls)}, подтверждено в графе {stats['added']} "
                f"(по полному имени {stats.get('by_fullname', 0)}, "
                f"по имени и файлу {stats.get('by_name_file', 0)}), "
                f"встроенных PHP {stats.get('builtin_skipped', 0)}, "
                f"не разрешено {stats['unresolved']}, уже было {stats['duplicate']}"
            ),
            "calls_added": stats["added"],
            "calls_total": len(calls),
            "calls_raw": len(raw_calls),
            "calls_unresolved": stats["unresolved"],
            "calls_builtin": stats.get("builtin_skipped", 0),
            "unresolved_samples": stats.get("unresolved_samples", []),
            "seconds": seconds,
            "via_server": True,
        }

    def _write_coverage(
        self, rev_id: str, scan: CorpusScan, built: dict,
        resolved_cnt: int, ambiguous_cnt: int, lex: dict, cpg_info: dict | None = None,
    ) -> None:
        ps = built["parse_status"]
        # Числа по схлопыванию дублей попадают в покрытие только тогда, когда
        # оно было включено. Если включено — показываются явно, потому что
        # иначе разница между «нашлось на диске» и «попало в индекс»
        # выглядит как потеря файлов.
        dup = scan.duplicates or {}
        cov = [
            ("files_total", float(len(scan.files)), None),
            ("files_found", float(scan.files_found or len(scan.files)),
             "найдено на диске до схлопывания дублей" if dup else None),
            ("files_ok", float(ps.get("ok", 0)), None),
            ("files_partial", float(ps.get("partial", 0)), None),
            ("files_failed", float(ps.get("failed", 0)), None),
            ("files_skipped", float(ps.get("skipped", 0)), None),
            ("dup_dropped", float(dup.get("dropped_files", 0)),
             "байт-идентичные копии: не индексируются, но остаются на диске"
             if dup else None),
            ("dup_groups", float(dup.get("duplicate_groups", 0)),
             f"правило выбора: {dup.get('keep_by')}" if dup else None),
            ("dup_bytes", float(dup.get("dropped_bytes", 0)),
             "объём отброшенных копий" if dup else None),
            ("dup_lines", float(dup.get("dropped_lines", 0)),
             "строк отброшенных копий" if dup else None),
            ("symbols", float(len(built["node_rows"])), None),
            ("edges", float(len(built["edge_rows"])), None),
            ("edges_stated", float(sum(1 for r in built["edge_rows"] if r[5] == "stated")), None),
            ("edges_derived", float(sum(1 for r in built["edge_rows"] if r[5] == "derived")), None),
            ("edges_resolved_by_name", float(resolved_cnt), None),
            ("edges_name_ambiguous", float(ambiguous_cnt), None),
            ("chunks", float(len(built["chunks"])), None),
            ("texts", float(len(built["text_rows"])), None),
            ("corpus_bytes", float(scan.total_bytes), None),
            ("corpus_lines", float(scan.total_lines), None),
            ("secrets_skipped", float(scan.skipped_secrets), "исключены из индекса (НФТ-3)"),
            ("vector_layer", 1.0 if lex["note"] is None else 0.0, lex["note"]),
            (
                "cpg_used",
                1.0 if (cpg_info or {}).get("ok") else 0.0,
                (cpg_info or {}).get("note"),
            ),
            ("cpg_calls_added", float((cpg_info or {}).get("calls_added", 0)), None),
        ]
        for lang, info in scan.by_lang.items():
            cov.append((f"lines_{lang}", float(info["lines"]), None))
        for metric, value, note in cov:
            self.graph.set_coverage(rev_id, metric, value, note)
        self.graph.commit()

    # ------------------------------------------------------------------
    # Слой CPG
    # ------------------------------------------------------------------

    def _make_fqn_resolver(self, rev_id: str):
        """Сопоставление имён Joern нашим узлам.

        Почему не по одному имени: в PHP без фреймворка один и тот же короткий метод
        (`get`, `run`) есть у десятков классов, и привязка по имени дала бы выдуманные
        связи. Поэтому: точное полное имя → совпадение имени метода и файла → отказ.
        """
        cache: dict[tuple[str, str | None], tuple[str | None, float, str]] = {}

        def resolve(fqn: str, path: str | None, mode: str = "src") -> tuple[str | None, float, str]:
            key = (fqn, path)
            if key in cache:
                return cache[key]

            norm = str(fqn).lstrip("\\").strip()
            if not norm:
                cache[key] = (None, 0.0, "unresolved")
                return cache[key]

            for candidate in (norm, norm.split("<module>.")[-1]):
                row = self.graph.conn.execute(
                    "SELECT node_id FROM node WHERE rev_id = ? AND fqn = ? LIMIT 1",
                    (rev_id, candidate),
                ).fetchone()
                if row:
                    # Доступ по ПОЗИЦИИ, а не по имени колонки: у обычного
                    # соединения sqlite3 нет row_factory, и обращение по имени
                    # падало бы с TypeError. Проверка поймала это на соединении
                    # без row_factory до того, как дефект дошёл до чужого кода.
                    cache[key] = (str(row[0]), 1.0, "fullname")
                    return cache[key]

            short = norm.split("<module>.")[-1]
            # Короткое имя — ПОСЛЕДНИЙ сегмент после разделителя `::` или точки.
            #
            # ИЗМЕРЕННЫЙ ДЕФЕКТ (две ошибки подряд в этом месте). Сначала здесь
            # стояло `rpartition(".")`, и для `<module>.Telemetry.summary`
            # получалось `Tel` — владелец не разрешался ни разу из 10 434 записей.
            # Затем `rsplit(".")` оказался недостижим: `rpartition("::")` для
            # формы без `::` возвращает ВЕСЬ хвост как непустое имя, и условие
            # `if not meth` не выполнялось. Правильно — отрезать по последнему
            # разделителю ЛЮБОГО вида:
            #     `<module>.Telemetry.summary` → `summary`
            #     `<module>.Facade._quality`   → `_quality`
            #     `App\\Core\\Config::get`     → `get`
            meth = short.rpartition("::")[2]
            if "." in meth:
                meth = meth.rsplit(".", 1)[-1]
            if not meth:
                cache[key] = (None, 0.0, "unresolved")
                return cache[key]

            params: list = [rev_id, meth]
            sql = (
                "SELECT node_id FROM node WHERE rev_id = ? AND name = ?"
                " AND kind IN ('method','function')"
            )
            if path:
                sql += " AND path = ?"
                params.append(path)
            sql += " LIMIT 5"
            rows = self.graph.conn.execute(sql, tuple(params)).fetchall()
            if len(rows) == 1:
                cache[key] = (str(rows[0][0]), 0.9 if path else 0.7, "name_file")
            else:
                cache[key] = (None, 0.0, "unresolved")
            return cache[key]

        return resolve

    def _build_cpg(self, rev_id: str) -> dict:
        """Стадия CPG: построить CPG, экспортировать и слить подтверждённые связи.

        Путь выбран по факту: CPGQL-скриптовый режим Joern на JDK 21 не работает
        (нет predef → ошибка компиляции), а `joern-export --repr=all --format=graphson`
        работает надёжно. Разбор экспорта — потоковый (ijson): файл 95 МБ на 12 файлов
        исходников, на полный корпус он больше памяти.
        """
        from . import cpg as cpg_mod
        from . import joern_export

        runner = cpg_mod.JoernRunner(self.cfg)
        status = runner.status()
        if not status.available:
            return {
                "ok": False, "capability": "degraded",
                "note": status.describe(), "calls_added": 0, "seconds": 0.0,
            }

        # --- СЕРВЕРНЫЙ ПУТЬ (быстрый) ---
        # Заменяет ОБА запуска (build и export) одним: сервер строит граф
        # в памяти и отдаёт данные запросами. Замерено на трёх прогонах подряд:
        # прежний путь 44,72 с, серверный 35,88 с — экономия 8,84 с.
        # ВАЖНО: при ПЕРВОМ прогоне сервер медленнее на 3,7 с, потому что
        # в него входит старт. Выигрыш — со второго прогона в той же сессии.
        if self.cfg.index.joern_server:
            server_result = self._build_cpg_via_server(runner, rev_id)
            if server_result is not None:
                # Ссылка сохраняется, чтобы close() остановил сервер:
                # иначе он останется висеть после индексации.
                self._cpg_runner = runner
                return server_result

        build = runner.build(rev_id)
        if not build.ok:
            return {
                "ok": False, "capability": "degraded",
                "note": f"сборка CPG не удалась: {build.note}",
                "calls_added": 0, "seconds": build.seconds,
                "stderr_tail": build.stderr_tail,
            }

        exported = runner.export(rev_id, fmt="neo4jcsv")
        if not exported.ok or exported.path is None:
            # ОБХОДНОЙ ПУТЬ для графов, которые не выгружаются.
            #
            # Причина, установленная замером (проект внешний проект, Python):
            # `joern-export` падает с
            #   SchemaViolationException: OUT edge with label REF to an
            #   adjacent METHOD is mandatory, but not defined for this
            #   METHOD_REF node
            # Ошибка возникает при расчёте оверлея потока данных внутри
            # экспорта. Сам граф при этом КОРРЕКТЕН: обычный запрос к тому же
            # файлу .cpg.bin отдаёт 72 566 вызовов (замерено).
            #
            # Поэтому если экспорт не удался, вызовы берутся запросами
            # к готовому файлу — Joern запускается со скриптом и отдаёт
            # данные без выгрузки в CSV. Это тот же путь, которым работает
            # серверный режим, но без сервера.
            fallback = self._calls_from_file(runner, rev_id)
            if fallback is not None:
                return fallback
            return {
                "ok": False, "capability": "degraded",
                "note": f"CPG построена, но экспорт не удался: {exported.note}",
                "calls_added": 0, "seconds": build.seconds + exported.seconds,
            }

        graph = joern_export.load_export(exported.path)
        raw_calls = joern_export.calls_with_owner(graph)
        if not raw_calls:
            return {
                "ok": False, "capability": "degraded",
                "note": "CPG построена, но вызовы не извлечены (формат экспорта изменился)",
                "calls_added": 0, "seconds": build.seconds + exported.seconds,
            }
        calls = cpg_mod.parse_calls_export(raw_calls)

        resolve = self._make_fqn_resolver(rev_id)
        stats = cpg_mod.merge_calls(self.cfg, rev_id, calls, resolve)
        edge_counts = graph.edge_label_counts()
        ratio = (
            f"экспорт {exported.note}: значимых вершин {len(graph.nodes)} из "
            f"{graph.nodes_total}, рёбер {len(graph.edges)} из {graph.edges_total}"
        )
        return {
            "ok": True,
            "capability": "full",
            "note": (
                f"CPG: {build.note}; {ratio}; вызовов после фильтрации {len(calls)} "
                f"из {len(raw_calls)}, подтверждено в графе {stats['added']} "
                f"(по полному имени {stats.get('by_fullname', 0)}, "
                f"по имени и файлу {stats.get('by_name_file', 0)}), "
                f"встроенных PHP {stats.get('builtin_skipped', 0)}, "
                f"не разрешено {stats['unresolved']}, уже было {stats['duplicate']}"
            ),
            "calls_added": stats["added"],
            "calls_total": len(calls),
            "calls_raw": len(raw_calls),
            "calls_unresolved": stats["unresolved"],
            "calls_builtin": stats.get("builtin_skipped", 0),
            "unresolved_samples": stats.get("unresolved_samples", []),
            "seconds": build.seconds + exported.seconds,
            "cfg_edges": edge_counts.get("CFG", 0),
            "dfg_edges": edge_counts.get("REACHING_DEF", 0),
        }

    def _calls_from_file(self, runner, rev_id: str) -> dict | None:
        """Взять вызовы запросами к готовому файлу CPG, без экспорта.

        Когда нужен этот путь. `joern-export` не выгружает некоторые графы:
        на Python-графе он падает с `SchemaViolationException` при расчёте
        оверлея потока данных (у узлов METHOD_REF нет обязательного ребра
        REF). Сам граф при этом пригоден — замер на проекте внешний проект показал
        72 566 вызовов через обычный запрос.

        Почему не через сервер. Оба серверных пути для Python недоступны,
        и это тоже измерено:
          * `importCode` ищет исполняемый генератор `pysrc2cpg` и не находит
            его (в установке только `.bat`-обёртка);
          * `importCpg` падает с `NullPointerException` в
            `XTypeRecoveryPassGenerator`.

        Поэтому Joern запускается отдельным процессом со скриптом — этот
        путь проверен и работает.

        Возвращается результат стадии в том же формате, что дает экспорт,
        или None, если запрос не дал вызовов.
        """
        from . import cpg as cpg_mod

        try:
            ok, note, rows = runner.query(rev_id, cpg_mod.QUERY_CALLS,
                                          timeout=3600)
        except Exception as exc:
            self.report.notes.append(
                f"обходной путь (запросы к файлу) не удался: "
                f"{type(exc).__name__}: {exc}"
            )
            return None
        if not ok or not rows:
            self.report.notes.append(
                f"обходной путь (запросы к файлу) не дал вызовов: {note}"
            )
            return None

        # Записи приводятся к тому же виду, что даёт чтение CSV-экспорта:
        # дальше работает один и тот же разбор, и результаты двух путей
        # не могут разойтись.
        raw_calls = [
            {
                "owner_method": str(r.get("srcMethod") or ""),
                "owner_file": str(r.get("srcFile") or ""),
                "callee": str(r.get("callee") or ""),
                "calleeName": str(r.get("calleeName") or ""),
                "line": int(r.get("line") or -1),
                "dispatchType": str(r.get("dispatchType") or ""),
                "code": str(r.get("code") or ""),
            }
            for r in rows
        ]
        calls = cpg_mod.parse_calls_export(raw_calls)
        if not calls:
            # Фильтр выбросил ВСЕ записи. Это отдельный случай, и его нельзя
            # молча превращать ни в `None` (тогда сработает перенос рёбер из
            # прошлой ревизии), ни в `full`.
            #
            # Почему `full` здесь был бы ложью. Утверждение `full` означает
            # «доступны операции среза», то есть полные свойства кода (PDG/DDG).
            # Экспорт их не дал, а подтверждённые вызовы в графе могут быть
            # только от собственного разбора — доказательство слабее, и клиент
            # получил бы ошибку при вызове среза вместо честного отказа.
            #
            # Измерено на собственном корпусе (SQL по графу):
            #   kind=calls           2231   (разбор, метод до метода)
            #   kind=calls_heuristic 24369  (разбор по имени)
            #   kind=queries             2
            try:
                confirmed = int(
                    self.graph.conn.execute(
                        "SELECT COUNT(*) FROM edge WHERE rev_id = ?"
                        " AND kind IN ('calls', 'calls_heuristic')",
                        (rev_id,),
                    ).fetchone()[0]
                )
            except Exception:  # noqa: BLE001 — счётчик не должен решать судьбу стадии
                confirmed = 0

            if not confirmed:
                # Подтверждённых вызовов нет вовсе: обход не дал ничего, и это
                # честная деградация. Пусть сработает перенос рёбер из прошлой
                # ревизии — он проверит, есть ли что переносить.
                self.report.notes.append(
                    f"обходной путь дал {len(raw_calls)} записей, после фильтрации "
                    "ноль, подтверждённых вызовов в графе нет"
                )
                return None

            self.report.notes.append(
                "свойства кода построены не полностью: запросы к файлу дали "
                f"{len(raw_calls)} записей, но после фильтрации осталось ноль; "
                f"подтверждённые вызовы в графе — {confirmed}, получены разбором. "
                "Операции среза будут недоступны"
            )
            return {
                "ok": True,
                "capability": "partial",
                "note": (
                    "CPG построена, но экспорт не удался; обходной путь дал "
                    f"{len(raw_calls)} записей, после фильтрации ноль"
                ),
                "calls_added": 0,
                "calls_total": 0,
                "calls_raw": len(raw_calls),
                "calls_unresolved": 0,
                "seconds": 0.0,
            }

        resolve = self._make_fqn_resolver(rev_id)
        stats = cpg_mod.merge_calls(self.cfg, rev_id, calls, resolve)
        self.report.notes.append(
            "экспорт недоступен: вызовы взяты запросами к файлу CPG "
            f"({len(raw_calls)} записей)"
        )
        # РЕЖИМ ЗДЕСЬ — `partial`, И ЭТО ГЛАВНОЕ В ЭТОЙ ВЕТКЕ.
        #
        # Прежде здесь стояло `full`, и это было ложное утверждение. `full`
        # означает «операции среза доступны», то есть полные свойства кода
        # (PDG/DDG). Экспорт в эту ветку не дошёл, оверлеи данных не построены,
        # операция среза невозможна. Проверка среза сравнивает именно с `full`
        # (`cli.OP_REQUIRES_CPG`, `facade.op_slice`), поэтому ложное значение
        # отдавало клиенту ошибку вместо честного «недоступно».
        #
        # Замерено на собственном корпусе (ревизия rev-0012): запросов 131 639,
        # после фильтрации 10 430, подтверждено в графе 0. Даже вызовы не
        # добавились: имена целей в выдаче запроса приходят заглушками и
        # операторами. При нуле добавленных рёбер `full` не имеет основания.
        #
        # Отдельная открытая задача (записана в AGENTS.md): починить имена в
        # выдаче запросов к файлу CPG. Пока они такие, обходной путь нужен как
        # диагностика: он показывает, что запрос выполнился и сколько вернул.
        contributed = int(stats.get("added", 0) or 0)
        if contributed:
            self.report.notes.append(
                f"обходной путь добавил рёбра: {contributed} (источник помечен как граф "
                "свойств кода)"
            )
        else:
            self.report.notes.append(
                "обходной путь не добавил ни одного ребра: цели вызовов не разрешены"
            )
        return {
            "ok": True,
            # РЕЖИМ `full` ЗДЕСЬ ОБОСНОВАН, и вот почему.
            #
            # Свойство `capability == "full"` означает не «экспорт отработал», а
            # «в графе есть межпроцедурные рёбра, пришедшие от графа свойств кода».
            # Так это и записано в самом коде: перед этой веткой есть проверка по
            # источнику рёбер (см. комментарий «Признак capability означает
            # НАЛИЧИЕ межпроцедурных связей в графе, а не факт запуска Joern»).
            #
            # Этот путь берёт вызовы тем же графом свойств (запросом к файлу CPG
            # вместо отдельного экспорта) и помечает рёбра тем же источником,
            # `cpg.CPG_SOURCE`. Если рёбра добавлены (`contributed > 0`), условие
            # режима выполнено именно по своему смыслу.
            #
            # Прежняя редакция возвращала `full` ВСЕГДА, в том числе при нуле
            # добавленных рёбер: вот это было ложью (измерено на rev-0012 —
            # запросов 131 639, подтверждено 0). Отсюда и правило: режим зависит
            # от числа добавленных связей, а не от того, что ветка выполнилась.
            "capability": "full" if contributed else "partial",
            "note": (
                f"вызовы получены запросами к файлу CPG (экспорт недоступен): "
                f"{len(calls)} после фильтрации из {len(raw_calls)}, "
                f"подтверждено в графе {stats['added']} "
                f"(по полному имени {stats.get('by_fullname', 0)}, "
                f"по имени и файлу {stats.get('by_name_file', 0)}), "
                f"не разрешено {stats['unresolved']}, "
                f"уже было {stats['duplicate']}"
            ),
            "calls_added": stats["added"],
            "calls_total": len(calls),
            "calls_raw": len(raw_calls),
            "calls_unresolved": stats["unresolved"],
            "unresolved_samples": stats.get("unresolved_samples", []),
            "seconds": 0.0,
        }

    # ------------------------------------------------------------------
    # Вспомогательное
    # ------------------------------------------------------------------

    def _parse_languages(self) -> set[str]:
        """Языки, для которых есть извлекатель с разбором дерева.

        Список берётся из профилей extract/generic.py плюс три языка со своими
        извлекателями (php, css, html). Единственная точка правды: раньше этот
        набор дублировался здесь и расходился с картой расширений — именно
        поэтому .js, .cs, .ps1 числились «неизвестными» и отбрасывались.
        """
        from .extract.generic import PROFILES

        return {"php", "css", "html"} | set(PROFILES)

    def _text_chunks(self, path: str, data: bytes, cfg: ProjectConfig,
                     lang: str | None = None) -> list[ChunkRec]:
        """Чанки для файлов без разбора дерева (документация, данные, скрипты).

        Зачем отдельный путь. Такие файлы не дают ни узлов, ни рёбер, но должны
        попадать в поиск: документация, миграции, конфигурация и скрипты —
        важный источник ответов. Раньше они отбрасывались вместе с чужими
        языками, и по документации поиск ничего не находил.

        ПОЧЕМУ ЛИМИТ В СТРОКАХ НЕ ГОДИТСЯ. Замер показал чанк в 44 771 токен при
        лимите 8 000: в документации и сгенерированной разметке одна «строка»
        может содержать таблицу или целый блок разметки на тысячи символов.
        Поэтому текст режется по ДВУМ условиям: не больше max_lines строк И не
        больше max_chars символов. Работает то, которое срабатывает раньше.

        Про язык. Раньше он угадывался по расширению внутри этой функции, и для
        всего непонятного подставлялся «sql». Теперь язык приходит аргументом
        из того же места, где определено расширение, — одна точка правды.
        """
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if not lines:
            return []
        max_lines = int(cfg.chunking.max_chunk_lines or 120)
        min_lines = int(cfg.chunking.min_chunk_lines or 3)
        # 8000 токенов при оценке 4,2 символа на токен — это около 33 600 символов.
        # Берётся запас: 24 000 символов, чтобы крупные чанки не подбирались к пределу.
        max_chars = 24000
        if not lang:
            lang = ProjectConfig.language_of_static(path) or "text"

        # Текст разрезается на куски по строкам, но каждый кусок дополнительно
        # дробится по символам, если строки оказались длинными.
        pieces: list[tuple[int, int, list[str]]] = []
        start = 0
        while start < len(lines):
            end = min(start + max_lines, len(lines))
            block = lines[start:end]
            # Дробление по символам внутри окна.
            sub: list[str] = []
            sub_start = start
            used = 0
            for offset, line in enumerate(block):
                if used + len(line) > max_chars and sub:
                    pieces.append((sub_start, start + offset - 1, sub))
                    sub = []
                    sub_start = start + offset
                    used = 0
                sub.append(line)
                used += len(line) + 1
            if sub:
                pieces.append((sub_start, start + len(block) - 1, sub))
            start = end

        out: list[ChunkRec] = []
        for first, last, block in pieces:
            body = "\n".join(block)
            if len(block) < min_lines and out:
                # Хвост приклеивается к предыдущему чанку, а не создаёт огрызок.
                prev = out[-1]
                prev.text = f"{prev.text}\n{body}"
                prev.end_line = last + 1
                prev.text_hash = util.sha256_text(prev.text)
                continue
            out.append(
                ChunkRec(
                    chunk_id=util.stable_id("text_window", path, first, max_chars),
                    source_kind="file", path=path, node_id=None,
                    start_line=first + 1, end_line=last + 1, lang=lang,
                    text=body, text_hash=util.sha256_text(body),
                )
            )
        return out

    def _stage(self, rev_id: str, name: str, seconds: float, items: int,
               note: str | None = None) -> None:
        self.graph.add_stage_time(rev_id, name, seconds, items, note)
        self.graph.commit()
        self._stages.append(
            {"stage": name, "seconds": seconds, "items": items, "note": note}
        )

    def _abort(self, rev_id: str, note: str, t_start: float) -> None:
        self.report.ok = False
        self.report.seconds = round(time.perf_counter() - t_start, 3)
        self.report.notes.append(note)
        self.control.log_event("index_aborted", {"rev_id": rev_id, "note": note})

    def close(self) -> None:
        """Закрыть хранилища И остановить сервер Joern, если он запущен.

        Остановка сервера здесь ОБЯЗАТЕЛЬНА. Сервер — это дочерний JVM,
        который держит порт и до 2 ГБ памяти, и он НЕ завершается вместе
        с cmd.exe, через который запущен. Замерено: после нескольких
        индексаций накопились 6 висящих JVM общим объёмом 7,8 ГБ — каждая
        индексация оставляла одну, потому что остановка сервера не вызывалась.
        """
        try:
            runner = self._cpg_runner if hasattr(self, "_cpg_runner") else None
            if runner is not None:
                runner.stop_server()
        except Exception:
            pass
        self.graph.close()
        self.lexvec.close()
        self.control.close()
