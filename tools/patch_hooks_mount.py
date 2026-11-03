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
import os
import re
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE = "@deepseek-ai/dsh-hooks-claude-code"
SHARED_ID = "hooks-shared"
SHARED_CONFIG = REPO / "harness" / "all-hooks.json"
PLAN_PATH = REPO / "harness" / ".patch-new.yml"
#: Установленная сборка клиента: по её списку файлов проверяется наличие
#: механизма моста хуков (см. `check_mechanism`).
#:
#: Путь ВЫЧИСЛЯЕТСЯ, а не прописан: публикуемый файл не имеет права нести
#: путь внутрь домашнего каталога конкретной машины. Замер 28.10.2026
#: (задание И42): буквальный путь сделал сборку негодной по правилу
#: [paths] проверяльщика поставки — «путь внутрь домашнего каталога».
DEFAULT_ASAR = (
    Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    / "Programs" / "DeepSeek Harness" / "resources" / "app.asar"
)

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
        "# Источники: tools\\xerj\\hooks.json,",
        "#            tools\\bsg\\hooks.json,",
        "#            harness\\hooks.json",
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


def check_mechanism(asar: Path, brief: bool = False) -> int:
    """Есть ли в установленной сборке DSH механизм моста хуков.

    ЗАЧЕМ. 21.10.2026 гейт починили (одна запись моста, свод хуков), а 28.10.2026
    проверка по факту показала: за неделю ни одного решения. Причина измерена —
    обновление приложения (0.2.0-rc.2 → 0.2.1-rc.3) удалило пакет
    `@deepseek-ai/dsh-hooks-claude-code` вместе с `dsh-hook-protocol` из сборки
    (18 188 файлов, 307 пакетов `@deepseek-ai`, ни одного hook-пакета; в 9 000
    js-файлов нет `PreToolUse`/`permissionDecision`/`UserPromptSubmit`). Профиль
    остался «правильным» и ссылался на несуществующий пакет — приложение молча
    не запускало ни один хук. Поэтому установщик обязан проверять МЕХАНИЗМ, а не
    только конфиг: иначе зелёная проверка подтверждает то, чего нет.

    Живёт здесь, а не отдельным файлом: у каждого файла `tools/` есть место в
    поставке (манифест выпуска), и десятая проверка чекера считает такие файлы.
    Отдельный файл сдвинул счётчик 334 → 335 и уронил гейт выпуска — измерено
    28.10.2026. Один инструмент, два режима — дешевле и честнее.
    """
    if not asar.exists():
        print(f"НЕТ: сборка DSH не найдена ({asar})")
        return 1
    try:
        with asar.open("rb") as fh:
            _, _, json_size = struct.unpack("<III", fh.read(16)[:12])
            raw = fh.read(json_size)
        header = json.loads(raw[:raw.rfind(b"}") + 1].decode("utf-8"))
        files = _list_asar_files(header)
    except Exception as exc:  # noqa: BLE001
        print(f"НЕТ: сборка не разобрана ({type(exc).__name__}: {exc})")
        return 1
    pkg_name = PACKAGE.rsplit("/", 1)[1]
    present = [f for f in files if f"/{pkg_name}/" in f or f.endswith(f"/{pkg_name}")]
    protocol = [f for f in files if "/dsh-hook-protocol/" in f]
    if present:
        if brief:
            print(f"ЕСТЬ: мост хуков в сборке ({len(present)} файлов)")
        else:
            print(f"ЕСТЬ: пакет {PACKAGE} найден в сборке ({len(present)} файлов)")
            print(f"протокол хуков: {'есть' if protocol else 'НЕТ'}")
        return 0
    if brief:
        print("НЕТ: моста хуков в сборке нет (мост может стоять в профиле — см. ниже)")
        return 1
    print(f"НЕТ: пакет {PACKAGE} в сборке отсутствует.")
    print(f"  разобрано файлов сборки: {len(files)}")
    print(f"  протокол хуков: {'есть' if protocol else 'НЕТ'}")
    print("  что это значит: мост ставится в ПРОФИЛЬ, а не в сборку — с 02.11.2026")
    print("  рабочая установка держит @deepseek-ai/dsh-hooks-claude-code и")
    print("  @deepseek-ai/dsh-hook-protocol в ~/.dsh/profiles/<профиль>/package.json,")
    print("  и хуки при этом вызываются. Поэтому вывод «в сборке нет» сам по себе")
    print("  НЕ доказывает, что хуки выключены (замер 03.11.2026: механизм-чекер")
    print("  говорил «нет», а install_harness_gate.py --check — «выполнение: ok»,")
    print("  1400+ вызовов хуков).")
    print("  проверять так: python tools/install_harness_gate.py --check")
    print("  («выполнение: ok» и свежее время вызовов = хуки работают).")
    print("  Если --check красный: python tools/install_harness_gate.py --write-hooks")
    print("  и перезапуск приложения.")
    return 1


def _list_asar_files(node: dict, prefix: str = "") -> list[str]:
    out: list[str] = []
    for name, item in (node.get("files") or {}).items():
        path = f"{prefix}/{name}" if prefix else name
        if isinstance(item, dict) and "files" in item:
            out.extend(_list_asar_files(item, path))
        else:
            out.append(path)
    return out


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="План/проверка правки патча профиля DSH")
    ap.add_argument("--patch", default=str(Path.home() / ".dsh" / "profiles" / "desktop"
                                           / "cordis.patch.yml"))
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--verify", default=None)
    ap.add_argument("--mechanism", action="store_true",
                    help="проверить, есть ли мост хуков в установленной сборке DSH")
    ap.add_argument("--brief", action="store_true")
    args = ap.parse_args(argv[1:])

    if args.mechanism:
        return check_mechanism(DEFAULT_ASAR, brief=args.brief)

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
