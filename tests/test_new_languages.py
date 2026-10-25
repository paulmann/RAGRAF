"""Новые языки: маршрутизация, разбор по tags-запросу, честный текстовый путь.

Что закрепляется
    1. Каждый язык, объявленный «с разбором», действительно даёт символы на
       пробном файле. Это главная проверка: ошибка в имени захвата tags-запроса
       не даёт исключения — она даёт ПУСТОЙ результат, и файл молча остаётся
       без структуры.
    2. Языки без разборчика честно попадают в текстовый путь: файл виден
       поиску, узлов не даёт.
    3. Имена файлов без расширения (Dockerfile, Makefile, CMakeLists.txt)
       распознаются: раньше такие файлы не индексировались вовсе.
    4. Новых языков не меньше двадцати — это и был запрос владельца.

Запуск: python tests/test_new_languages.py
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import config, formats  # noqa: E402
from ragraf.extract.css_html import MultiExtractor  # noqa: E402
from ragraf.extract.generic import PROFILES  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


print("=" * 78)
print("1. ЯЗЫКИ С РАЗБОРОМ: СИМВОЛЫ ЕСТЬ, А НЕ «ПУСТО И БЕЗ ОШИБОК»")
print("=" * 78)

# Пробные файлы минимальны, но содержат объявление функции и класса: если
# tags-запрос не сработал, символов не будет и проверка это покажет.
SNIPPETS = {
    "kotlin": ("kt", "class Greeter(val n: String) {\n"
                     "  fun hello(x: Int): String { return \"hi\" }\n}\n"
                     "fun top(x: Int) = x * 2\n"),
    "swift": ("swift", "struct P { var x: Int }\n"
                       "class Box {\n  func open() -> Int { return 1 }\n}\n"),
    "scala": ("scala", "object Main {\n"
                       "  def greet(n: Int): String = helper(n)\n"
                       "  private def helper(n: Int) = \"x\"\n}\n"),
    "dart": ("dart", "class G {\n  String hello(int n) { return n.toString(); }\n}\n"
                     "int twice(int x) => x * 2;\n"),
    "r": ("r", "f <- function(x) {\n  x + 1\n}\ng <- function(y) y * 2\n"),
    "fsharp": ("fs", "module Demo\nlet twice x = x * 2\nlet add a b = a + b\n"),
    "ocaml": ("ml", "let twice x = x * 2\nlet fact n = n - 1\n"),
    "solidity": ("sol", "pragma solidity ^0.8.0;\ncontract Token {\n"
                        "  function transfer(address to, uint v) public returns (bool)"
                        " { return true; }\n}\n"),
    "fortran": ("f90", "subroutine hello(x)\n  integer :: x\nend subroutine hello\n"),
    "matlab": ("m", "function y = twice(x)\n  y = x * 2;\nend\n"),
    "elm": ("elm", "module Main exposing (..)\n\ntwice : Int -> Int\ntwice x = x * 2\n"),
    "lisp": ("lisp", "(defun twice (x) (* x 2))\n(defclass shape () ())\n"),
    "rust": ("rs", "fn add(a: i32, b: i32) -> i32 { a + b }\nstruct P { x: i32 }\n"),
}

mx = MultiExtractor()
for lang, (ext, source) in SNIPPETS.items():
    profile = PROFILES.get(lang)
    check(profile is not None, f"{lang}: профиль объявлен")
    if profile is None:
        continue
    check(profile.tags, f"{lang}: разбор через tags-запрос", "ожидался tags=True")
    res = mx.extract(f"sample.{ext}", source.encode("utf-8"))
    symbols = [s for s in res.symbols if s.kind not in ("file", "external")]
    check(bool(symbols), f"{lang}: найдены символы",
          f"статус {res.parse_status}, примечание {res.parse_note}")
    check(res.parse_status in ("ok", "partial"),
          f"{lang}: разбор не помечен как проваленный", res.parse_status)
    if symbols:
        check(all(s.start_line >= 1 and s.end_line >= s.start_line for s in symbols),
              f"{lang}: у символов есть диапазон строк")

print()
print("=" * 78)
print("2. ЯЗЫКИ БЕЗ РАЗБОРА: ВИДНЫ ПОИСКУ, УЗЛОВ НЕ ДАЮТ")
print("=" * 78)

TEXT_ONLY = {
    ".jl": "julia", ".ex": "elixir", ".erl": "erlang", ".hs": "haskell",
    ".clj": "clojure", ".groovy": "groovy", ".vb": "vb", ".pas": "pascal",
    ".mm": "objc", ".zig": "zig", ".nim": "nim", ".cr": "crystal",
    ".tcl": "tcl", ".prolog": "prolog", ".adb": "ada", ".v": "verilog",
    ".vhd": "vhdl", ".asm": "asm", ".tf": "hcl", ".scss": "scss",
    ".vue": "vue", ".gd": "gdscript", ".vala": "vala",
}
for ext, want in TEXT_ONLY.items():
    got = config.ProjectConfig.language_of_static(f"a{ext}")
    check(got == want, f"{ext}: язык {want}", f"получено {got!r}")
    check(want not in PROFILES, f"{want}: разбора деревом нет (честный текст)")

sample = mx.extract("a.jl", "function add(a, b)\n  a + b\nend\n".encode("utf-8"))
nodes = [s for s in sample.symbols if s.kind not in ("file", "external")]
check(not nodes, "julia: узлов не выдумывается", f"получено {len(nodes)}")

print()
print("=" * 78)
print("3. ФАЙЛЫ БЕЗ РАСШИРЕНИЯ")
print("=" * 78)

for name, want in (("Dockerfile", "dockerfile"), ("Makefile", "make"),
                   ("GNUmakefile", "make"), ("CMakeLists.txt", "cmake"),
                   ("deploy/Dockerfile", "dockerfile")):
    got = config.ProjectConfig.language_of_static(name)
    check(got == want, f"{name}: язык {want}", f"получено {got!r}")

res = mx.extract("Dockerfile", b"FROM python:3.13\nRUN pip install foo\n")
check(res.lang == "dockerfile", "Dockerfile: разбор получил верный язык", res.lang)

print()
print("=" * 78)
print("4. СОГЛАСОВАННОСТЬ: TREE_LANGS, ПРОФИЛИ И РАСШИРЕНИЯ")
print("=" * 78)

missing_profile = sorted(set(config.TREE_LANGS) - set(PROFILES)
                         - {"php", "css", "html"})
check(not missing_profile, "у каждого языка с разбором есть профиль",
      f"без профиля: {missing_profile}")

extra_profile = sorted(set(PROFILES) - set(config.TREE_LANGS))
check(not extra_profile, "профиль не объявлен для языка вне TREE_LANGS",
      f"лишние: {extra_profile}")

bad_ext = [f"{ext}->{lang}" for ext, lang in config.PARSE_LANGS_EXT.items()
           if lang not in PROFILES and lang not in ("php", "css", "html")]
check(not bad_ext, "расширения с разбором ведут к известному языку",
      f"проблемные: {bad_ext}")

print()
print("=" * 78)
print("5. СКОЛЬКО ЯЗЫКОВ ДОБАВЛЕНО")
print("=" * 78)

HISTORICAL = {"php", "css", "html", "javascript", "typescript", "python",
              "csharp", "perl", "bash", "powershell", "ruby", "lua", "go",
              "java", "c", "cpp"}
code_ids = {f.id for f in formats.FORMATS if f.group == "code"}
new_langs = sorted(code_ids - HISTORICAL)
check(len(new_langs) >= 20, "добавлено не меньше двадцати языков",
      f"получено {len(new_langs)}: {new_langs}")
print(f"  добавлено ({len(new_langs)}): {', '.join(new_langs)}")

print()
print(f"ИТОГ: {PASS} проверок пройдено, {FAIL} провалено")
sys.exit(1 if FAIL else 0)
