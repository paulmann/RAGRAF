#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Контракт замера пользы: поля ответа и арифметика метрик.

Запуск: python tests/test_usefulness_contract.py

Зачем этот набор. Этап 13 добавил измерительный слой: «сколько стоит ответ
агенту и попадает ли он в файл-цель». У измерительного слоя свой класс
дефектов, и он опаснее поломки поиска: замер, который считает НЕ ТО, выглядит
зелёным и врёт числами. Поэтому проверяется не «поиск работает», а:

  1. `material` у каждого хита совпадает с РЕЕСТРОМ форматов, а `material_mix`
     — это сумма по выданным хитам (а не второй список расширений, который
     разошёлся бы с реестром молча);
  2. один и тот же путь не занимает в ответе два места: иначе «кандидатов 5»
     означало бы «4 файла»;
  3. подсказка `code_candidate` появляется ТОГДА И ТОЛЬКО ТОГДА, когда в
     выдаче нет ни одного файла кода, и её путь действительно файл кода;
  4. арифметика метрик (попадание в тройку, базовая линия без индекса,
     схлопывание копий) верна на синтетических данных — там, где ответ известен
     заранее;
  5. замер ОТКАЗЫВАЕТ, если набор вопросов противоречит корпусу (файла-цели
     нет, слово-якорь не встречается в цели). Иначе «0 попаданий» читалось бы
     как свойство поиска, а не как ошибка набора.

Подсадка (проверено при написании): если вернуть подсказку «всегда» или
сравнивать материал по списку расширений в замере, набор краснеет.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import catalog as catalog_mod  # noqa: E402
from ragraf import formats, util, watch  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402

PASS = 0
FAIL = 0
_TEST_CATALOG: Path | None = None


