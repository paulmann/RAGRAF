"""Обход дефекта Joern: получить вызовы БЕЗ расчёта dataflow-оверлея.

Точная причина падения установлена по стеке:

    flatgraph.SchemaViolationException: OUT edge with label REF to an adjacent
    METHOD is mandatory, but not defined for this METHOD_REF node with seq=964
    ... в ReachingDefPass -> DdgGenerator.addReachingDefEdges

То есть падает НЕ экспорт, а РАСЧЁТ ОВЕРЛЕЯ ПОТОКА ДАННЫХ, который
`joern-export` запускает при загрузке графа. Python-фронтенд создаёт узлы
METHOD_REF без обязательного ребра REF — Joern считает такой граф
некорректным и прерывается.

Вывод из этого: если запрашивать вызовы через **CPGQL-скрипт**, а не через
экспорт, оверлей не понадобится. Тогда вызовы по Python можно получить.

Проверяется вызовом: загружается готовый Python-CPG и выполняются запросы
через `joern --script`. Заодно сравнивается с PHP: там должно работать.

Ничего не меняет.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf.config import load as load_config  # noqa: E402
from ragraf import cpg as cpg_mod  # noqa: E402

CASES = [
    ("sagaai (Python)", REPO / "config" / "sagaai.yaml"),
    ("z (PHP)", REPO / "config" / "z-batteryservice.yaml"),
]

# Запросы без обращения к оверлею: только узлы и рёбра графа.
#
# ВАЖНО про формат: скриптовый режим Joern требует `@main def` с параметром
# cpgFile — голый запрос не исполняется и даёт «None.get». Это установлено
# по коду RAGRAF (cpg.py: блок «Синтаксис скриптового режима»).
QUERIES = {
    "вызовов всего": 'println(cpg.call.size)',
    "методов всего": 'println(cpg.method.size)',
    "METHOD_REF всего": 'println(cpg.methodRef.size)',
    "METHOD_REF без REF": (
        'println(cpg.methodRef.filter(_.out("REF").isEmpty).size)'
    ),
    "пример вызова": (
        'println(cpg.call.map(c => c.name).take(5).l.mkString(", "))'
    ),
}


def wrap_script(body: str) -> str:
    """Обернуть запрос в скрипт с @main — как это делает RAGRAF.

    Без @main Joern сообщает «None.get»: это не ошибка графа, а неверный
    формат вызова. В скриптовом режиме Joern сам загружает CPG по параметру
    cpgFile, поэтому в теле используется cpg напрямую.
    """
    return (
        "@main def main(cpgFile: String, outFile: String): Unit = {\n"
        "  importCpg(cpgFile)\n"
        f"  {body}\n"
        "}\n"
    )

for title, config_path in CASES:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)
    cfg = load_config(config_path)
    runner = cpg_mod.JoernRunner(cfg)
    scripts = cpg_mod.find_joern_scripts(cfg)
    joern = scripts.get("joern")
    files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
                   key=lambda p: -p.stat().st_size)
    if not files or joern is None:
        print("  нет CPG или скрипта joern")
        continue
    cpg_path = files[0]
    print(f"  CPG: {cpg_path.name} ({cpg_path.stat().st_size / 1024 / 1024:.1f} МБ)")

    for label, query in QUERIES.items():
        # Скрипт пишется во временный файл: joern принимает только файл.
        # Тело оборачивается в @main: иначе Joern отвечает «None.get».
        with tempfile.NamedTemporaryFile(
            "w", suffix=".sc", delete=False, encoding="utf-8", newline="\n"
        ) as fh:
            fh.write(wrap_script(query))
            script_file = pathlib.Path(fh.name)
        out_file = runner.workdir / "probe_out.txt"
        try:
            # ВАЖНО про передачу параметров: Joern требует один аргумент
            # вида `имя=значение`, а не два (`--param имя=значение`). Первая
            # попытка передавала значение отдельно и получала
            # «unable to parse param input cpgFile».
            proc = subprocess.run(
                cpg_mod._shell_cmd(
                    [str(joern), "--script", str(script_file),
                     f"--param", f"cpgFile={cpg_path}",
                     f"--param", f"outFile={out_file}"],
                    joern,
                ),
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=900, cwd=str(runner.workdir),
                env=cpg_mod.java_env(cfg),
            )
            out = ((proc.stdout or "") + (proc.stderr or "")).strip()
            crashed = ("SchemaViolationException" in out
                       or "NoSuchElement" in out or "None.get" in out)
            # Ищется строка с ответом: она не начинается с отступа
            # и не является служебным сообщением Joern.
            answer = "(нет ответа)"
            for ln in out.splitlines():
                s = ln.strip()
                if not s or s.startswith(("[", "WARN", "INFO", "CPG",
                                          "imported", "at ", "Exception")):
                    continue
                if "SchemaViolation" in s or "NoSuchElement" in s:
                    continue
                answer = s[:120]
                break
            mark = "ОШИБКА" if crashed else "ок"
            print(f"  {label:<22} [{mark}] {answer}")
        except subprocess.TimeoutExpired:
            print(f"  {label:<22} [таймаут]")
        finally:
            script_file.unlink(missing_ok=True)
            out_file.unlink(missing_ok=True)

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Проверка показывает, можно ли получить вызовы из Python-графа через
  CPGQL-запросы — без экспорта и без расчёта оверлея потока данных.

  Если да — RAGRAF надо переключить на этот путь для Python-корпусов:
  вызовы будут доступны, а операция slice заработает по графу вызовов.

  Если нет — остаётся честно зафиксировать ограничение: граф вызовов
  по Python не строится из-за дефекта фронтенда Joern, RAGRAF работает
  по данным разбора деревом.
""")
