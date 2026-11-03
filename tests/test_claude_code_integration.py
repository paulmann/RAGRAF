# -*- coding: utf-8 -*-
"""Интеграция Claude Code: установка, идемпотентность, откат, хуки.

Семь утверждений, которых чтением не подтвердить:

  1. Установка в чистый каталог делает рабочую конфигурацию: четыре события
     хуков, право `mcp__ragraf__*`, запись MCP, раздел правил, навыки.
  2. Повторная установка НЕ меняет файл и не плодит резервные копии: иначе
     каждый запуск сдвигал бы время правки файла, принадлежащего продукту.
  3. Чужие записи в тех же событиях и правах остаются целыми: установщик
     добавляет своё, а не переписывает чужое.
  4. Откат снимает ровно наше и сохраняет текст владельца в `CLAUDE.md`.
  5. Хук отвечает по протоколу клиента, а не просто «код 0»: `SessionStart`
     обязан вернуть `hookEventName=SessionStart` и непустой контекст.
  6. Метка задачи ставится первым запросом и не меняется вторым — иначе одна
     задача дробилась бы на десять и отчёт по ней показывал бы обрывки.
  7. Проверка ловит порчу: подсадки в копию установщика (пропало событие,
     снята дедупликация, раздел дописывается всегда) обязаны давать отклонение.

Песочница: свой каталог настроек в %TEMP% (`--home`), живая учётная запись
только читается — и это отдельная проверка.
"""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "install_claude_code.py"
HOOK = REPO / "tools" / "harness_gate.py"
INTEG = REPO / "integrations" / "claude-code"
SKILLS = REPO / "docs" / "skills"

PASS = 0
FAIL = 0


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


def run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL)] + args, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def events_of(settings: dict) -> dict:
    return settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}


def ours(entry: dict) -> bool:
    command = str(((entry.get("hooks") or [{}])[0]).get("command") or "")
    return "harness_gate.py" in command


