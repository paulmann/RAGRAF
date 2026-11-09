"""Проверки плагина RAGRAF для DeepSeek Harness (§ Global Plugins).

Стиль файла — как у остальных проверок проекта: самостоятельный скрипт с
функцией `check`, без pytest-фикстур (`python tests/test_dsh_plugin.py`).

Что проверяется и почему именно это:

1. **Контракт пакета.** Клиент ставит пакет только если у него есть
   `dsh.bundle.patch`, объявленный `engines.dsh` и существующая точка входа.
   Ошибка здесь видна не сообщением, а тем, что плагин молча не поднимается.
2. **Слой патча не содержит машинных путей.** Пакет один для всех машин; путь,
   вписанный литералом, означает «работает только у автора» — ровно та причина,
   по которой прежняя установка жила в профиле и разъезжалась.
3. **Сборка хуков.** Режим гейта обязан быть `deny` (в `warn` гейт только
   подсказывает, и подсказку игнорируют — измерено), чужие хуки (xerj, bsg)
   обязаны остаться дословно, команды — без кавычек в начале (PowerShell даёт
   ParserError, код 1, замерено 02.11.2026).
4. **Снятие прежних записей профиля.** Приманки обязаны выжить: чужая настройка
   `ui-chat` и чужой MCP-сервер `mcp-xerj`. Это проверка §0.0 правил: сначала
   песочница с приманками, потом боевая правка.
5. **Действующий бюджет инструкций.** Разбор развёрнутого дерева: верхняя
   запись `agent-instructions` отключена базовым слоем, действует бюджет
   пресета. Ошибка в этом разборе — это ложное «всё хорошо» при отброшенном
   файле правил (замер 03.11.2026: 65 536 против 74 442 байт).
6. **Fail-open хука.** Без рабочей копии RAGRAF стадия обязана завершиться кодом
   0 и назвать причину в stderr: сломанный гейт не имеет права ломать сеанс.
7. **Индекс рынка (format 1).** Проверяется ВАЛИДАТОРОМ САМОГО КЛИЕНТА,
   извлечённым из установленной сборки, — включая отрицательный контроль:
   испорченный индекс обязан быть отвергнут. Если клиента нет, проверка честно
   пропускается (SKIP), а не выдаёт «ок».
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUNDLE = REPO / "integrations" / "dsh-plugin"
INSTALLER = REPO / "tools" / "install_dsh_plugin.py"
DISPATCH = BUNDLE / "scripts" / "dispatch.py"
PY = sys.executable

PASS = 0
FAIL = 0
SKIP = 0


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


def skip(title: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {title} — {why}")


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------- 1. пакет


def section_package() -> dict:
    print("\n1. Контракт пакета-плагина")
    package = json.loads((BUNDLE / "package.json").read_text(encoding="utf-8"))
    check(package.get("name") == "ragraf-dsh", "имя пакета ragraf-dsh", package.get("name", ""))
    check(bool((package.get("dsh") or {}).get("bundle", {}).get("patch")),
          "объявлен dsh.bundle.patch")
    patch_name = (package.get("dsh") or {}).get("bundle", {}).get("patch", "")
    patch = BUNDLE / patch_name.lstrip("./")
    check(patch.is_file(), "файл слоя патча существует", str(patch))
    check(bool((package.get("engines") or {}).get("dsh")),
          "объявлен engines.dsh (совместимость с версией клиента)")
    deps = package.get("dependencies") or {}
    check("@deepseek-ai/dsh-hooks-claude-code" in deps,
          "мост хуков объявлен зависимостью (не зависит от сборки клиента)")
    check("@deepseek-ai/dsh-hook-protocol" in deps,
          "протокол хуков объявлен зависимостью (мост импортирует его первым)")
    entry = BUNDLE / str(package.get("main") or "lib/index.js")
    check(entry.is_file(), "точка входа существует", str(entry))
    text = patch.read_text(encoding="utf-8") if patch.is_file() else ""
    for entry_id in ("mcp-ragraf", "hooks-ragraf", "agent-instructions"):
        check(entry_id in text, f"слой патча объявляет запись {entry_id}")
    check("C:\\AutoClaw" not in text and "C:/AutoClaw" not in text,
          "в слое патча нет машинных путей")
    dispatch = BUNDLE / "scripts" / "dispatch.py"
    check(dispatch.is_file(), "dispatch.py входит в пакет", str(dispatch))
    return package


# ------------------------------------------------------------------ 2-3. хуки


def section_hooks() -> None:
    print("\n2. Сборка конфига хуков")
    module = load(INSTALLER, "install_dsh_plugin")
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "RAGRAF"
        (repo / "harness").mkdir(parents=True)
        (Path(tmp) / "tools" / "xerj").mkdir(parents=True)
        (Path(tmp) / "tools" / "xerj" / "hooks.json").write_text(json.dumps({
            "hooks": {"SessionStart": [{"hooks": [{
                "type": "command", "timeout": 15,
                "command": "pwsh -NoLogo -File C:\\tools\\xerj\\session-hook.ps1"}]}]}
        }), encoding="utf-8")
        (repo / "harness" / "hooks.json").write_text(json.dumps({
            "hooks": {
                "SessionStart": [{"hooks": [{"type": "command", "timeout": 30,
                                             "command": "python harness_gate.py session-start --mode=warn"}]}],
                "PreToolUse": [{"matcher": "pwsh|grep", "hooks": [
                    {"type": "command", "timeout": 60,
                     "command": "python harness_gate.py pre-tool --mode=warn"}]}],
            }
        }), encoding="utf-8")
        module.REPO = repo
        dispatch = Path(tmp) / "dsh" / "ragraf" / "dispatch.py"
        data = module.build_hooks("C:\\py\\python.exe", dispatch, "deny")
        raw = json.dumps(data, ensure_ascii=False)
        check(data["hook_mode"] == "deny", "режим записан как deny")
        check("--mode=deny" in raw and "--mode=warn" not in raw,
              "прежний режим warn заменён на deny")
        check(any("xerj" in h.get("command", "") for g in data["hooks"]["SessionStart"]
                  for h in g["hooks"]),
              "чужие хуки (xerj) перенесены дословно")
        check(any(g.get("matcher") == "pwsh|grep" for g in data["hooks"]["PreToolUse"]),
              "матчер PreToolUse сохранён")
        staged = [h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"]
                  if "dispatch.py" in h.get("command", "")]
        check(staged and all("hook pre-tool" in c for c in staged),
              "стадия RAGRAF идёт через dispatch.py", str(staged))
        check(all(not c.startswith('"') for c in staged),
              "команды не начинаются с кавычки (PowerShell ParserError)")
        check(all("harness_gate.py" not in c for g in data["hooks"].values()
                  for grp in g for c in [h.get("command", "") for h in grp["hooks"]]),
              "путь к рабочей копии не вписан в команды")
        empty = module.build_hooks("python", dispatch, "deny")
        check(bool(empty.get("hooks")) and "deny" in json.dumps(empty),
              "без проектных источников стадии берутся встроенные, а не пустой файл")
        quoted = '"C:\\Program Files\\PowerShell\\7\\pwsh.exe" -NoLogo -File x.ps1'
        fixed = module.normalize_command(quoted)
        check(not fixed.startswith('"') and " " not in fixed.split(" ")[0],
              "команда с пробелом в пути переписана без кавычек", fixed)
        check("pwsh.exe" in fixed.lower() or "POWERS~1" in fixed.upper(),
              "исполняемый файл назван в короткой форме", fixed)
        plain = "python x.py hook stop"
        check(module.normalize_command(plain) == plain,
              "команда без кавычек не меняется")


# ------------------------------------------------- 4. снятие прежних записей


def section_strip() -> None:
    print("\n3. Снятие прежних записей профиля (приманки обязаны выжить)")
    module = load(INSTALLER, "install_dsh_plugin")
    with tempfile.TemporaryDirectory() as tmp:
        patch = Path(tmp) / "cordis.patch.yml"
        patch.write_text(
            "- id: ui-chat\n  name: '@deepseek-ai/dsh-client-ui-chat'\n"
            "  config:\n    transcriptView: compact\n"
            "# пояснение к удаляемой записи\n"
            "- insert:\n    - id: mcp-ragraf\n      name: '@deepseek-ai/dsh-mcp-client'\n"
            "# пояснение к чужому серверу (обязано остаться)\n"
            "- insert:\n    - id: mcp-xerj\n      name: '@deepseek-ai/dsh-mcp-client'\n"
            "- insert:\n    - id: hooks-shared\n      name: '@deepseek-ai/dsh-hooks-claude-code'\n"
            "- id: agent-instructions\n  name: '@deepseek-ai/dsh-agent-instructions'\n"
            "  config:\n    maxBytes: 65536\n", encoding="utf-8")
        removed = module.strip_legacy(patch, "test")
        text = patch.read_text(encoding="utf-8")
        check(sorted(removed) == ["hooks-shared", "mcp-ragraf"],
              "сняты именно прежние записи", str(removed))
        check("ui-chat" in text, "приманка ui-chat осталась")
        check("mcp-xerj" in text, "приманка mcp-xerj осталась")
        check("пояснение к чужому серверу" in text,
              "комментарий соседней записи не снесён вместе с удаляемой")
        check("пояснение к удаляемой записи" not in text,
              "комментарий удаляемой записи ушёл вместе с ней")
        check("maxBytes: 65536" in text, "чужая запись agent-instructions не тронута")
        check(patch.with_name("cordis.patch.yml.bak-test").is_file(),
              "создана копия перед правкой")
        again = module.strip_legacy(patch, "test2")
        check(again == [], "повторный прогон ничего не меняет (идемпотентность)")
        check(not patch.with_name("cordis.patch.yml.bak-test2").exists(),
              "второй прогон не плодит копии")


# --------------------------------------------------------- 5. бюджет инструкций


DUMP_TOP = """- id: agent-instructions
  name: '@deepseek-ai/dsh-agent-instructions'
  config:
    maxBytes: 196608
