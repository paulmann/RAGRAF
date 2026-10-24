"""План правки профиля DSH: одна запись моста хуков вместо многих (§3.2).

ИЗМЕРЕННЫЙ ДЕФЕКТ. Мост хуков — один плагин (`hooks-claude-code`). В профиле
стояли три записи insert этого пакета (xerj, RAGRAF, bsg), каждая со своим
`configPath`. За неделю работы гейт RAGRAF не принял ни одного решения
(`catalog/gate_state.json`: всё от 14.10, дня установки; сеансы начинались 15, 16,
18 и 21 октября). `SessionStart`-хук матчера не имеет и обязан срабатывать на
каждом старте — значит наш конфиг не был смонтирован: активна одна запись
плагина, остальные не поднимаются.

РЕШЕНИЕ. Свод всех хуков в один файл (`tools/merge_hooks.py` →
`harness/all-hooks.json`) и одна запись `hooks-shared`.

ПОЧЕМУ ОТДЕЛЬНЫЙ ШАГ. У процесса python в DSH песочница «только рабочий
каталог»: читать патч можно, писать в `~/.dsh` — нельзя (измерено 21.10.2026:
PermissionError; из PowerShell тот же путь пишется). Python считает новый текст
в рабочем каталоге, замену файла делает `tools/install_harness_gate.ps1`.

ФОРМАТ ПАТЧА (замер): записи `- insert:` с отступом `    - id:` (четыре
пробела), у каждой записи свой блок; перед блоком бывают комментарии,
описывающие именно его. Поэтому правим БЛОКАМИ, а не по одной строке.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE = "@deepseek-ai/dsh-hooks-claude-code"
SHARED_ID = "hooks-shared"
SHARED_CONFIG = REPO / "harness" / "all-hooks.json"
PLAN_PATH = REPO / "harness" / ".patch-new.yml"

ENTRY_RE = re.compile(r"^(\s+)- id:\s*(\S+)\s*$")
INSERT_RE = re.compile(r"^- insert:\s*$")
TOP_LEVEL_RE = re.compile(r"^- [A-Za-z]")


def split_blocks(lines: list[str]) -> list[dict]:
    """Разбить патч на блоки `- insert:` вместе с их записями и комментариями.

    Возврат: `[{start, end, header, entries}]`, где `start` включает
    непосредственно предшествующие комментарии (они описывают этот блок).
    """
    blocks: list[dict] = []
    n = len(lines)
    i = 0
    while i < n:
        if INSERT_RE.match(lines[i]):
            start = i
            # Комментарии (и пустые строки между ними) прямо над блоком
            # принадлежат ему: без них удаление оставило бы описание
            # несуществующего хука.
            while start > 0:
                previous = lines[start - 1]
                if previous.lstrip().startswith("#") or not previous.strip():
                    start -= 1
                    continue
                break
            j = i + 1
            entries: list[tuple[int, str]] = []
            while j < n:
                if INSERT_RE.match(lines[j]) or TOP_LEVEL_RE.match(lines[j]):
                    break
                match = ENTRY_RE.match(lines[j])
                if match:
                    entries.append((j, match.group(2).rstrip("'\"")))
                j += 1
            blocks.append({"start": start, "end": j, "header": i, "entries": entries})
            i = j
        else:
            i += 1
    return blocks


def block_is_hook(block: dict, lines: list[str]) -> bool:
    if not block["entries"]:
        return False
    text = "\n".join(lines[block["start"]:block["end"]])
    return PACKAGE in text


def build(lines: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Собрать новый текст: блоки моста удалены, добавлена одна сводная запись.

    Возврат: (новые строки, удалённые id, все id после правки по нашему расчёту).
    """
    blocks = split_blocks(lines)
    drop: set[int] = set()
    removed: list[str] = []
    kept_ids: list[str] = []
    for block in blocks:
        ids = [entry_id for _line, entry_id in block["entries"]]
        if block_is_hook(block, lines):
            drop.update(range(block["start"], block["end"]))
            removed.extend(ids)
        else:
            kept_ids.extend(ids)
    out: list[str] = []
    for index, line in enumerate(lines):
        if index not in drop:
            out.append(line)
    header = [
        "",
        "# Свод хуков AutoClaw (§3.2): ОДНА запись моста на все проекты.",
        "# Мост — один плагин, вторая и последующие записи не поднимаются",
        "# (измерено 21.10.2026: гейт RAGRAF не получал вызовов неделю).",
        "# Источники: C:\\AutoClaw\\tools\\xerj\\hooks.json,",
        "#            C:\\AutoClaw\\tools\\bsg\\hooks.json,",
        "#            C:\\AutoClaw\\RAGRAF\\harness\\hooks.json",
        "# Свод: python tools/merge_hooks.py",
        "# Ставит: pwsh -File tools/install_harness_gate.ps1",
        "- insert:",
        f"    - id: {SHARED_ID}",
        f"      name: '{PACKAGE}'",
        "      config:",
        f"        configPath: {SHARED_CONFIG}",
        "        defaultTimeoutMs: 60000",
    ]
    # Обрезаем хвостовые пустые строки, чтобы не копить их при повторных правках.
    while out and not out[-1].strip():
        out.pop()
    # Строки приходили через splitlines(keepends=True), но последняя строка файла
    # могла быть без перевода строки — иначе добавленный блок склеивался с ней
    # в одну строку (измерено: `...hooks.json- insert:    - id: hooks-shared`).
    body = "".join(out)
    if body and not body.endswith("\n"):
        body += "\n"
    text = body + "\n".join(header) + "\n"
    expected = kept_ids + [SHARED_ID]
    return text.splitlines(keepends=True), removed, expected


def verify(path: Path) -> dict:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    hook_ids: list[str] = []
    all_ids: list[str] = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        for entry in item.get("insert") or []:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("id") or "")
            all_ids.append(entry_id)
            if str(entry.get("name") or "") == PACKAGE:
                hook_ids.append(entry_id)
    return {"ok": hook_ids == [SHARED_ID], "hook_ids": hook_ids,
            "all_ids": all_ids, "entries": len(all_ids)}


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="План/проверка правки патча профиля DSH")
    ap.add_argument("--patch", default=str(Path.home() / ".dsh" / "profiles" / "desktop"
                                           / "cordis.patch.yml"))
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--verify", default=None)
    args = ap.parse_args(argv[1:])

    if args.verify:
        result = verify(Path(args.verify))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1

    patch = Path(args.patch)
    if not patch.exists():
        print(json.dumps({"ok": False, "error": f"нет патча: {patch}"},
                         ensure_ascii=False))
        return 2
    before = verify(patch)
    lines = patch.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines, removed, expected = build(lines)
    PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAN_PATH.write_text("".join(new_lines), encoding="utf-8", newline="\n")
    planned = verify(PLAN_PATH)
    ok = planned["ok"] and sorted(planned["all_ids"]) == sorted(expected)
    print(json.dumps({
        "ok": ok,
        "patch": str(patch),
        "plan": str(PLAN_PATH),
        "before": before,
        "removed_hook_ids": removed,
        "expected_ids": expected,
        "planned": planned,
        "reason": "" if ok else "план не совпал с ожидаемым набором записей",
    }, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
