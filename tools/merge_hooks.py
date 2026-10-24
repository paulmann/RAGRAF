"""Свод всех хуков AutoClaw в ОДИН файл конфигурации (§3.2: починка моста).

ЗАЧЕМ. Мост хуков DSH (`@deepseek-ai/dsh-hooks-claude-code`) — плагин с именем
`hooks-claude-code`. В профиле оказались ТРИ записи `insert` этого пакета
(хуки xerj, RAGRAF и bsg), каждая со своим `configPath`. Живой замер 21.10.2026:
за неделю работы гейт RAGRAF не принял НИ ОДНОГО решения (в `catalog/gate_state.json`
все 51 решение от 14.10 — дня установки), при том что сеансы в клиенте
начинались 15, 16, 18 и 21 октября. Событие `SessionStart` у нашего конфига
матчера не имеет и обязано срабатывать на каждом старте — значит наш конфиг не
был смонтирован вовсе: вторая и последующие записи одного и того же плагина
не поднимаются, активной остаётся одна (по времени установки — bsg).

РЕШЕНИЕ. Один файл `C:\\AutoClaw\\tools\\hooks.json`, собранный из проектных
конфигов, и ОДНА запись `insert` в профиле. Проектные конфиги остаются на месте
и остаются источником правды: правишь свой — запускаешь этот сборщик (его же
вызывает установщик гейта), общий файл пересобирается.

Проверка расхождения: `--check` — общий файл совпадает со сборкой из источников.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
#: Источники в порядке слияния: сначала чужие хуки, потом наши. Порядок важен
#: для SessionStart (сборка идёт по порядку групп), а решения PreToolUse всё
#: равно сливаются протоколом как `deny > ask > allow`.
SOURCES = [
    ("xerj", Path(r"C:\AutoClaw\tools\xerj\hooks.json")),
    ("bsg", Path(r"C:\AutoClaw\tools\bsg\hooks.json")),
    ("ragraf", REPO / "harness" / "hooks.json"),
]
#: Куда пишется свод. Лежит В РАГРАФЕ по двум причинам: (1) свод — это
#: инфраструктура, которую собирает RAGRAF (он и владеет мостом хуков), (2) у
#: процесса python в DSH песочница «только рабочий каталог», и запись в
#: `C:\\AutoClaw\\tools` из python отклоняется (измерено 21.10.2026:
#: `PermissionError`, при этом из PowerShell тот же путь пишется). Профиль DSH
#: правит PowerShell-установщик, а свод остаётся здесь и читается мостом хуков.
SHARED = REPO / "harness" / "all-hooks.json"


def build() -> dict:
    """Собрать общий конфиг из проектных. Отсутствующий источник пропускается."""
    hooks: dict[str, list] = {}
    used: list[str] = []
    for label, path in SOURCES:
        if not path.exists():
            print(f"  источник отсутствует, пропущен: {label} ({path})")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"источник не разобран: {label} ({path}): {exc}")
        used.append(label)
        for event, groups in (data.get("hooks") or {}).items():
            for group in groups:
                hooks.setdefault(event, []).append({
                    "hooks": list(group.get("hooks") or []),
                    **({"matcher": group["matcher"]} if group.get("matcher") else {}),
                })
    readme = [
        "Сводный конфиг хуков AutoClaw для DSH (мост @deepseek-ai/dsh-hooks-claude-code).",
        "СОБРАН АВТОМАТИЧЕСКИ: python C:\\AutoClaw\\RAGRAF\\tools\\merge_hooks.py",
        "Источники (правка — в них, не здесь): " + ", ".join(used),
        "Причина одного файла: одна запись плагина в профиле; вторая запись того же",
        "плагина не поднимается — измерено 21.10.2026 (гейт RAGRAF не получал вызовов",
        "неделю, хотя его SessionStart-хук обязан срабатывать на каждом старте).",
    ]
    return {"_readme": readme, "sources": used, "hooks": hooks}


def render(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Свод хуков AutoClaw в один файл")
    ap.add_argument("--check", action="store_true", help="только сравнить, не писать")
    ap.add_argument("--print", action="store_true", dest="show", help="напечатать сборку")
    ap.add_argument("--shared", default=str(SHARED), help="куда писать общий файл")
    args = ap.parse_args(argv[1:])

    shared = Path(args.shared)
    data = build()
    text = render(data)
    if args.show:
        print(text)
        return 0
    if args.check:
        if not shared.exists():
            print(f"общий файл отсутствует: {shared}")
            return 1
        current = shared.read_text(encoding="utf-8")
        same = current == text
        print(f"общий файл: {shared} | совпадает со сборкой: {'да' if same else 'НЕТ'}")
        print("источники:", ", ".join(data["sources"]))
        for event, groups in data["hooks"].items():
            print(f"  {event}: групп {len(groups)}, хуков "
                  f"{sum(len(g['hooks']) for g in groups)}")
        if not same:
            print("нужно пересобрать: python tools/merge_hooks.py")
        return 0 if same else 1
    shared.parent.mkdir(parents=True, exist_ok=True)
    if shared.exists():
        backup = shared.with_name(
            shared.name + ".bak-" + str(int(shared.stat().st_mtime)))
        if not backup.exists():
            backup.write_text(shared.read_text(encoding="utf-8"), encoding="utf-8",
                              newline="\n")
            print(f"  резервная копия: {backup}")
    shared.write_text(text, encoding="utf-8", newline="\n")
    print(f"общий файл собран: {shared}")
    print("источники:", ", ".join(data["sources"]))
    for event, groups in data["hooks"].items():
        print(f"  {event}: групп {len(groups)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
