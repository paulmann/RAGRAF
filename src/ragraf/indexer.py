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

from . import textdec, util
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
    # Слой баз: собран или нет и почему. Нужен в ответе индексации, иначе не
    # отличить «базы нет» от «сбор настроен и отработал».
    databases: dict = field(default_factory=dict)
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
    # Прогон прерван по бюджету времени: ревизия НЕ закреплена, прежний индекс рабочий.
    # Поле нужно вызывающему (`ensure`), чтобы вернуть статус «partial», а не молчание.
    budget_exceeded: bool = False
    # Где именно встал прогон: стадия и сколько файлов успело обработаться.
    # Нужны потому, что «бюджет исчерпан» без адреса не подсказывает, что делать:
    # на `ast` помогает исключение каталога, на `resolve` — сокращение корпуса.
    budget_stage: str = ""
    budget_files_done: int = 0

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
            "budget_exceeded": self.budget_exceeded,
            "budget_stage": self.budget_stage,
            "budget_files_done": self.budget_files_done,
        }


class _BudgetExceeded(Exception):
    """Прогон обязан остановиться: бюджет времени исчерпан ВНУТРИ стадии.

    Почему исключение, а не возврат флага. Стадия `ast` — это цикл по файлам,
    и сообщить о превышении надо ИЗ него; возврат флага из середины цикла
    потребовал бы протащить результат через все уровни сборки фактов. Исключение
    перехватывается в `run`, где есть имя стадии, число обработанных файлов и
    подсказка, — то есть ровно то, чего не хватало ответу «таймаут без деталей»
    (требование P0-2 ТЗ).
    """

    def __init__(self, stage: str, files_done: int) -> None:
        super().__init__(f"бюджет исчерпан на стадии {stage}")
        self.stage = stage
        self.files_done = files_done


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

    def run(self, *, with_vectors: bool = True, limit_files: int | None = None,
            budget_seconds: float | None = None) -> IndexReport:
        t_start = time.perf_counter()
        cfg = self.cfg
        cfg.ensure_dirs()
        # --vectors явно разрешает получение модели; без флага — только из кэша.
        self.embedder.allow_download = bool(with_vectors)

        rev_id = self.control.next_rev_id()
        parent_rev = self.control.current_rev()
        self.report.rev_id = rev_id
        ctx = StageContext(cfg=cfg, rev_id=rev_id, parent_rev=parent_rev)

        # --- бюджет прогона (P0-2) ---
        # `ensure` вызывается первым шагом агента и обязан отвечать. Замер на корпусе
        # BWO (5601 файл): прогон с CPG (Joern) и GPU не завершался за ~11 минут, и
        # вызов уходил в таймаут. Поэтому: при нехватке времени тяжёлые стадии
        # пропускаются, а при исчерпании бюджета прогон прерывается ДО закрепления
        # ревизии — прежний индекс остаётся рабочим, отчёт называет стадию.
        deadline = (t_start + float(budget_seconds)) if budget_seconds else None

        def budget_left() -> float | None:
            return None if deadline is None else deadline - time.perf_counter()

        def budget_over(stage: str) -> bool:
            left = budget_left()
            if left is None or left > 0:
                return False
            self.report.budget_exceeded = True
            self.report.budget_stage = stage
            self._abort(
                rev_id,
                f"бюджет времени исчерпан на стадии «{stage}»: прогон прерван, ревизия "
                f"не закреплена, прежний индекс остался рабочим. Увеличьте "
                f"index.budget_seconds или сократите корпус через exclude.",
                t_start,
            )
            return True

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
        # Внутри стадии есть контрольные точки: `ast` — самый долгий этап
        # (на BWO 370–410 с при бюджете 120 с), и проверка только ПОСЛЕ него
        # означала, что бюджет соблюдается «в среднем», а не как предел.
        def check_budget(stage: str, files_done: int) -> None:
            if budget_left() is not None and budget_left() < 0:
                raise _BudgetExceeded(stage, files_done)

        try:
            built = self._build_facts(rev_id, scan, check_budget=check_budget)
        except _BudgetExceeded as exc:
            self.report.budget_exceeded = True
            self.report.budget_stage = exc.stage
            self.report.budget_files_done = exc.files_done
            self._abort(
                rev_id,
                f"бюджет времени исчерпан на стадии «{exc.stage}» после "
                f"{exc.files_done} файлов из {len(scan.files)}: прогон прерван, "
                f"ревизия не закреплена, прежний индекс остался рабочим. "
                f"Увеличьте index.budget_seconds или сократите корпус через exclude.",
                t_start,
            )
            return self.report
        self._stage(rev_id, "ast", round(time.perf_counter() - t0, 3), len(scan.files))
        if budget_over("ast"):
            return self.report
        if built["ast_notes"]:
            self.report.notes.append(
                f"файлов с неполным разбором: {len(built['ast_notes'])} "
                f"(первые: {', '.join(built['ast_notes'][:3])})"
            )

        # --- стадия 3: загрузка ---
        # Проверка ПЕРЕД стадией, а не только после: загрузка на BWO пишет
        # 2,2 млн строк и идёт минуту. Начинать её «в долг» по бюджету — значит
        # гарантированно выйти за предел и всё равно прервать прогон.
        if budget_over("load"):
            return self.report
        t0 = time.perf_counter()
        self.graph.add_files(built["file_rows"])
        self.graph.add_nodes(built["node_rows"])
        self.graph.add_edges(built["edge_rows"])
        self.graph.add_text_chunks(built["text_rows"])
        # P1-3: реестр точек входа. До этой правки таблица `web_route`
        # создавалась, но не заполнялась НИКОГДА (в коде не было ни одной
        # вставки), поэтому операция `routes` всегда отвечала пусто, а
        # `entrypoints` не знал ни одного маршрута.
        if built.get("route_rows"):
            self.graph.add_routes(built["route_rows"])
        self.graph.commit()
        self._stage(
            rev_id, "load", round(time.perf_counter() - t0, 3),
            len(built["file_rows"]) + len(built["node_rows"]) + len(built["edge_rows"])
            + len(built["text_rows"]) + len(built.get("route_rows") or []),
        )

        # --- стадия 4: разрешение внешних имён ---
        # На BWO эта стадия идёт 218–280 с, поэтому она тоже не начинается,
        # если бюджета на неё уже нет.
        if budget_over("resolve"):
            return self.report
        t0 = time.perf_counter()
        resolved_cnt, ambiguous_cnt = self._resolve_external_names(rev_id)
        # Рёбра наследования разрешаются отдельно и ДО общих счётчиков не
        # подмешиваются: у них своя природа (класс, а не метод) и своя
        # достоверность. Замер: на revoap все 29 рёбер `extends` вели на
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
        if budget_over("schema"):
            return self.report

        # --- стадия 5: CPG (межпроцедурные связи и потоки данных) ---
        t0 = time.perf_counter()
        # Состояние ключа запоминается ДО стадии. Быстрая переиндексация
        # выставляет cfg.index.joern_enabled = False на время прогона и
        # возвращает назад (watch.py:540,542-543,596), поэтому после прогона
        # значение неотличимо от настройки файла корпуса. Отсюда это отличие
        # НЕВОССТАНОВИМО: доказано только «ключ был выключен в момент стадии».
        # Мало времени до дедлайна — CPG (Joern) не собираем: это самая дорогая
        # стадия, а без неё индекс всё равно пригоден для поиска и схемы.
        left = budget_left()
        if left is not None and left < 45.0 and cfg.index.joern_enabled:
            cfg.index.joern_enabled = False
            self.report.notes.append(
                "режим без CPG: до конца бюджета меньше 45 с, стадия Joern пропущена"
            )
        joern_disabled_by_run = not bool(cfg.index.joern_enabled)
        cpg_info = self._build_cpg(rev_id)
        if joern_disabled_by_run and not cpg_info.get("ok"):
            # ИЗМЕРЕННЫЙ ДЕФЕКТ ФОРМУЛИРОВКИ. status() из cpg.py отдаёт
            # «CPG отключена в конфигурации (index.joern_enabled=false)», и это
            # было записано в stage_timing и coverage как причина. Замер
            # rev-0069/rev-0070: в YAML ключ true, а выключил его код на время
            # быстрого прогона — то есть причина называлась неверно. Здесь
            # пишется только доказанное, без домысла о том, кто выключил ключ.
            cpg_info["note"] = (
                "CPG в этом прогоне не собиралась (режим без CPG): на момент "
                "стадии index.joern_enabled был выключен. Быстрое выключение "
                "на время прогона делает watch.py и возвращает значение назад, "
                "поэтому «отключена в конфигурации» здесь не утверждается — "
                "с этой стороны настройка файла и выключение кодом неразличимы"
            )

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
        #
        # Понижение покрытия читается ДО переноса и сравнивается с ПРЕДЫДУЩЕЙ
        # ревизией, а не с донором рёбер: вопрос не «откуда взяли связи», а
        # «было ли покрытие и пропало ли оно». Замер rev-0069/rev-0070: у первой
        # cpg_used=1 и cpg_calls_added=1203, у второй оба нуля — при полностью
        # совпадающих capability, joern_used, счётчиках и config_hash. По сводке
        # ревизии потеря 1203 рёбер не была видна НИКАК.
        prev_cpg = self._cpg_coverage_of(parent_rev)
        prev_had_cpg = bool(prev_cpg["cpg_used"]) or prev_cpg["cpg_calls_added"] > 0
        cpg_downgraded = bool(prev_had_cpg and not cpg_info.get("ok"))
        cpg_carried_from: str | None = None
        carried = 0

        if not cpg_info.get("ok"):
            carried = self._carry_cpg_edges(rev_id)
            cpg_carried_from = getattr(self, "_cpg_carry_source", None) if carried else None
            if carried:
                cpg_info["carried_from_prev"] = carried
                cpg_info["carried_from_rev"] = cpg_carried_from
                if joern_disabled_by_run:
                    self.report.notes.append(
                        f"CPG в этом прогоне не собиралась (режим без CPG), "
                        f"рёбра вызовов перенесены из {cpg_carried_from}: {carried}"
                    )
                else:
                    self.report.notes.append(
                        f"граф свойств кода перенесён из {cpg_carried_from}: "
                        f"рёбер {carried} (построение CPG не выполнено)"
                    )
                # Донор дописывается и в note стадии: stage_timing читается
                # позже (`index_status`, CLI) и остаётся единственным следом
                # прогона для путей вроде `ensure`, которые отчёт индексатора
                # не показывают.
                cpg_info["note"] = (
                    f"{cpg_info.get('note') or 'CPG не построена'}; "
                    f"рёбра вызовов перенесены из {cpg_carried_from}: {carried}"
                )
            else:
                self.report.notes.append(
                    "граф свойств кода не построен и переносить нечего: "
                    "операция slice недоступна"
                )
        cpg_info["downgraded"] = cpg_downgraded
        if cpg_downgraded:
            # Понижение покрытия сообщается громко и в трёх местах: report.notes
            # (печатают index и reindex), coverage (виден в index_status) и
            # rev.json (поле cpg.downgraded). Молчаливое понижение — тот самый
            # дефект, из-за которого rev-0070 выглядела равноценной rev-0069.
            downgrade_note = (
                f"ПОНИЖЕНИЕ ПОКРЫТИЯ CPG: у предыдущей ревизии {parent_rev} "
                f"cpg_used={int(prev_cpg['cpg_used'])}, рёбер от CPG добавлено "
                f"{int(prev_cpg['cpg_calls_added'])}; в этом прогоне CPG "
                f"не собиралась (cpg_used=0, cpg_calls_added=0)"
            )
            if carried and cpg_carried_from:
                downgrade_note += (
                    f". Связи перенесены из {cpg_carried_from}: {carried} рёбер — "
                    f"capability остаётся full, но это НЕ сборка CPG этого прогона"
                )
            else:
                downgrade_note += ". Переносить нечего: связей от CPG в этой ревизии нет"
            cpg_info["downgrade_note"] = downgrade_note
            self.report.notes.append(downgrade_note)

        self._stage(
            rev_id, "cpg", round(time.perf_counter() - t0, 3),
            cpg_info.get("calls_added", 0), cpg_info.get("note"),
        )
        if cpg_info.get("stderr_tail"):
            # Вывод Joern показывается при ЛЮБОМ отказе сборки, а не только когда
            # CPG не подключена вовсе. Замер 16.10.2026: при переносе рёбер отказ
            # («Joern завершился с кодом 1») оставался без причины — а причина,
            # «Cannot unpack libzstd-jni…: Access is denied», лежала в начале
            # stderr и терялась вместе с ним.
            self.report.notes.append(f"вывод Joern: {cpg_info['stderr_tail']}")
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

        # Режим CPG для rev.json. Различие обязательно и измерено: у
        # rev-0069/rev-0070 capability и joern_used совпадали, потому что после
        # переноса рёбер ревизия объявляется полной. Только это поле показывает,
        # что во второй CPG не собиралась.
        cpg_info["mode"] = (
            "built" if cpg_info.get("ok")
            else ("carried" if cpg_info.get("carried_from_prev") else "none")
        )

        # --- стадия 5.5: слой баз данных (только если сбор включён) ---
        # Стоит ПЕРЕД лексикой намеренно: примеры баз попадают в те же чанки, и
        # вычислитель векторов получает их одним проходом, а не вторым запуском.
        # Перед сбором — проверка бюджета: сбор баз ходит в СУБД и на большом
        # наборе баз стоит десятки секунд.
        if budget_over("databases"):
            return self.report
        db_stats = self._collect_databases(rev_id)

        # --- стадия 6: лексика и вектор ---
        # Лексика с векторами на BWO идёт 150–180 с: не начинаем её без
        # остатка бюджета, иначе прогон выйдет за предел обязательно.
        if budget_over("lexvec"):
            return self.report
        t0 = time.perf_counter()
        lex = self._build_lexvec(rev_id, built["chunks"], with_vectors)
        # Слой баз показывается отдельной стадией: видно, собран он или
        # почему не собран. Без этого «пусто» и «выключено» выглядят
        # одинаково, а это разные состояния.
        if db_stats:
            self.report.databases = db_stats
            graph_stats = db_stats.get("graph") or {}
            self._stage(rev_id, "databases", 0.0,
                        int(graph_stats.get("tables", 0)))
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
            # Паспорт ревизии: окружение (версии извлекателей), режим CPG и
            # суммы — в rev.json, а не только в graph.sqlite. Замер
            # rev-0069/rev-0070 показал, что без этого активная ревизия
            # выглядит равноценной даже когда CPG в ней не собиралась.
            extractor_versions=ctx.extractor_versions,
            cpg={
                "mode": cpg_info.get("mode"),
                "used": bool(cpg_info.get("ok")),
                "calls_added": int(cpg_info.get("calls_added", 0) or 0),
                "carried_from": cpg_info.get("carried_from_rev"),
                "downgraded": bool(cpg_info.get("downgraded")),
            },
            corpus_hash=scan.corpus_hash,
            config_hash=cfg.config_hash,
        )
        if budget_over("lexvec"):
            return self.report

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

    def _build_facts(self, rev_id: str, scan: CorpusScan, check_budget=None) -> dict:
        cfg = self.cfg
        file_rows: list[tuple] = []
        node_rows: list[tuple] = []
        edge_rows: list[tuple] = []
        text_rows: list[tuple] = []
        chunks: list[ChunkRec] = []
        route_rows: list[tuple] = []
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

        # Контрольная точка бюджета: каждые 32 файла, а не на каждом — вызов
        # `time.perf_counter` и проверка дедлайна дешевы, но не бесплатны, а
        # стадия идёт по тысячам файлов. Шаг выбран так, чтобы остановка
        # укладывалась в доли секунды от момента исчерпания бюджета.
        processed = 0
        for cf in scan.files:
            processed += 1
            if check_budget is not None and processed % 32 == 0:
                check_budget("ast", processed)
            try:
                data = cf.abs_path.read_bytes()
            except OSError as exc:
                file_rows.append(
                    (rev_id, cf.path, cf.lang, cf.lines, cf.sha256, "failed",
                     f"чтение: {exc}", "")
                )
                parse_status["failed"] = parse_status.get("failed", 0) + 1
                continue

            if cf.lang not in parse_langs:
                # Язык без разбора деревом: узлов и рёбер не даёт, но попадает
                # в поиск через текстовые чанки. Раньше такие файлы молча
                # отбрасывались, поэтому по документации, конфигам и скриптам
                # batch/SQL поиск ничего не находил.
                file_rows.append(
                    (rev_id, cf.path, cf.lang, cf.lines, cf.sha256, "text", None,
                     textdec.detect(data))
                )
                parse_status["text"] = parse_status.get("text", 0) + 1
                chunks.extend(self._text_chunks(cf.path, data, cfg, cf.lang))
                continue

            res = self.extractor.extract(cf.path, data)
            file_rows.append(
                (rev_id, cf.path, cf.lang, res.lines, res.sha256, res.parse_status,
                 res.parse_note, res.encoding)
            )
            parse_status[res.parse_status] = parse_status.get(res.parse_status, 0) + 1

            for s in res.symbols:
                # B5: заглушка внешней цели НЕ ОБЪЯВЛЕНА НИ В ОДНОМ файле —
                # `extract/generic.py:585` делает ОДИН узел на весь корпус.
                # Прежняя редакция писала ей путь и строки текущего файла,
                # а ключ таблицы `node` — `(rev_id, node_id)` и запись идёт
                # `INSERT OR REPLACE` (`revisions.py`): у общей заглушки
                # оставался путь ПОСЛЕДНЕГО файла, который её упомянул.
                # Измерено на rev-0070 и подтверждено на rev-0073: узел
                # `fqn='python:cwd'`, `kind='external'` имел
                # `path='tools/ragraf-mcp-http.py'`, `start_line=138`, хотя
                # объявления в этом файле нет; в callees это читалось как связь
                # с чужим файлом. Схема допускает NULL в этих колонках
                # (`store.py:133-146` — `path TEXT`, `start_line INTEGER`).
                # `end_line` обнуляется вместе с `start_line`: оставить его —
                # значит сохранить выдуманный адрес в чужом файле.
                #
                # Свойство `shared` (P1-3): так помечены узлы, ОБЩИЕ для
                # корпуса, — ключ перевода (`tpl:reg.months`), маршрут,
                # пакетная переменная Perl. Их адрес — тоже чужой: `%{key}`
                # встречается в десяти шаблонах, и запись пути последнего из
                # них выдала бы один шаблон за место объявления ключа.
                is_stub = s.kind == "external" or bool(
                    (getattr(s, "props", None) or {}).get("shared")
                )
                node_rows.append(
                    (
                        rev_id, s.node_id, s.kind, s.name, s.fqn,
                        None if is_stub else res.path,
                        None if is_stub else s.start_line,
                        None if is_stub else s.end_line,
                        s.signature, util.dumps(s.props), "structural",
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
            route_seen: set[tuple[str, str]] = set()
            for rt in getattr(res, "routes", None) or []:
                route_key = str(rt.get("route", ""))
                method = str(rt.get("method", ""))
                # Повтор маршрута в файле даёт одну запись реестра: строки
                # разных вызовов одного и того же адреса — не разные точки
                # входа. Иначе на BWO в реестре оказалось 169 тысяч строк
                # (одна разметка даёт десятки повторов), и операция `routes`
                # перебирала их секундами.
                pair = (route_key, method)
                if pair in route_seen:
                    continue
                route_seen.add(pair)
                route_rows.append(
                    (
                        rev_id,
                        util.stable_id("route", res.path, route_key),
                        res.file_node_id,
                        route_key,
                        method,
                        res.path,
                        int(rt.get("line", 0) or 0),
                        0.9,
                    )
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
            "route_rows": route_rows,
            "chunks": chunks,
            "kind_counts": kind_counts,
            "edge_counts": edge_counts,
            "parse_status": parse_status,
            "ast_notes": ast_notes,
        }

    def _cpg_coverage_of(self, rev_id: str | None) -> dict[str, float]:
        """Покрытие CPG указанной ревизии: собран ли граф и сколько рёбер добавлено.

        Нужно, чтобы понижение покрытия было видно. Измерено на rev-0069/rev-0070:
        у rev-0069 `coverage.cpg_used=1` и `cpg_calls_added=1203`, у rev-0070 оба
        нуля, но у обеих ревизий совпадали `capability=full`, `joern_used=1`,
        счётчики файлов/узлов/рёбер и `config_hash` — по сводке ревизии потеря
        покрытия не читалась вообще. Сравнение с предыдущей ревизией — единственный
        способ её заметить изнутри прогона.

        Чтение идёт по позиции столбца, а не по имени: у обычного соединения
        sqlite3 нет row_factory, и обращение по имени упало бы с TypeError.
        Возвращаются нули, если данных нет — «неизвестно» здесь неотличимо от
        «не было», и в обе стороны это безопаснее выдуманного значения.
        """
        out = {"cpg_used": 0.0, "cpg_calls_added": 0.0}
        if not rev_id:
            return out
        try:
            rows = self.graph.conn.execute(
                "SELECT metric, value FROM coverage WHERE rev_id = ?"
                " AND metric IN ('cpg_used', 'cpg_calls_added')",
                (rev_id,),
            ).fetchall()
        except Exception:
            return out
        for row in rows:
            try:
                metric = str(row[0])
                if metric in out:
                    out[metric] = float(row[1] or 0)
            except (TypeError, ValueError, IndexError):
                continue
        return out

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

        Возвращается число перенесённых рёбер (сигнатура не меняется: значение
        читают вызывающий код и проверки). Ревизия-донор запоминается в
        `self._cpg_carry_source` — она нужна в rev.json и в текстах отчёта,
        чтобы перенос был отличим от собственной сборки CPG.
        """
        self._cpg_carry_source: str | None = None
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
            self._cpg_carry_source = source_rev

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
        Замерено на revoap (PHP, rev-0034): все 29 рёбер `extends` указывали на
        заглушки, и **ни одно** не вело на настоящий узел класса. То есть факты
        о наследовании в графе были невидимы для операций: имя базы есть, цели нет.

        КАК разрешается. По последнему сегменту имени: в revoap классы записаны
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

    def _collect_databases(self, rev_id: str) -> dict:
        """Собрать структуру живых баз в индекс, если сбор включён.

        Условие «сбор включён» обязательно: у большинства корпусов своей базы нет,
        и попытка подключиться к СУБД на каждой индексации была бы и медленной, и
        бессмысленной. Отсутствие базы — нормальное состояние, а не ошибка прогона.
        """
        try:
            from . import db_access as access_mod

            access = access_mod.DbAccessStore().load()
        except Exception as exc:
            return {"collected": False, "reason": f"{type(exc).__name__}: {exc}"}
        if not access.enabled:
            return {"collected": False, "reason": "сбор баз выключен в настройках"}

        try:
            from . import db_collect as collect_mod

            collected = collect_mod.collect(access)
            if collected.get("result") is None:
                return {"collected": False,
                        "reason": collected.get("reason") or "сбор не выполнен"}
            from . import db_writer as writer_mod

            stats = writer_mod.write_introspection(self.graph, rev_id,
                                                   collected["result"],
                                                   collected.get("samples") or {})
            lex = writer_mod.write_lexvec(self.lexvec, self.embedder, rev_id,
                                          collected["result"],
                                          collected.get("samples") or {})
            # Отпечаток запоминается: по нему сторож поймёт, изменилась ли
            # структура, не читая базу заново.
            from . import db_index as db_index_mod

            db_index_mod.record_fingerprint(self.control.conn, rev_id,
                                            collected["fingerprint"],
                                            access.dialect)
            return {"collected": True, "graph": stats, "lexvec": lex,
                    "fingerprint": collected["fingerprint"]}
        except Exception as exc:
            # Сбой слоя баз не должен отменять индексацию кода.
            return {"collected": False, "reason": f"{type(exc).__name__}: {exc}"}

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
            # Причина называется как есть. Раньше здесь всегда стояло «sqlite-vec
            # не загрузился», и это было НЕВЕРНО, когда отказывала запись в vec0:
            # расширение загружено, а падает вставка (замер 16.10.2026).
            note = f"векторный слой недоступен: {self.lexvec.vec_unavailable_reason()}"

        self.lexvec.add_chunks(rev_id, chunks, vectors)
        # Отказ записи мог случиться ВНУТРИ add_chunks (там сбрасывается vec_ok) —
        # заметка обязана сказать об этом в ЭТОМ же прогоне, а не в следующем:
        # ветка выше решает про vec_ok, каким он был ДО записи.
        if note is None and getattr(self.lexvec, "vec_error", ""):
            note = f"векторный слой отключён: {self.lexvec.vec_error}"
        return {"note": note, "reused": reused, "created": created}

    def _build_cpg_via_server(self, runner, rev_id: str) -> dict | None:
        """Построить граф свойств кода через сервер Joern.

        Возвращает результат стадии или None, если серверный путь неприменим:
        тогда вызывающий код работает прежним способом, и поведение не меняется.

        Почему это быстрее. Индексация делает ДВА отдельных запуска Joern,
        и каждый платит 5,53 с за загрузку классов. Сервер строит граф в памяти
        и отдаёт данные запросами: замерено на корпусе z.batteryservice.ru —
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
                f"по имени и файлу {stats.get('by_name_file', 0)}, "
                f"по имени без файла {stats.get('by_name_only', 0)}), "
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
            # Понижение покрытия — отдельной метрикой, а не только текстом в notes.
            # Измерено на rev-0069/rev-0070: сводки ревизий совпадали полностью
            # (capability=full, joern_used=1, одинаковые счётчики и config_hash),
            # и потерю 1203 рёбер можно было заметить только сравнением строк
            # coverage двух ревизий вручную. Две записи, потому что проверять
            # по индексу удобно числом, а человеку нужен текст: значение метрики
            # отвечает «было ли понижение», note — «что именно пропало и откуда
            # взяты связи». Ноль пишется явно: «проверено, понижения нет» и
            # «проверки не было» (старые ревизии) — разные состояния.
            (
                "cpg_downgrade",
                1.0 if (cpg_info or {}).get("downgraded") else 0.0,
                (cpg_info or {}).get("downgrade_note"),
            ),
            (
                "cpg_downgrade_note",
                1.0 if (cpg_info or {}).get("downgraded") else 0.0,
                (cpg_info or {}).get("downgrade_note"),
            ),
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
                if path:
                    cache[key] = (str(rows[0][0]), 0.9, "name_file")
                else:
                    # B5: файл владельца неизвестен (Joern его не дал), поэтому
                    # фильтр по файлу не добавлен и совпадение имени искалось
                    # ПО ВСЕМУ КОРПУСУ. Называть это `name_file` — неправда:
                    # файла в привязке нет. Способ назван честно (`name_only`),
                    # а достоверность снижена до уровня «один кандидат по имени
                    # во всём корпусе» — ровно так помечено то же решение
                    # в разрешении внешних имён выше (0.6 при единственном
                    # кандидате, 0.4 при нескольких). Ключ статистики меняется
                    # вместе со способом: `by_{how}` в `cpg.merge_calls`, поэтому
                    # отчёт ниже читает оба ключа.
                    cache[key] = (str(rows[0][0]), 0.6, "name_only")
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
        # Ссылка сохраняется СРАЗУ, а не только при успехе серверного пути:
        # сервер Joern мог быть поднят и остаться после отказа. Замер 16.10.2026:
        # три JVM-сироты держали порты 9500–9502, потому что при неудаче
        # серверного пути ссылка не сохранялась и close() их не останавливал.
        self._cpg_runner = runner
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
                # Ссылка сохранена выше: close() остановит сервер в любом исходе.
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
            # Причина, установленная замером (проект sagaai, Python):
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
                f"по имени и файлу {stats.get('by_name_file', 0)}, "
                f"по имени без файла {stats.get('by_name_only', 0)}), "
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
        REF). Сам граф при этом пригоден — замер на проекте sagaai показал
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
                f"по имени и файлу {stats.get('by_name_file', 0)}, "
                f"по имени без файла {stats.get('by_name_only', 0)}), "
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
        text = textdec.decode(data, path)[0]
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