def _load_tool(name: str):
    """Загрузить инструмент из `tools/` как модуль, не затеняя пакет `ragraf`."""
    path = REPO / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_i13_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # Модуль обязан быть в `sys.modules` ДО исполнения: иначе `@dataclass` не
    # находит свой модуль и падает (`NoneType.__dict__`) — проверено запуском.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _patch_catalog() -> None:
    original = catalog_mod.Catalog

    def factory(*args, **kwargs):
        if kwargs.get("path") is None and not args:
            kwargs["path"] = _TEST_CATALOG
        return original(*args, **kwargs)

    catalog_mod.Catalog = factory      # type: ignore[assignment]
    watch.Catalog = factory            # type: ignore[assignment]


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def make_corpus(root: Path, name: str, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    cfg_path = root / "ragraf.yaml"
    cfg_path.write_text(
        f"""project: {name}
root: {util.to_posix(root)}

languages:
  - python
  - markdown

include:
  - "**/*.py"
  - "**/*.md"

exclude:
  - "**/.ragraf/**"

db:
  enabled: false

embedding:
  model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
  dim: 384

chunking:
  by_symbol: true
  max_chunk_lines: 120

index:
  retain_revs: 3
  batch_size: 2000
  joern_enabled: false
  reindex_after_changes: 1
""",
        encoding="utf-8",
        newline="\n",
    )
    return cfg_path


def main() -> int:
    global _TEST_CATALOG
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_usefulness_"))
    _TEST_CATALOG = tmp / "catalog" / "catalog.json"
    _patch_catalog()

    try:
        from ragraf.facade import Facade

        tool = _load_tool("measure_usefulness")

        root = tmp / "proj"
        cfg = load_config(make_corpus(root, "proj", {
            # Код-цель: одно слово «клапан» встречается только здесь.
            "src/valve.py": (
                "def close_valve(pressure: int) -> bool:\n"
                "    \"\"\"Клапан закрывается, когда давление выше предела.\"\"\"\n"
                "    return pressure > 10\n"
            ),
            "docs/manual.md": (
                "# Руководство\n\n"
                "Здесь описано, как устроен насос и что делать с трубой.\n"
            ),
            "docs/other.md": "# Другое\n\nСовсем другой текст про провод.\n",
        }))
        watch.run_reindex(cfg, "proj", trigger="test", mode="fast")

        facade = Facade(cfg, tool="dev", agent="RAGRAF-Test", agent_kind="internal",
                        task="этап13:контракт", telemetry=False)
        try:
            # ---------------- 1. material и material_mix ----------------
            print("\n=== 1. Материал хита — из реестра форматов ===")
            answer = facade.op_search("клапан давление предел", limit=5)
            hits = answer.get("result") or []
            check(bool(hits), "поиск вернул хотя бы один хит", str(answer.get("found")))
            registry_ok = True
            for hit in hits:
                expected = formats.group_of_material(str(hit["path"]))
                got = hit.get("material")
                if expected and got != expected:
                    registry_ok = False
                    print(f"      {hit['path']}: в ответе {got!r}, в реестре {expected!r}")
                if not expected and got:
                    registry_ok = False
                    print(f"      {hit['path']}: реестр не знает формат, а ответ назвал {got!r}")
            check(registry_ok, "материал каждого хита совпадает с реестром форматов")
            mix = answer.get("material_mix") or {}
            counted = {}
            for hit in hits:
                if hit.get("material"):
                    counted[hit["material"]] = counted.get(hit["material"], 0) + 1
            check(mix == dict(sorted(counted.items())),
                  "material_mix равен сумме по выданным хитам",
                  f"{mix} != {counted}")
            check(answer.get("material_mix") is None or sum(mix.values()) == len(hits),
                  "сумма материала равна числу хитов",
                  f"{mix} при {len(hits)} хитах")

            # ---------------- 2. Один файл — одно место ----------------
            print("\n=== 2. Один путь — одно место в ответе ===")
            paths = [str(h["path"]) for h in hits]
            check(len(paths) == len(set(paths)),
                  "в выдаче нет повторяющихся путей", str(paths))

            # ---------------- 3. code_candidate ----------------
            print("\n=== 3. Подсказка code_candidate: только когда кода нет ===")
            doc_answer = facade.op_search("насос труба руководство", limit=3)
            doc_hits = doc_answer.get("result") or []
            doc_materials = [(h["path"], h.get("material")) for h in doc_hits]
            has_code = any(m == "code" for _, m in doc_materials)
            hint = doc_answer.get("code_candidate")
            if has_code:
                check(hint is None,
                      "в выдаче есть код — подсказка не нужна и отсутствует",
                      f"{doc_materials}")
            else:
                check(hint is not None,
                      "в выдаче нет кода — подсказка обязана быть",
                      f"{doc_materials}")
                if hint:
                    check(formats.group_of_material(str(hint["path"])) == "code",
                          "подсказка ведёт в файл КОДА, а не в документ",
                          str(hint.get("path")))
                    check(str(hint["path"]) not in [p for p, _ in doc_materials],
                          "подсказка не повторяет hit из выдачи")
            code_answer = facade.op_search("клапан давление предел", limit=5)
            if any(h.get("material") == "code" for h in (code_answer.get("result") or [])):
                check(code_answer.get("code_candidate") is None,
                      "когда код в выдаче есть, подсказка НЕ появляется",
                      str(code_answer.get("code_candidate")))
        finally:
            facade.close()

        # ---------------- 4. Арифметика метрик ----------------
        print("\n=== 4. Арифметика методики на синтетике ===")
        check(tool.first_rank(["a", "b", "c"], ["b"]) == 2,
              "место цели считается с единицы", str(tool.first_rank(["a", "b"], ["b"])))
        check(tool.first_rank(["a", "b"], ["z"]) is None,
              "отсутствие цели даёт None, а не 0")
        rows = [
            {"id": "x", "kind": "code", "targets": ["a"], "target_rank": 1,
             "candidates": 3, "hits": 3, "copies": 0, "code_hits": 1, "doc_hits": 0,
             "code_candidate": "", "answer_chars": 100, "found": True, "seconds": 0.1,
             "paths": ["a"]},
            {"id": "y", "kind": "doc", "targets": ["d"], "target_rank": None,
             "candidates": 3, "hits": 3, "copies": 1, "code_hits": 0, "doc_hits": 2,
             "code_candidate": "d", "answer_chars": 100, "found": True, "seconds": 0.1,
             "paths": ["b"]},
        ]
        baseline = {
            "x": {"anchors": ["m"], "files": 4, "chars": 1000, "has_target": True},
            "y": {"anchors": ["n"], "files": 6, "chars": 2000, "has_target": True},
        }
        summary = tool.summarize(rows, baseline)
        check(summary["hit1"] == 1 and summary["hit3"] == 1 and summary["hit5"] == 1,
              "попадания считаются только по непустому месту",
              str({k: summary[k] for k in ("hit1", "hit3", "hit5")}))
        check(summary["candidates_avg"] == 3.0, "кандидатов в среднем считается верно",
              str(summary["candidates_avg"]))
        check(summary["grep_files_total"] == 10 and summary["grep_chars_total"] == 3000,
              "базовая линия суммируется по вопросам",
              f"{summary['grep_files_total']} / {summary['grep_chars_total']}")
        check(summary["visible_total"] == 2,
              "цель «видна» = место в выдаче ИЛИ подсказка code_candidate",
              str(summary["visible_total"]))
        check(summary["saved_chars"] == 3000 - 200,
              "замена чтения файлов = базовая линия минус ответы",
              str(summary["saved_chars"]))

        # Схлопывание копий: два пути с одинаковым содержимым — второе место
        # считается потерянным, и цель поднимается.
        (root / "src" / "twin_a.py").write_text("print(1)\n", encoding="utf-8")
        (root / "src" / "twin_b.py").write_text("print(1)\n", encoding="utf-8")
        recovered = tool.recover_by_copies(
            ["src/twin_a.py", "src/twin_b.py", "src/valve.py"], root, 2)
        check(recovered == ["src/twin_a.py", "src/valve.py"],
              "схлопывание копий возвращает на освободившееся место следующий файл",
              str(recovered))

        # ---------------- 5. Набор против корпуса ----------------
        print("\n=== 5. Замер отказывает, если набор противоречит корпусу ===")
        bad_set = tmp / "bad.yaml"
        bad_set.write_text(
            "version: 1\nquestions:\n"
            "  - id: b1\n    kind: code\n"
            "    question: \"нет такого файла\"\n"
            "    targets: [\"src/missing.py\"]\n"
            "    anchor: [\"клапан\"]\n",
            encoding="utf-8", newline="\n")
        questions = tool.load_questions(bad_set)
        texts = tool.corpus_texts(cfg)
        base = tool.measure_baseline(questions, texts)
        check(base["b1"]["has_target"] is False,
              "якорь, которого нет в файле-цели, виден проверке набора",
              str(base["b1"]))
        good_set = tmp / "good.yaml"
        good_set.write_text(
            "version: 1\nquestions:\n"
            "  - id: g1\n    kind: code\n"
            "    question: \"где закрывается клапан\"\n"
            "    targets: [\"src/valve.py\"]\n"
            "    anchor: [\"клапан\"]\n",
            encoding="utf-8", newline="\n")
        questions = tool.load_questions(good_set)
        base = tool.measure_baseline(questions, texts)
        check(base["g1"]["has_target"] is True and base["g1"]["files"] >= 1,
              "правильный набор даёт сравнимую базовую линию", str(base["g1"]))
        try:
            broken = tmp / "broken.yaml"
            broken.write_text("questions:\n  - id: x\n    question: q\n", encoding="utf-8")
            tool.load_questions(broken)
            check(False, "набор без файла-цели отклоняется")
        except ValueError as exc:
            check("файл-цель" in str(exc) or "цель" in str(exc),
                  "набор без файла-цели отклоняется с причиной", str(exc)[:60])

        print("\n" + "=" * 78)
        print(f"итог: пройдено {PASS}, провалено {FAIL}")
        print("=" * 78)
        return 0 if FAIL == 0 else 1
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