"""

DUMP_PRESET = """- id: agent-instructions
  name: '@deepseek-ai/dsh-agent-instructions'
  config:
    maxBytes: 196608
  disabled: true
- id: preset-standard
  name: '@deepseek-ai/dsh-agent-preset'
  config:
    plugins:
      - id: agent-instructions
        name: '@deepseek-ai/dsh-agent-instructions'
        config:
          maxBytes: 65536
"""


def section_budget() -> None:
    print("\n4. Действующий бюджет инструкций")
    module = load(INSTALLER, "install_dsh_plugin")
    top = module.budget_report(DUMP_TOP)
    check(top["maxBytes"] == 196608, "включённая запись профиля действует", str(top))
    preset = module.budget_report(DUMP_PRESET)
    check(preset["maxBytes"] == 65536,
          "отключённая верхняя запись не действует: считан бюджет пресета", str(preset))
    check(preset["source"].startswith("пресет"), "источник назван", preset["source"])
    empty = module.budget_report("- id: other\n  name: x\n")
    check(empty["maxBytes"] is None, "пустое дерево не даёт выдуманного бюджета")


# ------------------------------------------------------------------ 6. dispatch


def section_dispatch() -> None:
    print("\n5. dispatch.py: поиск установки и fail-open хука")
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "fake-ragraf"
        (fake / "tools").mkdir(parents=True)
        (fake / "src" / "ragraf").mkdir(parents=True)
        (fake / "tools" / "ragraf-mcp.py").write_text("# stub\n", encoding="utf-8")
        (fake / "src" / "ragraf" / "__init__.py").write_text("# stub\n", encoding="utf-8")
        env = {**os.environ, "RAGRAF_HOME": str(fake), "DSH_HOME": str(Path(tmp) / "dsh")}
        result = subprocess.run([PY, str(DISPATCH), "where"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", env=env)
        found = json.loads(result.stdout or "{}")
        check(found.get("mode") == "checkout", "RAGRAF_HOME распознан как рабочая копия",
              result.stdout + result.stderr)
        result = subprocess.run([PY, str(DISPATCH), "hook", "pre-tool"], input="{}",
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", env=env)
        check(result.returncode == 0, "хук без harness_gate.py завершается кодом 0 (fail-open)")
        check("хук пропущен" in (result.stderr or ""), "причина названа в stderr",
              result.stderr)
        bad = {**os.environ, "RAGRAF_HOME": str(Path(tmp) / "нет-такого"),
               "DSH_HOME": str(Path(tmp) / "dsh")}
        result = subprocess.run([PY, str(DISPATCH), "where"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", env=bad)
        check(result.returncode == 1, "неверный RAGRAF_HOME не подменяется догадкой")
        result = subprocess.run([PY, str(DISPATCH), "doctor", "--profile", "desktop"],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", env=bad)
        report = json.loads(result.stdout or "{}")
        check(isinstance(report.get("checks"), list) and report["checks"],
              "doctor отдаёт проверки структурой")
        check(any(c["check"] == "ragraf" and c["status"] == "fail" for c in report["checks"]),
              "отсутствие RAGRAF названо провалом, а не умолчанием")


# ---------------------------------------------------- 7. индекс рынка плагинов


def _asar_files(path: Path) -> tuple[dict, int]:
    with path.open("rb") as handle:
        head = handle.read(16)
        pickle_size = struct.unpack("<I", head[4:8])[0]
        json_len = struct.unpack("<I", head[12:16])[0]
        header = json.loads(handle.read(json_len).decode("utf-8"))
    return header, 8 + pickle_size


def _walk(node: dict, prefix: str = ""):
    for name, meta in node.get("files", {}).items():
        full = f"{prefix}/{name}" if prefix else name
        if "files" in meta:
            yield from _walk(meta, full)
        else:
            yield full, meta


def section_market_index() -> None:
    print("\n6. Индекс рынка плагинов (format 1)")
    module = load(REPO / "tools" / "build_dsh_market_index.py", "build_index")
    index = module.build("a" * 40, 1793600000, "0.1.0", "https://github.com/paulmann/RAGRAF")
    check(index["format"] == 1, "format = 1")
    check(index["plugins"][0]["versions"][0]["source"]["type"] == "git",
          "источник git с закреплённым коммитом")
    check(len(index["plugins"][0]["versions"][0]["source"]["commit"]) == 40,
          "коммит 40 hex-символов")
    shipped = BUNDLE / "index.json"
    if shipped.is_file():
        data = json.loads(shipped.read_text(encoding="utf-8"))
        check(data.get("format") == 1, "поставленный index.json объявляет format 1")
        check(data["plugins"][0]["name"] == json.loads(
            (BUNDLE / "package.json").read_text(encoding="utf-8"))["name"],
            "имя плагина в индексе совпадает с package.json")

    asar = Path(os.environ.get("LOCALAPPDATA")
                or (Path(os.environ.get("USERPROFILE") or ".") / "AppData" / "Local"))
    asar = asar / "Programs" / "DeepSeek Harness" / "resources" / "app.asar"
    if not asar.is_file():
        skip("проверка валидатором клиента", "сборка DSH не найдена")
        return
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "app"
        header, base = _asar_files(asar)
        wanted = ("dsh/node_modules/@deepseek-ai/dsh-plugin-market-index/lib/",
                  "dsh/node_modules/semver/")
        with asar.open("rb") as handle:
            for full, meta in _walk(header):
                if not full.startswith(wanted):
                    continue
                target = out / full
                target.parent.mkdir(parents=True, exist_ok=True)
                handle.seek(base + int(meta["offset"]))
                target.write_bytes(handle.read(int(meta["size"])))
        validator = out / "dsh/node_modules/@deepseek-ai/dsh-plugin-market-index/lib/index.js"
        if not validator.is_file():
            skip("проверка валидатором клиента", "валидатор не извлёкся")
            return
        script = out / "check.mjs"
        script.write_text(
            "import { parseMarketIndex } from "
            "'./dsh/node_modules/@deepseek-ai/dsh-plugin-market-index/lib/index.js';\n"
            "import fs from 'node:fs';\n"
            "const url = 'https://example.invalid/index.json';\n"
            "const good = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));\n"
            "parseMarketIndex(good, url);\n"
            "const bad = JSON.parse(JSON.stringify(good));\n"
            "bad.format = 2;\n"
            "let rejected = false;\n"
            "try { parseMarketIndex(bad, url); } catch { rejected = true; }\n"
            "console.log(JSON.stringify({ ok: true, negative_rejected: rejected }));\n",
            encoding="utf-8")
        healthy = Path(tmp) / "index.json"
        healthy.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        node = shutil_which("node")
        if not node:
            skip("проверка валидатором клиента", "node не найден")
            return
        result = subprocess.run([node, str(script), str(healthy)], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", cwd=out)
        ok = result.returncode == 0 and '"ok":true' in (result.stdout or "")
        check(ok, "собранный индекс принят валидатором самого клиента",
              (result.stdout or "") + (result.stderr or ""))
        check('"negative_rejected":true' in (result.stdout or ""),
              "испорченный индекс отвергнут (отрицательный контроль)",
              (result.stdout or "") + (result.stderr or ""))


def shutil_which(name: str) -> str | None:
    from shutil import which
    return which(name)


def main() -> int:
    print(f"RAGRAF ↔ DeepSeek Harness: проверки плагина ({REPO})")
    section_package()
    section_hooks()
    section_strip()
    section_budget()
    section_dispatch()
    section_market_index()
    print(f"\nИТОГО: {PASS} прошло, {FAIL} провалов, {SKIP} пропущено")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
