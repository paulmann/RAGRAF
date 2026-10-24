"""Установка гейта использования индекса в профиль DSH (§3.2).

Что делает:
  1. проверяет, что `harness/hooks.json` на месте и разбирается как JSON,
     а интерпретатор и скрипт хука существуют — иначе установка бессмысленна;
  2. идемпотентно добавляет в `~/.dsh/profiles/desktop/cordis.patch.yml` запись
     `insert` с `@deepseek-ai/dsh-hooks-claude-code` и `configPath` на этот файл;
  3. делает резервную копию патча и ПОСЛЕ правки проверяет, что YAML разбирается
     и что уже существовавшая запись хука (xerj) осталась на месте — иначе
     откатывается из копии;
  4. говорит, что сделать дальше (перезапуск сеанса: конфиг хуков читается один
     раз при старте процесса).

Режимы:
  `--check`  — только показать состояние, ничего не менять;
  `--remove` — убрать запись гейта (копия тоже делается);
  `--path`   — другой путь патча (для проверок).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "harness" / "hooks.json"
GATE_SCRIPT = REPO / "tools" / "harness_gate.py"
INTERPRETER = REPO / ".venv" / "Scripts" / "python.exe"
DEFAULT_PATCH = Path.home() / ".dsh" / "profiles" / "desktop" / "cordis.patch.yml"
ENTRY_ID = "hooks-ragraf-gate"
PACKAGE = "@deepseek-ai/dsh-hooks-claude-code"


def entry_block(config_path: Path) -> str:
    return (
        "\n# Гейт использования индекса RAGRAF (§3.2): PreToolUse-хук решает, можно ли\n"
        "# искать по файлам, если есть актуальный индекс, к которому не обратились.\n"
        "# Логика — в RAGRAF (src/ragraf/gate.py), здесь только подключение хука.\n"
        "# Поставил: python tools/install_harness_gate.py\n"
        "- insert:\n"
        f"  - id: {ENTRY_ID}\n"
        f"    name: '{PACKAGE}'\n"
        "    config:\n"
        f"      configPath: {config_path}\n"
        "      defaultTimeoutMs: 60000\n"
    )


def load_patch(path: Path) -> list | None:
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"патч не разобран как YAML: {type(exc).__name__}: {exc}")
        return None
    return data if isinstance(data, list) else None


def has_entry(data: list | None) -> bool:
    for item in data or []:
        if not isinstance(item, dict):
            continue
        entries = item.get("insert") if "insert" in item else [item]
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get("id") == ENTRY_ID:
                return True
    return False


def other_hook_ids(data: list | None) -> list[str]:
    """Чужие записи хуков — их потеря при правке недопустима."""
    out = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        entries = item.get("insert") if "insert" in item else [item]
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "")
            if name == PACKAGE and str(entry.get("id") or "") != ENTRY_ID:
                out.append(str(entry.get("id") or "?"))
    return out


def preflight() -> list[str]:
    problems = []
    if not HOOKS.exists():
        problems.append(f"нет файла хуков: {HOOKS}")
    else:
        try:
            data = json.loads(HOOKS.read_text(encoding="utf-8"))
            events = sorted((data.get("hooks") or {}).keys())
            if "PreToolUse" not in events:
                problems.append("в hooks.json нет события PreToolUse")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"hooks.json не разобран: {type(exc).__name__}: {exc}")
    if not GATE_SCRIPT.exists():
        problems.append(f"нет скрипта хука: {GATE_SCRIPT}")
    if not INTERPRETER.exists():
        problems.append(f"нет интерпретатора: {INTERPRETER}")
    return problems


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Установка гейта RAGRAF в профиль DSH")
    ap.add_argument("--check", action="store_true", help="только показать состояние")
    ap.add_argument("--remove", action="store_true", help="убрать запись гейта")
    ap.add_argument("--path", default=str(DEFAULT_PATCH), help="путь cordis.patch.yml")
    args = ap.parse_args(argv[1:])

    patch_path = Path(args.path)
    print(f"патч профиля: {patch_path}")
    problems = preflight()
    for p in problems:
        print(f"  ПРОБЛЕМА: {p}")
    data = load_patch(patch_path)
    print(f"  записей гейта: {'есть' if has_entry(data) else 'нет'}"
          f" | чужих хуков: {', '.join(other_hook_ids(data)) or '—'}")
    if problems:
        return 2
    if args.check:
        return 0 if has_entry(data) else 1

    if not patch_path.exists():
        print("  патч профиля не найден — установка невозможна (проверьте путь)")
        return 2

    original = patch_path.read_text(encoding="utf-8")

    if args.remove and not has_entry(data):
        print("  записи гейта в патче нет — удалять нечего")
        return 0
    if not args.remove and has_entry(data):
        print("  запись уже есть — правка не нужна (идемпотентно)")
        return 0

    backup = patch_path.with_name(
        patch_path.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(patch_path, backup)
    print(f"  резервная копия: {backup}")

    if args.remove:
        blocks = [b for b in original.split("\n- insert:") if f"id: {ENTRY_ID}" not in b]
        text = "\n- insert:".join(blocks) if len(blocks) > 1 else original
    else:
        text = original.rstrip("\n") + "\n" + entry_block(HOOKS)

    patch_path.write_text(text, encoding="utf-8", newline="\n")
    after = load_patch(patch_path)
    if after is None:
        patch_path.write_text(original, encoding="utf-8", newline="\n")
        print("  ОТКАТ: после правки YAML не разбирается")
        return 3
    if args.remove:
        if has_entry(after):
            patch_path.write_text(original, encoding="utf-8", newline="\n")
            print("  ОТКАТ: запись гейта осталась на месте")
            return 3
    else:
        if not has_entry(after):
            patch_path.write_text(original, encoding="utf-8", newline="\n")
            print("  ОТКАТ: запись гейта не появилась")
            return 3
    kept = set(other_hook_ids(after))
    lost = set(other_hook_ids(data)) - kept
    if lost:
        patch_path.write_text(original, encoding="utf-8", newline="\n")
        print(f"  ОТКАТ: потеряны чужие записи хуков: {', '.join(sorted(lost))}")
        return 3

    print("  Готово." if not args.remove else "  Запись гейта удалена.")
    if not args.remove:
        print("  Дальше: перезапустить сеанс DSH — конфиг хуков читается один раз")
        print("  при старте процесса. Проверка: pwsh с `Select-String` в проекте,")
        print("  у которого отстал индекс, должен получить запрос/отказ с причиной.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