def plugin_checks() -> None:
    """Плагин: манифесты, лаунчер, навык, совпадение с кодом.

    Проверки без модели: манифесты разбираются и сходятся с кодом, лаунчер
    находит установку и отдаёт настоящий MCP-ответ, хук через лаунчер отвечает
    по протоколу, а префикс инструментов совпадает в навыке, в матчере хуков и
    в том, что умеет распознавать гейт.
    """
    import subprocess as sp

    plugin = REPO / "plugin"
    launcher = plugin / "scripts" / "ragraf_launcher.py"
    print("11. Плагин: манифесты и сборка")
    manifest = load(plugin / ".claude-plugin" / "plugin.json")
    market = load(REPO / ".claude-plugin" / "marketplace.json")
    check(manifest.get("name") == "ragraf", "plugin.json называет плагин ragraf",
          str(manifest.get("name")))
    version = ""
    init = (REPO / "src" / "ragraf" / "__init__.py").read_text(encoding="utf-8")
    for line in init.splitlines():
        if line.startswith("__version__"):
            version = line.split('"')[1]
            break
    check(bool(version) and manifest.get("version") == version,
          "версия плагина совпадает с кодом", f"плагин {manifest.get('version')}, код {version}")
    listed = [p for p in market.get("plugins") or [] if p.get("name") == "ragraf"]
    check(bool(listed) and listed[0].get("source") == "./plugin",
          "маркетплейс объявляет плагин из ./plugin", json.dumps(listed[:1])[:200])
    check(listed and listed[0].get("version") == version,
          "версия в маркетплейсе совпадает с кодом", str(listed[0].get("version") if listed else ""))
    check((plugin / ".claude-plugin" / "icon.png").is_file(),
          "иконка каталога на месте (без неё валидатор каталога ругается)")

    print("12. Плагин: хуки, MCP, навык")
    hooks = load(plugin / "hooks" / "hooks.json")
    events = hooks.get("hooks") if isinstance(hooks.get("hooks"), dict) else {}
    check(set(events) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"},
          "в хуках плагина те же четыре события", ", ".join(sorted(events)))
    matcher = str((events.get("PreToolUse") or [{}])[0].get("matcher") or "")
    check("mcp__ragraf__" in matcher and "mcp__plugin_ragraf_ragraf__" in matcher,
          "матчер ловит оба префикса инструментов", matcher)
    mcp = load(plugin / ".mcp.json")
    server = (mcp.get("mcpServers") or {}).get("ragraf") or {}
    args = " ".join(server.get("args") or [])
    check("ragraf_launcher.py" in args and server.get("command") == "python",
          "MCP-сервер плагина запускается через лаунчер", args[:200])
    skill = (plugin / "skills" / "ragraf-core" / "SKILL.md").read_text(encoding="utf-8")
    check("mcp__plugin_ragraf_ragraf__ragraf_" in skill,
          "навык плагина называет имена с префиксом плагина")
    for relative in ("reference/operations.md", "reference/repair.md"):
        same = ((plugin / "skills" / "ragraf-core" / relative).read_bytes()
                == (SKILLS / "ragraf-core" / relative).read_bytes())
        check(same, f"{relative} в плагине побайтово равен проектному")
    variants = load(SKILLS / "ragraf-core" / "VARIANTS.json")
    names = [v.get("name") for v in variants.get("variants") or []]
    check("claude-plugin" in names, "вариант навыка claude-plugin объявлен", ", ".join(map(str, names)))

    print("13. Плагин: лаунчер (без модели)")
    where = sp.run([sys.executable, str(launcher), "where"], capture_output=True,
                   text=True, encoding="utf-8", errors="replace", timeout=120)
    located = {}
    try:
        located = json.loads(where.stdout or "{}")
    except ValueError:
        located = {}
    check(located.get("mode") in ("checkout", "package"),
          "лаунчер находит установку RAGRAF", str(located)[:200])
    init_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2024-11-05",
                                      "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "0"}}})
    server_run = sp.run([sys.executable, str(launcher), "mcp", "--agent", "claude-code"],
                        input=init_req + "\n", capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=300)
    server_info = {}
    for line in (server_run.stdout or "").splitlines():
        if "serverInfo" in line:
            try:
                server_info = json.loads(line).get("result", {}).get("serverInfo", {})
            except ValueError:
                server_info = {}
            break
    check(server_info.get("name") == "ragraf",
          "MCP через лаунчер отвечает на initialize", str(server_info)[:200])
    hook_run = sp.run([sys.executable, str(launcher), "hook", "session-start"],
                      input=json.dumps({"session_id": "t-3", "cwd": str(REPO),
                                        "hook_event_name": "SessionStart",
                                        "source": "startup"}),
                      capture_output=True, text=True, encoding="utf-8",
                      errors="replace", timeout=180)
    hook_json = {}
    try:
        hook_json = json.loads(hook_run.stdout or "{}")
    except ValueError:
        hook_json = {}
    out = hook_json.get("hookSpecificOutput") or {}
    check(out.get("hookEventName") == "SessionStart" and out.get("additionalContext"),
          "хук через лаунчер отвечает по протоколу", str(hook_run.stdout)[:200])

    print("14. Плагин: сборка идемпотентна, валидатор клиента")
    before = (plugin / "skills" / "ragraf-core" / "SKILL.md").read_bytes()
    sync = sp.run([sys.executable, str(TOOL), "--sync-plugin"], capture_output=True,
                  text=True, encoding="utf-8", errors="replace", timeout=180)
    after = (plugin / "skills" / "ragraf-core" / "SKILL.md").read_bytes()
    check(sync.returncode == 0 and before == after,
          "--sync-plugin не перезаписывает текст навыка плагина", (sync.stdout or "")[-200:])
    cli = shutil.which("claude")
    if cli:
        validated = sp.run([cli, "plugin", "validate", str(plugin)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=300)
        check(validated.returncode == 0,
              "claude plugin validate пропускает плагин",
              ((validated.stdout or "") + (validated.stderr or ""))[-300:])
    else:
        check(True, "claude plugin validate: CLI не найден — проверка пропущена (названо)")

    print("15. Community-версия для каталога")
    readme_en = (plugin / "README.md").read_text(encoding="utf-8")
    check("Community" in readme_en and len(readme_en) > 1000,
          "README плагина говорит о Community-издании и не пустой",
          f"символов: {len(readme_en)}")
    check(all(word in readme_en for word in ("Install", "Privacy", "Licence", "Boundaries")),
          "README закрывает установку, приватность, лицензию и границы")
    for name, source in (("LICENSE", REPO / "LICENSE"), ("NOTICE", REPO / "NOTICE"),
                         ("EULA.md", REPO / "community" / "EULA.md")):
        target = plugin / name
        check(target.is_file() and target.read_bytes() == source.read_bytes(),
              f"{name} в плагине совпадает с источником (каталог смотрит лицензию в папке)")
    check(bool(manifest.get("displayName")),
          "у плагина есть displayName (слаг name неизменяем, подпись — отдельно)",
          str(manifest.get("displayName")))

    print("16. Сборка пакета и бланк подачи")
    out = base_dir() / "dist"
    packaged = sp.run([sys.executable, str(REPO / "tools" / "package_plugin.py"),
                       "--out", str(out), "--no-validate"], capture_output=True,
                      text=True, encoding="utf-8", errors="replace", timeout=600)
    archive = out / f"ragraf-community-{version}.zip"
    check(packaged.returncode == 0 and archive.is_file(),
          "пакет Community собирается и лежит в dist/", (packaged.stdout or "")[-300:])
    form_path = out / f"ragraf-community-{version}.submission.json"
    form = {}
    if form_path.is_file():
        try:
            form = json.loads(form_path.read_text(encoding="utf-8"))
        except ValueError:
            form = {}
    check(form.get("repository") == "paulmann/RAGRAF" and form.get("plugin_path") == "plugin",
          "бланк подачи называет репозиторий и папку плагина", json.dumps(form)[:200])
    check(form.get("edition") == "community" and form.get("version") == version,
          "бланк подачи объявляет Community и версию",
          f"{form.get('edition')} {form.get('version')}")
    if archive.is_file():
        import hashlib

        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        check(form.get("package_sha256") == digest,
              "sha256 в бланке совпадает с пакетом")
    else:
        check(False, "sha256 в бланке совпадает с пакетом", "пакета нет")


def base_dir() -> Path:
    """Отдельный каталог для сборки пакета: живое dist/ тест не трогает."""
    path = Path(tempfile.mkdtemp(prefix="ragraf-plugin-dist-"))
    return path


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="ragraf-claude-"))
    home = base / "home"
    home.mkdir(parents=True)

    print("1. Проверка на пустом каталоге")
    empty = run(["--home", str(home), "--no-cli", "--check"])
    check(empty.returncode == 1, "--check на пустом каталоге даёт код 1",
          f"код {empty.returncode}: {(empty.stdout or '')[-300:]}")

    print("2. Установка")
    first = run(["--home", str(home), "--no-cli"])
    check(first.returncode == 0, "установка завершилась кодом 0",
          (first.stdout or "")[-400:] + (first.stderr or "")[-200:])
    settings_path = home / "settings.json"
    memory_path = home / "CLAUDE.md"
    state_path = home / "claude.json"
    settings = load(settings_path) if settings_path.exists() else {}
    events = events_of(settings)
    check(set(events) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"},
          "в настройках ровно четыре события протокола", ", ".join(sorted(events)))
    check(all(len(entries) == 1 for entries in events.values()),
          "у каждого события ровно одна запись", str({k: len(v) for k, v in events.items()}))
    matchers = {event: entry[0].get("matcher") for event, entry in events.items()}
    check(matchers.get("PreToolUse") and "Grep" in str(matchers.get("PreToolUse")),
          "matcher стоит у PreToolUse и покрывает файловый поиск", str(matchers))
    check(all(not matchers.get(event) for event in matchers if event != "PreToolUse"),
          "у остальных событий matcher пуст (протокол его не применяет)", str(matchers))
    commands = [entry[0]["hooks"][0]["command"] for entry in events.values()]
    check(all(not command.startswith('"') for command in commands),
          "команды хуков начинаются не с кавычки", str(commands)[:300])
    check(all(HOOK.name in command for command in commands),
          "команды ведут в tools/harness_gate.py", str(commands)[:300])
    python_path = Path(commands[0].split(" ")[0])
    check(python_path.exists(), "интерпретатор из команды существует", str(python_path))
    allow = ((settings.get("permissions") or {}).get("allow") or [])
    check(allow == ["mcp__ragraf__*"], "право mcp__ragraf__* выдано ровно одно", str(allow))
    memory = memory_path.read_text(encoding="utf-8-sig") if memory_path.exists() else ""
    check(memory.count("== RAGRAF") == 1 and memory.count("== конец блока RAGRAF") == 1,
          "раздел правил в CLAUDE.md добавлен один раз",
          f"маркеров: {memory.count('== RAGRAF')}")
    check("ensure" in memory and "mcp__ragraf__" in memory,
          "раздел правил говорит про ensure и полные имена инструментов")
    check(all((home / "skills" / name / "SKILL.md").exists()
              for name in ("ragraf-core", "ragraf-secrets")),
          "навыки развёрнуты оба")
    same_bytes = ((home / "skills" / "ragraf-core" / "SKILL.md").read_bytes()
                  == (SKILLS / "ragraf-core" / "SKILL.md").read_bytes())
    check(same_bytes, "навык побайтово равен проектному (вариант объявлен identical)")
    state = load(state_path) if state_path.exists() else {}
    entry = (state.get("mcpServers") or {}).get("ragraf") or {}
    check(entry.get("type") == "stdio" and "--agent" in (entry.get("args") or []),
          "MCP-сервер записан с типом stdio и именем агента", json.dumps(entry)[:200])
    check((entry.get("env") or {}).get("PYTHONIOENCODING") == "utf-8",
          "кодировка потока задана в окружении сервера")

    print("3. Повторная установка (идемпотентность)")
    before = settings_path.read_bytes()
    backups_before = len(list(home.glob("settings.json.bak-*")))
    second = run(["--home", str(home), "--no-cli"])
    check(second.returncode == 0, "повторная установка завершилась кодом 0",
          (second.stdout or "")[-300:])
    check(settings_path.read_bytes() == before, "файл настроек не изменился побайтово")
    check(len(list(home.glob("settings.json.bak-*"))) == backups_before,
          "новая резервная копия не создана")
    check(len(events_of(load(settings_path))) == 4 and
          all(len(v) == 1 for v in events_of(load(settings_path)).values()),
          "событий по-прежнему по одной записи")

    print("4. Чужие записи не трогаются")
    foreign = base / "foreign"
    foreign.mkdir()
    (foreign / "settings.json").write_text(json.dumps({
        "hooks": {"PostToolUse": [{"matcher": "Edit",
                                   "hooks": [{"type": "command",
                                              "command": "echo свой-хук"}]}]},
        "permissions": {"allow": ["Bash(git status)"]},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    run(["--home", str(foreign), "--no-cli"])
    merged = load(foreign / "settings.json")
    check("PostToolUse" in events_of(merged)
          and events_of(merged)["PostToolUse"][0]["hooks"][0]["command"] == "echo свой-хук",
          "чужой хук сохранился")
    check("Bash(git status)" in ((merged.get("permissions") or {}).get("allow") or []),
          "чужое правило allow сохранилось")
    check(len([e for e in events_of(merged).get("PreToolUse") or [] if ours(e)]) == 1,
          "наш хук добавлен рядом с чужим", json.dumps(events_of(merged))[:200])
    removed = run(["--home", str(foreign), "--no-cli", "--remove"])
    after_remove = load(foreign / "settings.json")
    check(removed.returncode == 0 and "PreToolUse" not in events_of(after_remove),
          "откат снял наши хуки", (removed.stdout or "")[-300:])
    check("PostToolUse" in events_of(after_remove),
          "откат оставил чужой хук")
    check("mcp__ragraf__*" not in ((after_remove.get("permissions") or {}).get("allow") or []),
          "откат снял наше правило allow")

    print("5. Откат")
    run(["--home", str(home), "--no-cli", "--remove"])
    check(not settings_path.exists() or not events_of(load(settings_path)),
          "после отката хуков в настройках нет")
    check(not memory_path.exists()
          or "== RAGRAF" not in memory_path.read_text(encoding="utf-8-sig"),
          "после отката раздел правил снят")
    check(not (home / "skills").exists(), "после отката каталог навыков убран")
    state_after = load(state_path) if state_path.exists() else {}
    check("ragraf" not in (state_after.get("mcpServers") or {}),
          "после отката записи MCP нет")
    check(run(["--home", str(home), "--no-cli", "--check"]).returncode == 1,
          "после отката --check даёт код 1")

    print("6. Сухой прогон и изоляция живой учётной записи")
    dry = base / "dry"
    dry.mkdir()
    dry_run = run(["--home", str(dry), "--no-cli", "--dry-run"])
    check(dry_run.returncode == 0 and not any(dry.iterdir()),
          "сухой прогон ничего не создаёт", str(list(dry.iterdir()))[:200])
    live_settings = Path.home() / ".claude" / "settings.json"
    live_before = live_settings.read_bytes() if live_settings.exists() else b""
    run(["--home", str(base / "iso"), "--no-cli"])
    live_after = live_settings.read_bytes() if live_settings.exists() else b""
    check(live_before == live_after, "установка в песочницу не меняет живой settings.json")

    print("7. Хуки на пробном payload")
    session = subprocess.run(f'"{sys.executable}" "{HOOK}" session-start', shell=True,
                             input=json.dumps({"session_id": "t-1", "cwd": str(REPO),
                                               "hook_event_name": "SessionStart",
                                               "source": "startup"}),
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=180)
    try:
        payload = json.loads(session.stdout or "{}")
    except ValueError:
        payload = {}
    hook_out = payload.get("hookSpecificOutput") or {}
    check(session.returncode == 0 and hook_out.get("hookEventName") == "SessionStart",
          "SessionStart отвечает по протоколу",
          (session.stdout or "")[:200] + (session.stderr or "")[:200])
    context = str(hook_out.get("additionalContext") or "")
    check("RAGRAF" in context and len(context) > 40,
          "в контексте сеанса есть строка о корпусе", context[:200])
    pre = subprocess.run(f'"{sys.executable}" "{HOOK}" pre-tool', shell=True,
                         input=json.dumps({"session_id": "t-1", "cwd": str(REPO),
                                           "hook_event_name": "PreToolUse",
                                           "tool_name": "Grep",
                                           "tool_input": {"pattern": "facade"}}),
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=180)
    try:
        pre_payload = json.loads(pre.stdout or "{}")
    except ValueError:
        pre_payload = {}
    decision = (pre_payload.get("hookSpecificOutput") or {}).get("permissionDecision")
    check(pre.returncode == 0 and decision in ("allow", "ask", "deny"),
          "PreToolUse отдаёт решение allow/ask/deny", str(decision))

    print("8. Метка задачи (состояние гейта — в песочнице)")
    state_file = base / "gate_state.json"
    spec = importlib.util.spec_from_file_location("ragraf_hook_under_test", HOOK)
    hook_mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(hook_mod)
    from ragraf import gate as gate_mod  # noqa: E402 — после загрузки хука

    saved = gate_mod.state_path
    gate_mod.state_path = lambda: state_file
    try:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            hook_mod.prompt({"session_id": "t-2", "cwd": str(REPO),
                             "prompt": "  проверь   интеграцию  claude  "})
        task = gate_mod.state().current_task(str(REPO))
        check(task.startswith("проверь интеграцию"), "первый запрос стал меткой задачи",
              f"метка: {task!r}; вывод: {buffer.getvalue()[:200]}")
        buffer2 = io.StringIO()
        with redirect_stdout(buffer2):
            hook_mod.prompt({"session_id": "t-2", "cwd": str(REPO),
                             "prompt": "совсем другой запрос"})
        check(gate_mod.state().current_task(str(REPO)) == task and not buffer2.getvalue(),
              "второй запрос метку не меняет", buffer2.getvalue()[:200])
    finally:
        gate_mod.state_path = saved

    print("9. Шаблоны и описание")
    template = load(INTEG / "claude.template.json")
    check("ragraf" in (template.get("mcpServers") or {}),
          "шаблон MCP называет сервер ragraf")
    settings_template = load(INTEG / "settings.template.json")
    check(set(events_of(settings_template)) ==
          {"SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"},
          "шаблон хуков описывает те же четыре события",
          ", ".join(sorted(events_of(settings_template))))
    readme = (INTEG / "README.md").read_text(encoding="utf-8-sig")
    check(all(word in readme for word in ("## Подключение", "## Проверка", "## Границы")),
          "README называет подключение, проверку и границы")
    help_out = run(["--help"])
    check(all(flag in (help_out.stdout or "") for flag in ("--check", "--remove", "--dry-run")),
          "установщик объявляет check/remove/dry-run")

    print("10. Подсадки: проверка обязана ловить порчу")
    mutants = base / "mutants"

    def mutant(name: str, old: str, new: str) -> Path:
        root = mutants / name
        (root / "tools").mkdir(parents=True, exist_ok=True)
        # Текст правил нужен и подсадке: установщик берёт его из
        # integrations/claude-code/CLAUDE-section.md относительно своего корня.
        target = root / "integrations" / "claude-code"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(INTEG / "CLAUDE-section.md", target / "CLAUDE-section.md")
        text = TOOL.read_text(encoding="utf-8")
        assert old in text, f"подсадка не нашла текст: {name}"
        (root / "tools" / "install_claude_code.py").write_text(
            text.replace(old, new, 1), encoding="utf-8")
        return root / "tools" / "install_claude_code.py"

    def run_mutant(path: Path, home_dir: Path) -> subprocess.CompletedProcess:
        home_dir.mkdir(parents=True, exist_ok=True)
        return subprocess.run([sys.executable, str(path), "--home", str(home_dir),
                               "--no-cli", "--no-skills"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=300)

    m1 = mutant("no-pretool", '    ("PreToolUse", "pre-tool", 60, "pwsh|Bash|read|Read|grep|Grep|glob|Glob|mcp__ragraf__.*"),\n', "")
    m1_home = base / "m1-home"
    run_mutant(m1, m1_home)
    m1_events = events_of(load(m1_home / "settings.json")) if (m1_home / "settings.json").exists() else {}
    check(len(m1_events) == 3 and "PreToolUse" not in m1_events,
          "подсадка «снят PreToolUse» видна: событий стало три", ", ".join(sorted(m1_events)))

    m2 = mutant("no-dedupe",
                "            if our_hooks(entry):\n                removed += 1\n            else:\n                kept.append(entry)",
                "            kept.append(entry)")
    m2_home = base / "m2-home"
    run_mutant(m2, m2_home)
    run_mutant(m2, m2_home)
    m2_events = events_of(load(m2_home / "settings.json")) if (m2_home / "settings.json").exists() else {}
    duplicated = any(len(entries) > 1 for entries in m2_events.values())
    check(duplicated, "подсадка «снята дедупликация» видна: записи задвоились",
          str({k: len(v) for k, v in m2_events.items()}))

    m3 = mutant("memory-always", '    if has_section(text):\n        report["action"] = "уже есть"\n        return report\n', "")
    m3_home = base / "m3-home"
    run_mutant(m3, m3_home)
    run_mutant(m3, m3_home)
    m3_memory = (m3_home / "CLAUDE.md").read_text(encoding="utf-8-sig") \
        if (m3_home / "CLAUDE.md").exists() else ""
    blocks = m3_memory.count("== конец блока RAGRAF")
    check(blocks > 1,
          "подсадка «раздел дописывается всегда» видна: блоков больше одного",
          f"блоков: {blocks}")

    shutil.rmtree(base, ignore_errors=True)
    plugin_checks()
    print(f"\nпроверок: {PASS + FAIL}, провалов: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
