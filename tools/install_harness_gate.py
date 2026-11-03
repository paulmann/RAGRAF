"""Установка гейта использования индекса в профиль DSH (§3.2).

С 02.11.2026 установка состоит из четырёх частей, а не из одной правки YAML:
в HARNESS 44.7.0 моста хуков `@deepseek-ai/dsh-hooks-claude-code` в сборке
больше нет, поэтому запись `hooks-shared` в профиле указывала на несуществующий
пакет и хуки не вызывались вовсе.

Что делает эта команда
  1. **Ставит мост в профиль**, если его там нет: `dsh plugin --profile <p> add
     <мост>@<версия рантайма>` (и `@deepseek-ai/dsh-hook-protocol`, которого в
     сборке тоже нет). Флаг `--no-plugin` — не ставить, `--dry-run` — показать
     команду.
  2. **Собирает команды хуков в форме, которую понимает оболочка клиента.**
     PowerShell не принимает строку, начинающуюся с кавычки (`ParserError`,
     код 1 — измерено 02.11.2026), поэтому команды пишутся без кавычек:
     путь при необходимости сокращается до 8.3 либо создаётся пусковой `.cmd`.
  3. **Правит профиль**: идемпотентно добавляет `insert` с `configPath` на
     `harness/all-hooks.json`, снимает копию патча и проверяет, что YAML
     разбирается и чужие записи хуков целы — иначе откат.
  4. **Проверяет связку запуском**: каждая команда хука прогоняется через
     PowerShell и `cmd /c` со стадией `selftest` (состояние не меняется).

Режимы:
  `--check`      — только показать состояние (код 0 — всё на месте, 1 — нет);
  `--remove`     — убрать запись гейта (копия тоже делается);
  `--path`       — другой путь патча профиля (для проверок и песочниц);
  `--profile`    — другой профиль (по умолчанию `desktop`);
  `--no-plugin`  — не трогать пакеты профиля;
  `--dry-run`    — показать, что будет сделано, ничего не меняя.
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
sys.path.insert(0, str(REPO / "src"))

from ragraf import harness  # noqa: E402  — после правки sys.path

HOOKS = REPO / "harness" / "hooks.json"
GATE_SCRIPT = REPO / "tools" / "harness_gate.py"
INTERPRETER = REPO / ".venv" / "Scripts" / "python.exe"
ENTRY_ID = harness.ENTRY_ID
PACKAGE = harness.BRIDGE_PACKAGE

#: События и стадии хука: имя события протокола → аргумент скрипта.
STAGES = (("SessionStart", "session-start", 30),
          ("PreToolUse", "pre-tool", 60),
          ("Stop", "stop", 30))
#: Матчер PreToolUse: инструменты, после которых нужен индекс, а не файловый поиск.
PRETOOL_MATCHER = "pwsh|Bash|read|Read|grep|Grep|glob|Glob|mcp__ragraf__.*"
#: Режим, записываемый в команды хуков. `deny` — решение владельца 03.11.2026:
#: файловый поиск по проекту с корпусом отклоняется, пока индекс не использован.
#: Почему не `ask`: в этом клиенте подтверждения выключены, и `ask` молча
#: превращается в отказ с невнятной причиной — строгий режим называется явно.
GATE_MODE = "deny"


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
    """Разобрать патч профиля, понимая теги приложения.

    В патче есть значения вида `!!js dshHomePath('cache/session-search.db')` — это
    выражение загрузчика DSH, а не данные. `yaml.safe_load` на нём падает
    `ConstructorError: could not determine a constructor for the tag
    'tag:yaml.org,2002:js'` (замерено 02.11.2026 на живом профиле), и прежний
    установщик из-за этого объявлял ЛЮБОЙ такой профиль нечитаемым: 'записей
    гейта: нет', а затем добавлял второй блок. Поэтому тег `js` объявлен явно и
    значение сохраняется строкой — для проверки структуры этого достаточно.
    """
    if not path.exists():
        return None
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_patch_loader())
    except Exception as exc:  # noqa: BLE001
        print(f"патч не разобран как YAML: {type(exc).__name__}: {exc}")
        return None
    return data if isinstance(data, list) else None


def _patch_loader() -> type:
    """SafeLoader с поддержкой тега `!!js` (выражения загрузчика DSH)."""
    global _LOADER
    if _LOADER is not None:
        return _LOADER

    class PatchLoader(yaml.SafeLoader):
        pass

    PatchLoader.add_constructor(
        "tag:yaml.org,2002:js",
        lambda loader, node: ("!!js +", loader.construct_scalar(node)),
    )
    _LOADER = PatchLoader
    return _LOADER


_LOADER: type | None = None


def entries_of(data: list | None) -> list[dict]:
    out: list[dict] = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        entries = item.get("insert") if "insert" in item else [item]
        out.extend(e for e in (entries or []) if isinstance(e, dict))
    return out


def has_entry(data: list | None) -> bool:
    return any(str(e.get("id") or "") == ENTRY_ID for e in entries_of(data))


def other_hook_ids(data: list | None) -> list[str]:
    """Чужие записи хуков — их потеря при правке недопустима."""
    return [str(e.get("id") or "?") for e in entries_of(data)
            if str(e.get("name") or "") == PACKAGE and str(e.get("id") or "") != ENTRY_ID]


# ---------------------------------------------------------------------------
# Сборка конфигурации хуков
# ---------------------------------------------------------------------------


def gate_command(stage: str) -> dict:
    """Команда одной стадии в форме, понятной PowerShell и cmd."""
    return harness.safe_command([str(INTERPRETER), str(GATE_SCRIPT), stage,
                                 f"--mode={GATE_MODE}"],
                                launcher_dir=REPO / "harness" / "bin")


def hooks_config() -> dict:
    """Конфигурация хуков RAGRAF: события протокола и команды без кавычек."""
    hooks: dict[str, list] = {}
    for event, stage, timeout in STAGES:
        command = gate_command(stage)
        if not command["command"]:
            raise SystemExit(f"команду хука {event} собрать не удалось: {command['reason']}")
        group: dict = {"hooks": [{"type": "command", "timeout": timeout,
                                  "command": command["command"]}]}
        if event == "PreToolUse":
            group["matcher"] = PRETOOL_MATCHER
        hooks[event] = [group]
    return {
        "_readme": [
            "Конфигурация хуков DSH для гейта использования индекса RAGRAF (§3.2).",
            "СОБРАН АВТОМАТИЧЕСКИ: python tools/install_harness_gate.py --write-hooks",
            "Подключается записью insert в ~/.dsh/profiles/<профиль>/cordis.patch.yml.",
            "Команды без кавычек: PowerShell не принимает строку, начинающуюся с",
            "кавычки (ParserError, код 1 — измерено 02.11.2026).",
            "Режим задаётся переменной окружения RAGRAF_GATE_MODE: warn | ask | deny | off,",
            "по умолчанию — значение из src/ragraf/gate.py.",
        ],
        "hooks": hooks,
    }


def write_hooks(check_only: bool = False) -> dict:
    """Записать `harness/hooks.json` и пересобрать свод `all-hooks.json`."""
    text = json.dumps(hooks_config(), ensure_ascii=False, indent=2) + "\n"
    current = HOOKS.read_text(encoding="utf-8") if HOOKS.is_file() else ""
    changed = current.strip() != text.strip()
    result = {"path": str(HOOKS), "changed": changed}
    if changed and not check_only:
        HOOKS.parent.mkdir(parents=True, exist_ok=True)
        HOOKS.write_text(text, encoding="utf-8", newline="\n")
        result["written"] = True
    return result


def merge_shared() -> dict:
    """Пересобрать общий файл хуков вызовом штатного сборщика."""
    import subprocess

    proc = subprocess.run([sys.executable, str(REPO / "tools" / "merge_hooks.py")],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)
    return {"code": proc.returncode,
            "tail": "\n".join(((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-6:])}


def verify_commands() -> dict:
    """Проверить команды хуков, НЕ выполняя стадии с побочными эффектами.

    Что проверяется:
      * разбор каждой реальной команды парсером PowerShell — именно на разборе
        падал прежний хук (`ParserError`, код 1);
      * выполнение стадии `selftest` в PowerShell и cmd — она доказывает, что
        цепочка «интерпретатор → скрипт → ядро» работает и что русский текст
        не ломает вывод (прежний дефект — `UnicodeEncodeError` на cp1252).
    """
    out: dict[str, dict] = {}
    for event, stage, _timeout in STAGES:
        command = gate_command(stage)["command"]
        out[event] = {"command": command, "parse": harness.parse_only(command)}
    probe = harness.safe_command([str(INTERPRETER), str(GATE_SCRIPT), "selftest"],
                                 launcher_dir=REPO / "harness" / "bin")
    executed = harness.verify_command(probe["command"], payload="")
    ok = (all(item["parse"]["ok"] for item in out.values()) and executed["ok"])
    return {"ok": ok, "events": out, "selftest": {"command": probe["command"], **executed}}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def preflight() -> list[str]:
    problems = []
    if not GATE_SCRIPT.exists():
        problems.append(f"нет скрипта хука: {GATE_SCRIPT}")
    if not INTERPRETER.exists():
        problems.append(f"нет интерпретатора: {INTERPRETER}")
    return problems


def print_status(profile: str) -> int:
    state = harness.state(profile)
    print(f"  HARNESS:      {state['app_version'] or '?'}  ({state['app_root'] or 'не найден'})")
    print(f"  рантайм:      {state['runtime_version'] or '?'}")
    bridge = state["bridge"]
    proto = state["protocol"]
    print(f"  мост:         в профиле {bridge['installed'] or 'нет'}"
          f" | в сборке {'да' if bridge['in_build'] else 'нет'}")
    print(f"  протокол:     в профиле {proto['installed'] or 'нет'}"
          f" | в сборке {'да' if proto['in_build'] else 'нет'}")
    entry = state["entry"]
    print(f"  запись:       {'есть' if entry['present'] else 'нет'}"
          f" | файл хуков {'есть' if entry['config_exists'] else 'нет'}")
    events = state["hook_events"]
    verdict = state.get("hook_verdict") or {}
    installed_at = ""
    manifest = Path(state["profile"]) / "package.json"
    if manifest.is_file():
        installed_at = time.strftime("%Y-%m-%d %H:%M:%S",
                                     time.localtime(manifest.stat().st_mtime))
    if events["skipped"]:
        print(f"  вызовы хуков: не проверено ({events['skipped']})")
    else:
        codes = ", ".join(f"{k}×{v}" for k, v in (events.get("exit_codes") or {}).items()) or "—"
        print(f"  вызовы хуков: {events['invoked']} в {events['checked']} сеансах"
              f" | последний {events['last_at'] or '—'} | коды: {codes}")
        print(f"  выполнение:  {verdict.get('state', '?')} — {verdict.get('reason', '')}")
        if installed_at and events["last_at"] and events["last_at"] < installed_at:
            print(f"  ВАЖНО:       после установки моста ({installed_at}) вызовов НЕ было:"
                  f" клиент запись не смонтировал — нужен перезапуск либо разбор загрузчика")
    if state["actions"]:
        print("  требуется:")
        for action in state["actions"]:
            print(f"    · {action}")
    return 0 if not state["actions"] else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Установка гейта RAGRAF в профиль DSH")
    ap.add_argument("--check", action="store_true", help="только показать состояние")
    ap.add_argument("--remove", action="store_true", help="убрать запись гейта")
    ap.add_argument("--path", default="", help="путь cordis.patch.yml (по умолчанию — профиль)")
    ap.add_argument("--profile", default=harness.DEFAULT_PROFILE)
    ap.add_argument("--no-plugin", action="store_true", help="не ставить пакеты в профиль")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write-hooks", action="store_true",
                    help="только пересобрать harness/hooks.json и общий свод")
    ap.add_argument("--verify", action="store_true",
                    help="только прогнать команды хуков в PowerShell и cmd")
    ap.add_argument("--json", action="store_true", help="печатать состояние JSON-ом")
    ap.add_argument("--plain-shell", action="store_true",
                    help="включить обычную оболочку вместо песочной (без неё хук не запускается)")
    ap.add_argument("--sandbox-shell", action="store_true",
                    help="вернуть песочную оболочку (откат --plain-shell)")
    args = ap.parse_args(argv[1:])

    profile_dir = harness.profile_dir(args.profile)
    patch_path = Path(args.path) if args.path else profile_dir / "cordis.patch.yml"

    if args.write_hooks:
        result = write_hooks()
        print(json.dumps(result, ensure_ascii=False))
        print(json.dumps(merge_shared(), ensure_ascii=False))
        return 0
    if args.verify:
        result = verify_commands()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    if args.plain_shell or args.sandbox_shell:
        change = harness.apply_plain_shell(args.profile, enable=args.plain_shell,
                                           dry_run=args.dry_run)
        print(json.dumps(change, ensure_ascii=False, indent=2))
        if not change.get("ok"):
            return 5
        print("  (изменение оболочки вступит в силу после перезапуска HARNESS)")
        if args.plain_shell:
            return 0

    print(f"профиль: {args.profile} ({profile_dir})")
    print(f"патч:    {patch_path}")
    problems = preflight()
    for p in problems:
        print(f"  ПРОБЛЕМА: {p}")

    state = harness.state(args.profile)
    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=2))

    if args.check:
        return print_status(args.profile) if not problems else 2

    # 1. Пакеты профиля: мост и протокол.
    if not args.no_plugin and not args.remove:
        if not state["bridge"]["installed"] or (
                state["protocol"]["in_build"] is False and not state["protocol"]["installed"]):
            print("шаг 1: установка моста хуков в профиль")
            install = harness.install_bridge(args.profile, dry_run=args.dry_run)
            print(json.dumps(install, ensure_ascii=False, indent=2))
            if not install.get("ok") and not args.dry_run:
                print("  установка не удалась — дальше не идём, профиль не менялся")
                return 3
        else:
            print("шаг 1: мост и протокол уже в профиле")

    if args.dry_run:
        print(json.dumps({"hooks": hooks_config(), "patch": str(patch_path)},
                         ensure_ascii=False, indent=2))
        return 0

    # 2. Команды хуков в безопасной форме + свод.
    print("шаг 2: сборка harness/hooks.json без кавычек")
    print(json.dumps(write_hooks(), ensure_ascii=False))
    merged = merge_shared()
    print(f"  свод: код {merged['code']}")

    # 3. Проверка команд до правки профиля: сломанная команда не должна попасть в патч.
    print("шаг 3: проверка команд (разбор PowerShell + запуск selftest)")
    verified = verify_commands()
    for event, info in verified["events"].items():
        print(f"  {event}: разбор {'ок' if info['parse']['ok'] else 'ОШИБКА'}"
              f" | {info['command']}")
        if not info["parse"]["ok"]:
            print(f"     {info['parse'].get('err', '')[:200]}")
    codes = {k: v["code"] for k, v in verified["selftest"]["checks"].items()}
    print(f"  selftest: {codes}")
    if not verified["ok"]:
        print("  команда не проходит в одной из оболочек — профиль не меняю")
        return 4

    # 4. Запись в профиль.
    data = load_patch(patch_path)
    print(f"  записей гейта: {'есть' if has_entry(data) else 'нет'}"
          f" | чужих хуков: {', '.join(other_hook_ids(data)) or '—'}")
    if problems:
        return 2
    if args.remove and not has_entry(data):
        print("  записи гейта в патче нет — удалять нечего")
        return 0
    if not patch_needs_change(args, data):
        print("  запись уже есть — правка не нужна (идемпотентно)")
        _final_note(args)
        return 0
    if not patch_path.exists():
        print("  патч профиля не найден — установка невозможна (проверьте путь)")
        return 2

    original = patch_path.read_text(encoding="utf-8")
    backup = patch_path.with_name(patch_path.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(patch_path, backup)
    print(f"  резервная копия: {backup}")

    shared = REPO / "harness" / "all-hooks.json"
    if args.remove:
        blocks = [b for b in original.split("\n- insert:") if f"id: {ENTRY_ID}" not in b]
        text = "\n- insert:".join(blocks) if len(blocks) > 1 else original
    else:
        text = original.rstrip("\n") + "\n" + entry_block(shared)

    patch_path.write_text(text, encoding="utf-8", newline="\n")
    after = load_patch(patch_path)
    failure = ""
    if after is None:
        failure = "после правки YAML не разбирается"
    elif args.remove and has_entry(after):
        failure = "запись гейта осталась на месте"
    elif not args.remove and not has_entry(after):
        failure = "запись гейта не появилась"
    else:
        lost = set(other_hook_ids(data)) - set(other_hook_ids(after))
        if lost:
            failure = f"потеряны чужие записи хуков: {', '.join(sorted(lost))}"
    if failure:
        patch_path.write_text(original, encoding="utf-8", newline="\n")
        print(f"  ОТКАТ: {failure}")
        return 3

    print("  Готово." if not args.remove else "  Запись гейта удалена.")
    _final_note(args)
    return 0


def patch_needs_change(args, data: list | None) -> bool:
    """Нужна ли правка патча: записи нет, либо её configPath смотрит в другое место."""
    if args.remove:
        return has_entry(data)
    if not has_entry(data):
        return True
    shared = str(REPO / "harness" / "all-hooks.json")
    for entry in entries_of(data):
        if str(entry.get("id") or "") == ENTRY_ID:
            config = (entry.get("config") or {})
            return str(config.get("configPath") or "") != shared
    return True


def _final_note(args) -> None:
    if args.remove:
        return
    print("  Дальше: перезапустить HARNESS — конфиг хуков читается один раз при")
    print("  старте процесса. После перезапуска проверить: python tools/install_harness_gate.py --check")
    print("  (строка «вызовы хуков» должна показать свежее время).")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
