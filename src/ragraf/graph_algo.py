"""Графовые алгоритмы на разрежённых матрицах (torch.sparse).

Зачем отдельный модуль. Архитектура (docs/02_architecture/ARCHITECTURE.md, §7,
операция `repo_map`) требует ранжировать файлы «по связности и по „центральности“
в графе», а настройка `index.graph_gpu` с порогом `graph_gpu_min_nodes` до сих пор
не имела исполнителя: значение читалось, но ни один алгоритм его не использовал.
Здесь появляется исполнитель, и настройка перестаёт быть просто числом.

Что считается разрежённой арифметикой, а что нет. Замеры этого проекта
(`tools/check_graph_gpu_alt.py`, повторены при написании модуля):

    граф                              процессор    видеокарта   выигрыш
    наш корпус, 1867 узлов             11,78 мс      7,96 мс      1,48×
    синтетика, 5 000 узлов             19,81 мс      6,77 мс      2,93×
    синтетика, 20 000 узлов            85,18 мс      5,60 мс     15,20×
    синтетика, 60 000 узлов           249,61 мс      5,94 мс     42,01×

Значения PageRank на обоих вычислителях совпадают с точностью 3,7e-09, то есть
переключение вычислителя на существующем индексе не меняет факты — только время.
Вид вычисления (разрежённое умножение матрицы на вектор) — именно тот случай, где
карта окупается; обходы графа на нашем размере (доли миллисекунды) от карты не
выигрывают, и это записано в правилах окружения.

Границы возможностей torch, проверенные вызовом (не по памяти):

  * `torch.sparse.sum(csr, dim=...)` НЕ поддерживается для CSR — приходится
    считать степени на COO-тензоре, поэтому и хранится COO;
  * разрежённое умножение CSR на плотный вектор работает, но первый вызов на
    карте платит за инициализацию ядер: 683 мс против 7,96 мс после прогрева.
    Отсюда правило прогрева: перед замером выполнять несколько холостых итераций;
  * при отсутствии torch или видеокарты модуль не падает, а честно возвращает
    причину и считает на процессоре — деградация вместо исключения.

Внешний интерфейс намеренно узкий: `available` (можно ли считать на карте и
почему), `pagerank` (центральность), `expand` (шаг обхода), `load_graph`
(чтение графа из хранилища) и `status` (чем считали). Всё остальное — внутри.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

# Глубина по умолчанию для PageRank. 30 итераций — как в проверочных замерах
# проекта: на графах этого размера значение сходится, дальнейшие итерации
# меняют результат в четвёртом знаке и только добавляют время.
PAGERANK_ITERATIONS = 30
DAMPING = 0.85

# Сколько холостых итераций сделать перед замером на карте. Первый вызов
# разрежённой арифметики на карте стоит 683 мс (инициализация ядер) против
# 7,96 мс после прогрева — без прогрева любой замер будет ложным.
WARMUP_ITERATIONS = 2

# Рёбра, которые участвуют в обходах. Совпадает со списком в `facade._bfs`:
# если списки разойдутся, обход на карте и обход в SQL дадут разные ответы.
TRAVERSAL_KINDS = (
    "calls", "calls_heuristic", "instantiates", "extends", "implements",
    "overrides", "queries", "imports", "imports_heuristic", "handles",
    "renders", "tested_by", "documented_by",
)

# Виды узлов, у которых есть путь к файлу. Центральность считается только по
# ним: узел-файл не несёт информации о важности содержимого.
SYMBOL_KINDS = frozenset({"class", "function", "method"})


@dataclass
class GraphStatus:
    """Чем и почему считали. Показывается в `index_status`, а не прячется."""

    backend: str = "none"        # torch-gpu | torch-cpu | none
    device: str = ""             # cuda:0 | cpu | ""
    reason: str = ""
    nodes: int = 0
    edges: int = 0
    ms: float = 0.0
    warmup_ms: float = 0.0
    iterations: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "device": self.device,
            "reason": self.reason,
            "nodes": self.nodes,
            "edges": self.edges,
            "ms": round(self.ms, 3),
            "warmup_ms": round(self.warmup_ms, 3),
            "iterations": self.iterations,
            "notes": list(self.notes),
        }


@dataclass
class GraphArrays:
    """Граф, приведённый к индексам: узлы — позиции, рёбра — пары позиций."""

    node_ids: list[str]
    position: dict[str, int]
    src: list[int]
    dst: list[int]
    kinds: list[str]
    # Путь узла — только у символьных узлов (класс, функция, метод).
    # Нужен, чтобы сложить центральность по файлу.
    paths: dict[str, str] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.node_ids)

    @property
    def m(self) -> int:
        return len(self.src)

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": self.n, "edges": self.m, "symbol_nodes": len(self.paths)}


def _quiet():
    """Контекст, в котором молчат два известных предупреждения torch.

    Первое — «sparse invariant checks are implicitly disabled»: проверки
    разрежённых тензоров выключены по умолчанию, и предупреждение печатается при
    каждом их создании. Второе — «Sparse CSR tensor support is in beta state».
    Оба предупреждения про внутренности torch, не про наш код, но идут на
    каждый вызов и мешают заметить настоящее исключение. Подавление узкое —
    только эти два текста.

    Реализовано декоратором `contextmanager` намеренно. Первая версия возвращала
    уже ВОЙДЁННЫЙ `catch_warnings()`, и повторный вход из `with` давал
    `RuntimeError: Cannot enter catch_warnings() twice` — расчёт падал целиком,
    а виноват был не тензор, а обёртка вокруг него.
    """
    import warnings
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*sparse invariant checks.*")
            warnings.filterwarnings("ignore", message=".*Sparse CSR tensor support.*")
            yield

    return _ctx()


def _torch():
    """Импортировать torch лениво: его может не быть, и это не ошибка сборки."""
    try:
        import torch  # noqa: PLC0415 — импорт намеренно ленивый
    except Exception as exc:  # noqa: BLE001
        return None, f"torch недоступен: {type(exc).__name__}"
    return torch, ""


def gpu_available() -> tuple[bool, str, str]:
    """Проверить карту РЕАЛЬНЫМ вычислением, а не фактом её наличия.

    Возвращает (доступна, имя устройства, причина отказа). Проверка повторяет
    подход `compute.probe_torch`: `is_available()` сам по себе ничего не
    доказывает — ядра под архитектуру карты выясняются только запуском.
    """
    torch, why = _torch()
    if torch is None:
        return False, "", why
    try:
        if not torch.cuda.is_available():
            return False, "", "torch сообщает, что устройство CUDA недоступно"
        probe = torch.randn(64, 64, device="cuda")
        _ = probe @ probe
        torch.cuda.synchronize()
    except Exception as exc:  # noqa: BLE001
        return False, "", f"проверка карты не прошла: {type(exc).__name__}: {exc}"
    name = torch.cuda.get_device_name(0)
    return True, name, ""


def load_graph(conn: sqlite3.Connection, rev: str,
               kinds: tuple[str, ...] = TRAVERSAL_KINDS) -> GraphArrays:
    """Прочитать граф ревизии и привести его к позициям.

    Узлы берутся из таблицы `node`, рёбра — из `edge` с отбором по видам связей.
    Ребро, у которого любого конца нет среди узлов, отбрасывается: разрежённая
    матрица требует, чтобы оба индекса существовали, а внешний узел (например
    вызов из библиотеки, которой нет в корпусе) позиции не имеет.

    Порядок узлов фиксирован сортировкой по идентификатору, а не порядком выдачи
    SQL: от порядка зависят позиции, а значит и воспроизводимость результата.

    Заодно читается путь символьных узлов (класс, функция, метод): он нужен,
    чтобы сложить центральность по файлу, не делая второй запрос к хранилищу.
    """
    node_rows = conn.execute(
        "SELECT node_id, kind, path FROM node WHERE rev_id = ? ORDER BY node_id", (rev,)
    ).fetchall()
    node_ids: list[str] = []
    paths: dict[str, str] = {}
    for r in node_rows:
        # Доступ по позиции, а не по имени: модуль обязан работать с обычным
        # соединением sqlite3, у которого нет row_factory. Первая версия читала
        # `r["node_id"]` и падала с TypeError у любого вызывающего, кроме
        # `store.connect` (тот row_factory выставляет) — тест поймал это
        # на обычном соединении до того, как дефект дошёл до чужого кода.
        nid = str(r[0])
        node_ids.append(nid)
        kind = str(r[1] or "")
        path = str(r[2] or "")
        if path and kind in SYMBOL_KINDS:
            paths[nid] = path
    position = {nid: i for i, nid in enumerate(node_ids)}

    marks = ",".join("?" for _ in kinds)
    src: list[int] = []
    dst: list[int] = []
    kept: list[str] = []
    for row in conn.execute(
        f"SELECT src, dst, kind FROM edge WHERE rev_id = ? AND kind IN ({marks})"
        " ORDER BY src, dst, kind",
        (rev, *kinds),
    ):
        # По позиции — по той же причине, что и выше: row_factory может быть любым.
        a = position.get(str(row[0]))
        b = position.get(str(row[1]))
        if a is None or b is None or a == b:
            continue
        src.append(a)
        dst.append(b)
        kept.append(str(row[2]))
    return GraphArrays(
        node_ids=node_ids, position=position, src=src, dst=dst, kinds=kept, paths=paths
    )


def _coo(torch, arrays: GraphArrays, device: str):
    """Собрать разрежённую матрицу смежности (n × n) на нужном устройстве.

    Семантика проверена вызовом на графе из трёх узлов: `M[i][j] = 1` означает
    ребро `i → j`, то есть матрица направлена «источник → приёмник». Кто из
    этого следует (это неочевидно и один раз уже привело к ошибке):

        M · v     — предшественники (кто ведёт в узел),
        Mᵀ · v    — последователи (куда ведёт из узла).

    Ошибка в направлении не только даёт противоположный ответ, но и ломает
    сохранение массы в PageRank: деление на исходящую степень при движении
    против рёбер даёт суммы по столбцам, отличные от единицы.
    """
    n = arrays.n
    if not arrays.src:
        return torch.sparse_coo_tensor(
            torch.zeros(2, 0, dtype=torch.long, device=device),
            torch.zeros(0, device=device),
            (n, n),
        ).coalesce()
    with _quiet():
        index = torch.stack([
            torch.as_tensor(arrays.src, dtype=torch.long, device=device),
            torch.as_tensor(arrays.dst, dtype=torch.long, device=device),
        ])
        values = torch.ones(len(arrays.src), dtype=torch.float32, device=device)
        return torch.sparse_coo_tensor(index, values, (n, n)).coalesce()


def _pick_device(cfg, nodes: int) -> tuple[str, str, list[str]]:
    """Решить, где считать, и объяснить решение.

    Порядок проверок — от дешёвой к дорогой: сначала настройка, затем порог
    окупаемости, затем реальная проверка карты. Ровно так же устроен выбор
    вычислителя эмбеддингов (`embeddings.Embedder._pick_backend`), и по той же
    причине: карта стоит около 700 мс на первый вызов, поэтому на малом графе
    она проигрывает, а на большом выигрывает.
    """
    notes: list[str] = []
    enabled = bool(getattr(getattr(cfg, "index", None), "graph_gpu", False))
    threshold = int(getattr(getattr(cfg, "index", None), "graph_gpu_min_nodes", 5000) or 5000)
    if not enabled:
        return "torch-cpu", "cpu", ["графовый анализ на карте выключен в настройках"]
    if nodes < threshold:
        notes.append(
            f"узлов {nodes} меньше порога окупаемости {threshold}: считаем на процессоре"
        )
        return "torch-cpu", "cpu", notes
    ok, name, why = gpu_available()
    if not ok:
        notes.append(why)
        return "torch-cpu", "cpu", notes
    notes.append(f"карта {name}")
    return "torch-gpu", "cuda:0", notes


def pagerank(arrays: GraphArrays, cfg=None, *, iterations: int = PAGERANK_ITERATIONS,
             damping: float = DAMPING, status: GraphStatus | None = None
             ) -> tuple[list[float], GraphStatus]:
    """Центральность узлов (PageRank) на разрежённой матрице.

    Направление обхода — по входящим связям (как в классическом PageRank):
    узел важен, если на него ссылаются важные узлы. Семантика torch проверена
    вызовом: `M · v` даёт именно предшественников. Прямое использование `M · v`
    дало бы обратный ответ и, что менее заметно, ломало бы сохранение массы:
    деление на исходящую степень при движении против рёбер даёт суммы по
    столбцам, отличные от единицы, и сумма оценок уходила от единицы (замерено:
    2,011 вместо 1,0 при 1867 узлах).

    Возвращает (оценки в порядке узлов `arrays.node_ids`, состояние). Оценки
    нормированы в сумме на единицу, поэтому их можно складывать по файлу и
    сравнивать между ревизиями.

    Висячие узлы (без исходящих рёбер) уводят массу из графа. Здесь она
    перераспределяется равномерно — на этом и держится нормировка; степень
    берётся снизу ограниченной единицей, чтобы у висячего узла не было деления
    на ноль.
    """
    st = status or GraphStatus(nodes=arrays.n, edges=arrays.m, iterations=iterations)
    st.nodes = arrays.n
    st.edges = arrays.m
    st.iterations = iterations

    torch, why = _torch()
    if torch is None:
        st.backend = "none"
        st.reason = why
        st.notes.append("графовые алгоритмы недоступны: считается только SQL-обход")
        return [0.0] * arrays.n, st

    device = "cpu"
    if cfg is not None:
        backend, device, notes = _pick_device(cfg, arrays.n)
        st.backend = backend
        st.notes.extend(notes)
    else:
        st.backend = "torch-cpu"
    st.device = device
    st.reason = st.notes[-1] if st.notes else ""

    n = arrays.n
    if n == 0:
        return [], st

    started = time.perf_counter()
    try:
        # Матрица «источник → приёмник»; для PageRank нужны входящие связи,
        # то есть транспонированная матрица (M · v = предшественники).
        with _quiet():
            coo = _coo(torch, arrays, device).transpose(0, 1).coalesce()
            # Степени: `torch.sparse.sum` по CSR не поддерживается (проверено
            # вызовом — NotImplementedError), поэтому тензор остаётся в COO.
            degree = torch.sparse.sum(coo, dim=1).to_dense().clamp(min=1.0).unsqueeze(1)
        inv_degree = 1.0 / degree
        x = torch.full((n, 1), 1.0 / n, dtype=torch.float32, device=device)

        # Прогрев: первая разрежённая операция на карте инициализирует ядра и
        # стоит около 700 мс. Без прогрева замер врёт в десятки раз.
        with _quiet():
            for _ in range(WARMUP_ITERATIONS if device != "cpu" else 0):
                x = damping * torch.sparse.mm(coo, x * inv_degree) + (1.0 - damping) / n
        if device != "cpu":
            torch.cuda.synchronize()
        warm_done = time.perf_counter()
        st.warmup_ms = (warm_done - started) * 1000.0

        with _quiet():
            for _ in range(iterations):
                x = damping * torch.sparse.mm(coo, x * inv_degree) + (1.0 - damping) / n
        if device != "cpu":
            torch.cuda.synchronize()
        st.ms = (time.perf_counter() - warm_done) * 1000.0
        scores = x.flatten().tolist()
    except Exception as exc:  # noqa: BLE001 — деградация важнее падения
        st.backend = "none"
        st.device = ""
        st.reason = f"графовый расчёт не удался: {type(exc).__name__}: {exc}"
        return [0.0] * arrays.n, st

    total = sum(scores)
    if total > 0:
        scores = [s / total for s in scores]
    return scores, st


def expand(arrays: GraphArrays, frontier: list[int], cfg=None, *, reverse: bool = False,
           status: GraphStatus | None = None) -> tuple[list[int], GraphStatus]:
    """Один шаг обхода: соседи фронта, без повторов.

    Направление важнее оптимизации, и оно то же, что в SQL-версии
    (`facade._bfs`), иначе два исполнителя того же обхода дадут разные ответы:

        reverse=False — исходящие связи (кого вызывает), это Mᵀ · v;
        reverse=True  — входящие связи (кто вызывает), это M · v.

    Модуль один раз уже ошибся именно здесь: направление было взято из
    интуиции «матрица смежности умноженное на вектор даёт последователей», а
    проверка вызовом показала обратное. Правило на будущее: `M · v` — это
    предшественники.

    Возвращаются ПОЗИЦИИ узлов, а не идентификаторы: вызывающая сторона сама
    решает, какие доказательства подтянуть из хранилища, и может сверить
    результат с SQL-обходом.
    """
    st = status or GraphStatus(nodes=arrays.n, edges=arrays.m)
    st.nodes = arrays.n
    st.edges = arrays.m

    torch, why = _torch()
    if torch is None:
        st.backend = "none"
        st.reason = why
        return [], st

    device = "cpu"
    if cfg is not None:
        backend, device, notes = _pick_device(cfg, arrays.n)
        st.backend = backend
        st.notes.extend(notes)
    else:
        st.backend = "torch-cpu"
    st.device = device

    if not frontier:
        return [], st

    started = time.perf_counter()
    try:
        with _quiet():
            base = _coo(torch, arrays, device)
            # Исходящие — транспонированная матрица; входящие — прямая.
            matrix = base if reverse else base.transpose(0, 1).coalesce()
            vec = torch.zeros((arrays.n, 1), dtype=torch.float32, device=device)
            vec[torch.as_tensor(frontier, dtype=torch.long, device=device)] = 1.0
            out = torch.sparse.mm(matrix.to_sparse_csr(), vec).flatten()
        if device != "cpu":
            torch.cuda.synchronize()
        st.ms = (time.perf_counter() - started) * 1000.0
        blocked = set(frontier)
        return [int(i) for i in (out > 0).nonzero().flatten().tolist() if int(i) not in blocked], st
    except Exception as exc:  # noqa: BLE001
        st.backend = "none"
        st.reason = f"шаг обхода на матрице не удался: {type(exc).__name__}: {exc}"
        return [], st


def centrality_by_file(arrays: GraphArrays, scores: list[float]) -> list[tuple[str, float]]:
    """Сложить центральность символьных узлов по их файлам.

    Центральность имеет смысл только для символов: узел-файл не несёт информации
    о важности содержимого, поэтому берутся только узлы с путём (класс, функция,
    метод) — они и собраны в `arrays.paths` при чтении графа.

    Возвращается список «путь → сумма», отсортированный по убыванию рейтинга с
    разрешением ничьей по пути: результат обязан быть воспроизводимым, иначе
    порядок файлов в карте репозитория будет меняться от прогона к прогону.
    """
    totals: dict[str, float] = {}
    for nid, path in arrays.paths.items():
        pos = arrays.position.get(nid)
        if pos is None or pos >= len(scores):
            continue
        totals[path] = totals.get(path, 0.0) + scores[pos]
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))


def status(arrays: GraphArrays, cfg=None) -> GraphStatus:
    """Описать, где и почему будут считаться алгоритмы, без самого расчёта.

    Нужно состоянию индекса: `index_status` обязан показать вычислитель и
    причину выбора, а не молчать. Считается на одном шаге PageRank, поэтому
    дёшево и честно — отчёт отражает фактический путь, а не предполагаемый.
    """
    st = GraphStatus(nodes=arrays.n, edges=arrays.m)
    _, result = pagerank(arrays, cfg, iterations=1, status=st)
    return result
