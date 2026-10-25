"""Проверка: есть ли в Python-графе вызовы внутри функций.

Запрос RAGRAF отдаёт ВСЕ вызовы с владельцем `<module>` — то есть вызовы
приписаны модулю, а не функции. Надо понять, действительно ли Python-граф
не связывает вызовы с функциями.

Проверяется скриптами с `println`: они пишут ответ в stdout, и его видно
без разбора JSON — так надёжнее, потому что путь с `ujson` и файлом
вывода уже давал сбои.

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

cfg = load_config(REPO / "config" / "sagaai.yaml")
runner = cpg_mod.JoernRunner(cfg)
scripts = cpg_mod.find_joern_scripts(cfg)
joern = scripts.get("joern")

files = sorted(runner.workdir.glob("rev-*.cpg.bin"),
               key=lambda p: -p.stat().st_size)
cpg_path = files[0]
print(f"  CPG: {cpg_path.name}")

QUERIES = [
    ("методов всего",
     'println("METHODS=" + cpg.method.size)'),
    ("модулей",
     'println("MODULES=" + cpg.method.name("<module>").size)'),
    ("функций (не модуль)",
     'println("FUNCS=" + cpg.method.filterNot(_.name == "<module>").size)'),
    ("имена функций",
     'println("NAMES=" + cpg.method.filterNot(_.name == "<module>").name.take(8).l.mkString(", "))'),
    ("вызовов всего",
     'println("CALLS=" + cpg.call.size)'),
    ("вызовов вне модуля",
     'println("OUTSIDE=" + cpg.call.filter(_.method.name != "<module>").size)'),
    ("пример вызова вне модуля",
     'println("SAMPLE=" + cpg.call.filter(_.method.name != "<module>").map(c => c.name + " in " + c.method.name).take(5).l.mkString(" | "))'),
    ("файл вызова вне модуля",
     'println("FILE=" + cpg.call.filter(_.method.name != "<module>").map(c => c.file.name.headOption.getOrElse("?")).take(5).l.mkString(" | "))'),
]

for label, body in QUERIES:
    script_text = (
        "@main def main(cpgFile: String, outFile: String): Unit = {\n"
        "  importCpg(cpgFile)\n"
        f"  {body}\n"
        "}\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".sc", delete=False,
                                     encoding="utf-8", newline="\n") as fh:
        fh.write(script_text)
        script_file = pathlib.Path(fh.name)
    out_file = runner.workdir / "probe_out.txt"
    try:
        cmd = [str(joern), "--script", str(script_file),
               f"--param", f"cpgFile={cpg_path}",
               f"--param", f"outFile={out_file}"]
        proc = subprocess.run(
            cpg_mod._shell_cmd(cmd, joern), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900,
            cwd=str(runner.workdir), env=cpg_mod.java_env(cfg),
        )
        text = (proc.stdout or "") + (proc.stderr or "")
        answer = "(нет ответа)"
        for ln in text.splitlines():
            s = ln.strip()
            if any(s.startswith(p) for p in
                   ("METHODS=", "MODULES=", "FUNCS=", "NAMES=", "CALLS=",
                    "OUTSIDE=", "SAMPLE=", "FILE=")):
                answer = s[:150]
                break
        print(f"  {label:<28} {answer}")
    except subprocess.TimeoutExpired:
        print(f"  {label:<28} таймаут")
    finally:
        script_file.unlink(missing_ok=True)

print()
print("=" * 78)
print("ВЫВОД")
print("=" * 78)
print("""
  Ответы показывают главное:
    * MODULES и FUNCS — есть ли в графе функции кроме модулей;
    * OUTSIDE — сколько вызовов лежит ВНЕ модуля (внутри функций).

  Если OUTSIDE больше нуля, а запрос RAGRAF отдаёт одни модули — дефект
  в запросе, и его надо исправить (для Python брать c.method.fullName
  из вызовов внутри функций).

  Если OUTSIDE равно нулю — Python-фронтенд Joern действительно не связал
  вызовы с функциями, и граф вызовов по Python построить нельзя.
""")
