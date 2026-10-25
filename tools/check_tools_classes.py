#!/usr/bin/env python3
"""Класс каждого файла tools/: рабочая проверка или разовый инструмент этапа.

Класс дефекта (измерен 25.10.2026, ПРОМТ-И14): в tools/ 348 файлов .py, из них
266 названы разовыми по префиксу. Но имя — не признак: 7 файлов `check_*`
вызывает гейт, и они работают защитой. Обратное тоже бывает: `final_state.py`
выглядит отчётом, а `keep_cut.py` держит инвариант о копии до удаления.

Поэтому класс выводится из ФАКТА, а не из написания:

  working — файл достижим от гейта (tools/Invoke-ProjectDoctrine.ps1) напрямую
            или по цепочке импортов, либо объявлен рабочим в манифесте
            с причиной (tools/release/manifest.toml, [tools_classes]);
  oneoff  — остальное, и каждый такой файл обязан попасть в объявленную группу
            темы (manifest, [[tools_classes.group]]) или быть объявлен лично.

Файл, не попавший ни туда, ни туда, — находка: значит появился инструмент, о
классе которого никто не решил, и читатель снова не отличит проверку от патча.

Ответ печатает оба числа (рабочих и разовых) и объявленное послабление: поставка
сейчас несёт весь каталог tools/, поэтому разовый в неё идёт — это названо
числом `oneoff_shipped_now` и сверяется здесь же.

Коды возврата: 0 — всё объявлено, 1 — находки, 2 — нечего осматривать
(ноль файлов): «ноль осмотренных» не должен выглядеть как PASS.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
import tomllib
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]


def _gate_closure(tools: Path, gate_text: str) -> tuple[set[str], list[str]]:
    """Файлы tools/*.py, достижимые от гейта: прямые упоминания и импорты."""
    files = {p.name: p for p in tools.glob("*.py")}
    named = sorted(name for name in files if name in gate_text)
    seen = set(named)
    stack = list(named)
    while stack:
        current = files.get(stack.pop())
        if current is None:
            continue
        text = current.read_text(encoding="utf-8", errors="replace")
        modules = set(re.findall(r"(?m)^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", text))
        for module in modules:
            head = module.split(".")[0] + ".py"
            if head in files and head not in seen:
                seen.add(head)
                stack.append(head)
    return seen, named


def main() -> int:
    parser = argparse.ArgumentParser(description="классы файлов tools/")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="корень проекта")
    parser.add_argument("--json", action="store_true", help="машинный ответ")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    tools = root / "tools"
    manifest_path = root / "tools/release/manifest.toml"
    failures: list[str] = []

    if not manifest_path.exists():
        print("НЕ ИЗМЕРЕНО: нет tools/release/manifest.toml — объявлений класса нет", file=sys.stderr)
        return 2
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
    declared = manifest.get("tools_classes")
    if not declared:
        print("НЕ ИЗМЕРЕНО: в манифесте нет раздела [tools_classes]", file=sys.stderr)
        return 2

    gate_rel = str(declared.get("gate") or "")
    gate_path = root / gate_rel
    if not gate_path.exists():
        print(f"НЕ ИЗМЕРЕНО: нет файла гейта {gate_rel}", file=sys.stderr)
        return 2
    gate_text = gate_path.read_text(encoding="utf-8", errors="replace")

    files = sorted(p.name for p in tools.glob("*.py"))
    if not files:
        print("НЕ ИЗМЕРЕНО: в tools/ нет файлов .py — обход разошёлся с деревом", file=sys.stderr)
        return 2

    reached, named = _gate_closure(tools, gate_text)
    groups = declared.get("group") or []
    manual = {str(item.get("path", "")).replace("\\", "/"): item for item in (declared.get("manual") or [])}

    classes: dict[str, str] = {}
    reasons: dict[str, str] = {}
    misleading: list[str] = []

    for name in files:
        rel = f"tools/{name}"
        override = manual.get(rel)
        if override is not None:
            classes[name] = str(override.get("class") or "")
            reasons[name] = str(override.get("reason") or "").strip()
            if classes[name] not in {"working", "oneoff"}:
                failures.append(f"{rel}: класс «{classes[name]}» не из двух объявленных")
            if not reasons[name]:
                failures.append(f"{rel}: объявлен лично, но причина не названа")
            continue
        if name in reached:
            classes[name] = "working"
            reasons[name] = "достижим от гейта"
            for group in groups:
                if any(name.startswith(str(prefix)) for prefix in (group.get("prefixes") or [])):
                    misleading.append(name)
            continue
        hit = None
        for group in groups:
            if any(name.startswith(str(prefix)) for prefix in (group.get("prefixes") or [])):
                hit = group
                break
        if hit is not None:
            reasons[name] = str(hit.get("reason") or "").strip().replace("\n", " ")
            if not reasons[name]:
                failures.append(f"{rel}: группа объявлена без причины")
            classes[name] = "oneoff"
            continue
        failures.append(f"{rel}: класс не объявлен — нет ни группы темы, ни личной записи")

    working = [n for n in files if classes.get(n) == "working"]
    oneoff = [n for n in files if classes.get(n) == "oneoff"]
    unclassified = [n for n in files if n not in classes]

    # Объявленное послабление: сколько разовых реально уходит в поставку.
    shipped = 0
    include_tools = any(
        str(item.get("from") or "").rstrip("/") == "tools" for item in (manifest.get("include") or [])
    )
    excludes = [str(item.get("pattern") or "") for item in (manifest.get("exclude") or [])]
    tools_excludes = [pattern for pattern in excludes if pattern.replace("\\", "/").startswith("tools/")]
    for name in oneoff:
        dropped = any(fnmatch.fnmatch(f"tools/{name}", pattern) for pattern in tools_excludes)
        if include_tools and not dropped:
            shipped += 1
    declared_shipped = declared.get("oneoff_shipped_now")
    if declared_shipped is None:
        failures.append("в [tools_classes] не объявлено oneoff_shipped_now: послабление поставки не названо")
    elif int(declared_shipped) != shipped:
        failures.append(
            f"объявлено «разовых в поставке {declared_shipped}», измерено {shipped}: объявление разошлось с манифестом"
        )

    ok = not failures
    payload = dict(
        ok=ok,
        files=len(files),
        working=len(working),
        oneoff=len(oneoff),
        unclassified=len(unclassified),
        gate_named=len(named),
        gate_reached=len(reached),
        misleading_names=misleading,
        oneoff_shipped=shipped,
        oneoff_shipped_declared=declared_shipped,
        failures=failures,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"[check_tools_classes] файлов tools/*.py: {len(files)}")
        print(f"  рабочие (входят в гейт): {len(working)}, из них названы прямо: {len(named)}")
        print(f"  разовые (объявлены группой темы): {len(oneoff)}")
        print(f"  имя «разовое», а класс рабочий: {len(misleading)} — {', '.join(misleading) if misleading else '—'}")
        print(f"  разовых в поставке: {shipped} (объявлено {declared_shipped})")
        print(f"  не объявлено: {len(unclassified)}")
        for item in failures:
            print(f"  - {item}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
