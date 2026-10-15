"""Проверка графовых алгоритмов на разрежённых матрицах.

Стиль файла совпадает с остальными проверками проекта: самостоятельный скрипт с
собственной функцией `check`, а не pytest-функции (штатной конфигурации pytest в
репозитории нет).

Что закрепляется. Модуль `graph_algo` один раз уже ошибся в направлении обхода:
матрица смежности «источник → приёмник», умноженная на вектор, даёт
ПРЕДШЕСТВЕННИКОВ, а не последователей. Ошибка не только разворачивала ответ, но и
ломала сохранение массы в PageRank: деление на исходящую степень при движении
против рёбер давало суммы по столбцам, отличные от единицы, и сумма оценок
уходила от единицы (замерено: 2,011 вместо 1,0 при 1867 узлах). Второй дефект —
обёртка подавления предупреждений входила в контекст дважды и роняла расчёт
целиком (`RuntimeError: Cannot enter catch_warnings() twice`).

Оба дефекта ловятся здесь на графе из нескольких узлов, без обращения к корпусу,
поэтому проверка быстрая и не зависит от состояния индексов.

Ничего не меняет: считает в памяти.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf import graph_algo  # noqa: E402

PASS = 0
FAIL = 0
SKIP = 0

# Доступность матричного вычислителя: выставляется в main(). Без torch
# проверять направление обхода, сохранение массы и ранжирование не на чем.
TORCH_OK = False


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


def make_graph(node_ids: list[str], edges: list[tuple[str, str]],
               paths: dict[str, str] | None = None) -> graph_algo.GraphArrays:
    """Собрать граф в памяти, без обращения к хранилищу."""
    position = {nid: i for i, nid in enumerate(sorted(node_ids))}
    ordered = sorted(node_ids)
    src = [position[a] for a, b in edges]
    dst = [position[b] for a, b in edges]
    return graph_algo.GraphArrays(
        node_ids=ordered,
        position=position,
        src=src,
        dst=dst,
        kinds=["calls"] * len(edges),
        paths={nid: p for nid, p in (paths or {}).items() if nid in position},
    )


class _Cfg:
    """Минимальный стенд конфигурации: модулю нужны только эти два поля."""

    class _Index:
        graph_gpu = False
        graph_gpu_min_nodes = 5000

    index = _Index()


def brute_force_neighbours(arrays: graph_algo.GraphArrays, node: str, reverse: bool) -> set[str]:
    """Эталон: соседи по списку рёбер, без матриц и без torch.

    Если реализация на матрицах разойдётся с этим эталоном — виноват матричный
    путь, а не эталон: здесь нет ни разрежённой арифметики, ни направлений.
    """
    out: set[str] = set()
    for a, b in zip(arrays.src, arrays.dst):
        if reverse and arrays.node_ids[b] == node:
            out.add(arrays.node_ids[a])
        if not reverse and arrays.node_ids[a] == node:
            out.add(arrays.node_ids[b])
    out.discard(node)
    return out


def check_calc(condition: bool, title: str, detail: str = "") -> None:
    """Проверка матричного пути: без torch она НЕ проверена, а не провалена.

    torch не объявлен в зависимостях проекта (AGENTS.md, ловушка 8), поэтому
    на машине без него матричного вычислителя нет вовсе: `pagerank` и `expand`
    честно отдают причину и пустые оценки. Прежняя редакция всё равно
    требовала результаты матричного пути и давала 9 провалов на КАЖДОМ
    прогоне — «красная проверка, которая всегда красная, не проверяет ничего»
    (тот же вывод записан в tests/test_report_facts.py). Сам отказ проверяется
    отдельно: причина названа в секции 1 и в состоянии секции 8.
    """
    global SKIP
    if not TORCH_OK:
        SKIP += 1
        print(f"  [ПРОП ] {title} — не проверено: torch недоступен")
        return
    check(condition, title, detail)


def main() -> int:
    print("=== 1. Импорт и доступность вычислителя ===")
    check(hasattr(graph_algo, "pagerank"), "модуль импортируется")
    global TORCH_OK
    torch_ok = graph_algo._torch()[0] is not None
    TORCH_OK = torch_ok
    print(f"  torch доступен: {torch_ok}")
    if not torch_ok:
        print("  примечание: torch отсутствует — проверяются только отказы")

    print("\n=== 2. Направление обхода совпадает с эталоном ===")
    nodes = ["a", "b", "c", "d"]
    edges = [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")]
    g = make_graph(nodes, edges)
    ok_all = True
    for node in nodes:
        for reverse in (False, True):
            got, _ = graph_algo.expand(g, [g.position[node]], _Cfg(), reverse=reverse)
            got_ids = {g.node_ids[i] for i in got}
            want = brute_force_neighbours(g, node, reverse)
            if got_ids != want:
                ok_all = False
                print(f"      {node} reverse={reverse}: получено {sorted(got_ids)}"
                      f" ожидалось {sorted(want)}")
    check_calc(ok_all, "обход совпадает с эталоном в обоих направлениях")

    # Явная проверка семантики, ради которой всё затевалось: a вызывает b и c,
    # значит последователи a — это {b, c}, а предшественники a — пусто.
    fwd, _ = graph_algo.expand(g, [g.position["a"]], _Cfg(), reverse=False)
    bwd, _ = graph_algo.expand(g, [g.position["a"]], _Cfg(), reverse=True)
    check_calc({g.node_ids[i] for i in fwd} == {"b", "c"},
          "исходящие связи a = {b, c}", str([g.node_ids[i] for i in fwd]))
    check_calc({g.node_ids[i] for i in bwd} == set(),
          "входящих связей у a нет", str([g.node_ids[i] for i in bwd]))
    # И зеркально для d: на него ведут b и c.
    pre_d, _ = graph_algo.expand(g, [g.position["d"]], _Cfg(), reverse=True)
    check_calc({g.node_ids[i] for i in pre_d} == {"b", "c"},
          "предшественники d = {b, c}", str([g.node_ids[i] for i in pre_d]))

    print("\n=== 3. Сохранение массы (сумма оценок равна единице) ===")
    scores, st = graph_algo.pagerank(g, _Cfg())
    check_calc(abs(sum(scores) - 1.0) < 1e-6, "сумма оценок равна 1",
          f"получено {sum(scores)!r}")
    check_calc(len(scores) == g.n, "оценка на каждый узел",
          f"{len(scores)} против {g.n}")

    # Висячий узел (d никуда не ведёт) не должен уводить массу из графа.
    check_calc(abs(sum(scores) - 1.0) < 1e-6, "висячий узел не теряет массу",
          f"получено {sum(scores)!r}")

    print("\n=== 4. Воспроизводимость ===")
    s1, _ = graph_algo.pagerank(g, _Cfg())
    s2, _ = graph_algo.pagerank(g, _Cfg())
    check_calc(s1 == s2, "два вызова дают одинаковый результат")

    print("\n=== 5. Ранжирование файлов ===")
    nodes2 = ["f1:def", "f2:def", "f3:def"]
    edges2 = [("f1:def", "f2:def"), ("f3:def", "f2:def")]
    g2 = make_graph(nodes2, edges2, paths={"f1:def": "a.py", "f2:def": "b.py", "f3:def": "c.py"})
    sc2, _ = graph_algo.pagerank(g2, _Cfg())
    ranked = graph_algo.centrality_by_file(g2, sc2)
    check_calc(len(ranked) == 3, "ранжированы все файлы с символами", str(ranked))
    check_calc(ranked[0][0] == "b.py", "первым файл, на который ссылаются",
          str(ranked))
    # Ничья разрешается путём: порядок обязан быть воспроизводимым.
    g3 = make_graph(["x:a", "y:a"], [], paths={"x:a": "zz.py", "y:a": "aa.py"})
    sc3, _ = graph_algo.pagerank(g3, _Cfg())
    r3 = graph_algo.centrality_by_file(g3, sc3)
    check_calc([p for p, _ in r3] == ["aa.py", "zz.py"],
          "ничья разрешается путём (детерминизм)", str(r3))

    print("\n=== 6. Отсутствие узлов и рёбер не роняет расчёт ===")
    empty = make_graph([], [])
    sc_e, st_e = graph_algo.pagerank(empty, _Cfg())
    check_calc(sc_e == [], "пустой граф даёт пустой результат", str(sc_e))
    only_nodes = make_graph(["a", "b"], [])
    sc_n, _ = graph_algo.pagerank(only_nodes, _Cfg())
    check_calc(len(sc_n) == 2 and abs(sum(sc_n) - 1.0) < 1e-6,
          "граф без рёбер: оценки равны и нормированы", str(sc_n))
    step, _ = graph_algo.expand(only_nodes, [], _Cfg())
    check_calc(step == [], "пустой фронт даёт пустой шаг", str(step))

    print("\n=== 7. Выбор вычислителя по настройке ===")
    scores_cpu, st_cpu = graph_algo.pagerank(g, _Cfg())
    check_calc(st_cpu.backend in ("torch-cpu", "none"), "при выключенной настройке — процессор",
          st_cpu.backend)

    class _CfgGpu(_Cfg):
        class _Index:
            graph_gpu = True
            graph_gpu_min_nodes = 1
        index = _Index()

    _, st_gpu = graph_algo.pagerank(g, _CfgGpu())
    gpu_ok, _, _ = graph_algo.gpu_available()
    if gpu_ok:
        check_calc(st_gpu.backend == "torch-gpu" and st_gpu.device == "cuda:0",
              "с включённой настройкой и низким порогом — карта",
              f"{st_gpu.backend}/{st_gpu.device}")
        check_calc(bool(st_gpu.notes), "причина выбора записана", str(st_gpu.notes))
    else:
        check_calc(st_gpu.backend == "torch-cpu",
              "без карты настройка не роняет расчёт (деградация)", st_gpu.backend)

    # Порог окупаемости обязан уважаться: он не декорация.
    class _CfgHigh(_Cfg):
        class _Index:
            graph_gpu = True
            graph_gpu_min_nodes = 10_000_000
        index = _Index()

    _, st_high = graph_algo.pagerank(g, _CfgHigh())
    check_calc(st_high.backend == "torch-cpu",
          "граф меньше порога — считается на процессоре", st_high.backend)

    print("\n=== 8. Состояние вычисления описывает себя ===")
    fields = ("backend", "device", "reason", "nodes", "edges", "ms", "iterations")
    d = st_cpu.to_dict()
    check(all(f in d for f in fields), "поля состояния на месте", str(sorted(d)))
    check(d["iterations"] == graph_algo.PAGERANK_ITERATIONS,
          "число итераций записано", str(d["iterations"]))

    print("\n=== 9. Граф читается из хранилища ===")
    import sqlite3

    tmp = Path(tempfile.mkdtemp(prefix="graph_algo_"))
    try:
        db = tmp / "graph.sqlite"
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE node (rev_id TEXT, node_id TEXT, kind TEXT, name TEXT,
                               fqn TEXT, path TEXT, start_line INT, end_line INT,
                               signature TEXT, props TEXT, layer TEXT);
            CREATE TABLE edge (rev_id TEXT, edge_uid TEXT, src TEXT, dst TEXT,
                               kind TEXT, provenance TEXT, evidence_path TEXT,
                               evidence_line INT, confidence REAL, sources TEXT, props TEXT);
            """
        )
        conn.execute("INSERT INTO node VALUES ('rev-0001','n1','function','a','a','x.py',1,2,'','','src')")
        conn.execute("INSERT INTO node VALUES ('rev-0001','n2','function','b','b','y.py',3,4,'','','src')")
        conn.execute("INSERT INTO node VALUES ('rev-0001','n3','file','z','z','z.py',1,1,'','','src')")
        conn.execute("INSERT INTO edge VALUES ('rev-0001','e1','n1','n2','calls','stated','x.py:2',2,1.0,'','')")
        # Ребро с внешним концом: узла n9 нет, ребро обязано отпасть.
        conn.execute("INSERT INTO edge VALUES ('rev-0001','e2','n1','n9','calls','stated','x.py:2',2,1.0,'','')")
        # Ребро петлёй: матрица требует различать концы, петля отбрасывается.
        conn.execute("INSERT INTO edge VALUES ('rev-0001','e3','n1','n1','calls','stated','x.py:1',1,1.0,'','')")
        conn.commit()
        arrays = graph_algo.load_graph(conn, "rev-0001")
        check(arrays.n == 3, "узлы прочитаны", str(arrays.n))
        check(arrays.m == 1, "внешние концы и петли отброшены", str(arrays.m))
        check(arrays.paths.get("n1") == "x.py", "путь символьного узла прочитан",
              str(arrays.paths))
        check("n3" not in arrays.paths, "узел-файл в ранжирование файлов не попадает",
              str(sorted(arrays.paths)))
        conn.close()
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}, не проверено: {SKIP}")
    if SKIP:
        print(f"  не проверено свойств: {SKIP} — нужен torch (матричный путь)")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
