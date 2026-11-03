"""Поддержка новой версии DeepSeek Harness: сборка моста хуков и его окружения.

Зачем этот модуль
    В HARNESS 44.7.0 (пакеты 0.2.1-rc.5) мост хуков
    `@deepseek-ai/dsh-hooks-claude-code` больше **не входит в сборку**: в
    `resources/app.asar` его нет вовсе, а `@deepseek-ai/dsh-hook-protocol`
    (его обязательный peer) отсутствует там же. Запись `hooks-shared` в профиле
    осталась, но указывает на пакет, которого нет; клиент молча не монтирует
    мост, хуки не вызываются, и гейт RAGRAF не получает ни одного события
    (`hook/invoked` в стенограммах нет с 23.10.2026 — измерено).

    Поэтому установка гейта больше не сводится к правке YAML профиля: нужно
    (1) поставить мост и протокол в профиль, (2) выровнять версию с рантаймом,
    (3) записать команды хуков в форме, которую понимает оболочка клиента,
    (4) проверить, что хуки действительно вызываются.

Что здесь есть
    * разбор установки HARNESS: корень, версия приложения (`version`),
      версия рантайма (из `app.asar`), путь к CLI `dsh`;
    * состояние профиля: запись моста в `cordis.patch.yml`, наличие пакета в
      `node_modules` профиля, наличие `dsh-hook-protocol`, файл хуков;
    * установка моста через родную команду `dsh plugin --profile <p> add`,
      с резервными копиями и откатом;
    * чтение стенограмм сеансов (zstd) — последнее событие `hook/invoked`;
    * построение команд хуков, которые не ломаются в PowerShell.

Почему команда хука строится здесь, а не пишется руками
    Мост исполняет команду хука через оболочку клиента. На этой машине это
    PowerShell 7, а он **не принимает строку, начинающуюся с кавычки**:
    `"C:\\path\\python.exe" script.py` даёт `ParserError: Unexpected token`,
    код 1 (замерено 02.11.2026 — ровно так падал прежний хук RAGRAF). Форма
    без кавычек работает и в PowerShell, и в cmd, поэтому путь с пробелами
    сначала сокращается до 8.3, и только если это невозможно — строится
    пусковой `.cmd` в каталоге без пробелов.

Автор: Михаил Дейнекин
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

BRIDGE_PACKAGE = "@deepseek-ai/dsh-hooks-claude-code"
PROTOCOL_PACKAGE = "@deepseek-ai/dsh-hook-protocol"
ENTRY_ID = "hooks-shared"
DEFAULT_PROFILE = "desktop"
#: Сколько последних стенограмм просматривать в поисках `hook/invoked`.
SESSIONS_TO_SCAN = 40


# ---------------------------------------------------------------------------
# Установка HARNESS
# ---------------------------------------------------------------------------


def _candidates() -> list[Path]:
    out: list[Path] = []
    for env in ("LOCALAPPDATA", "ProgramFiles", "ProgramW6432"):
        base = os.environ.get(env)
        if base:
            out.append(Path(base) / "Programs" / "DeepSeek Harness")
            out.append(Path(base) / "DeepSeek Harness")
    home = Path(os.path.expanduser("~"))
    out.append(home / "AppData" / "Local" / "Programs" / "DeepSeek Harness")
    return out


def install_root() -> Path | None:
    """Каталог установленного HARNESS (там, где лежат `resources` и `version`)."""
    for path in _candidates():
        if (path / "resources" / "app.asar").is_file():
            return path
    return None


def app_version(root: Path | None = None) -> str:
    """Версия приложения из файла `version` (например, 44.7.0)."""
    root = root or install_root()
    if not root:
        return ""
    try:
        return (root / "version").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


_RUNTIME_CACHE: dict[str, str] = {}
_ASAR_CACHE: dict[str, dict[str, Any]] = {}


def _asar_root(root: Path) -> dict[str, Any]:
    """Заголовок `app.asar` один раз на процесс: разбор 4,7 МБ JSON дорог."""
    key = str(root)
    if key in _ASAR_CACHE:
        return _ASAR_CACHE[key]
    asar = root / "resources" / "app.asar"
    try:
        with asar.open("rb") as fh:
            head = fh.read(16)
            pickled = struct.unpack("<I", head[4:8])[0]
            fh.seek(16)
            node, _end = json.JSONDecoder().raw_decode(
                fh.read(pickled - 4).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        node = {}
    node["__pickled"] = pickled if node else 0
    _ASAR_CACHE[key] = node
    return node


def _asar_package_json(root: Path, package: str) -> dict[str, Any] | None:
    """`package.json` пакета из `app.asar` (или None, если пакета в сборке нет)."""
    asar = root / "resources" / "app.asar"
    if not asar.is_file():
        return None
    try:
        header = _asar_root(root)
        pickled = int(header.get("__pickled") or 0)
        node: Any = header
        for part in ("dsh", "node_modules", *package.split("/"), "package.json"):
            node = (node.get("files") or {}).get(part) or {}
        if not pickled or not node or "offset" not in node:
            return None
        with asar.open("rb") as fh:
            fh.seek(8 + pickled + int(node["offset"]))
            blob = fh.read(int(node["size"]))
        return json.loads(blob.decode("utf-8"))
    except Exception:  # noqa: BLE001 — разбор архива не должен ронять установку
        return None


def runtime_version(root: Path | None = None) -> str:
    """Версия пакетов рантайма (`@deepseek-ai/dsh`) — то, с чем сверяется мост."""
    root = root or install_root()
    if not root:
        return ""
    key = str(root)
    if key in _RUNTIME_CACHE:
        return _RUNTIME_CACHE[key]
    version = ""
    data = _asar_package_json(root, "@deepseek-ai/dsh")
    if data:
        version = str(data.get("version") or "")
    _RUNTIME_CACHE[key] = version
    return version


def bundled(root: Path | None, package: str) -> bool:
    """Есть ли пакет в сборке приложения (а не только в реестре npm)."""
    root = root or install_root()
    if not root:
        return False
    return _asar_package_json(root, package) is not None


def dsh_cli(root: Path | None = None) -> Path | None:
    """Путь к CLI `dsh` этой установки (`dsh plugin add` идёт через него)."""
    root = root or install_root()
    if not root:
        return None
    cli = root / "resources" / "runtime" / "cli" / "bin" / "dsh.cmd"
    return cli if cli.is_file() else None


def dsh_home() -> Path:
    """Каталог состояния клиента: `$DSH_HOME` либо `~/.dsh`."""
    return Path(os.environ.get("DSH_HOME") or (Path(os.path.expanduser("~")) / ".dsh"))


def profile_dir(profile: str = DEFAULT_PROFILE, home: Path | None = None) -> Path:
    return (home or dsh_home()) / "profiles" / profile


# ---------------------------------------------------------------------------
# Состояние профиля
# ---------------------------------------------------------------------------


def _profile_manifest(path: Path) -> dict[str, Any]:
    try:
        return json.loads((path / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def profile_store_dir(path: Path) -> str:
    """Хранилище pnpm, из которого собран `node_modules` профиля.

    Зачем. `node_modules` профиля бывает собран ДРУГОЙ версией pnpm (записано
    в `.modules.yaml` как `packageManager` и `storeDir`). Связанная версия pnpm
    из сборки HARNESS имеет свой путь хранилища и отказывается работать:
    «pnpm now wants to use the store at … (this error may happen if the
    node_modules was installed with a different major version of pnpm)» —
    именно так упала первая установка моста 02.11.2026 на живом профиле.
    Поэтому хранилище берётся из профиля и передаётся pnpm явно.
    """
    modules = path / "node_modules" / ".modules.yaml"
    if not modules.is_file():
        return ""
    try:
        text = modules.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    found = re.search(r'"?storeDir"?\s*:\s*"((?:[^"\\]|\\.)+)"', text)
    if not found:
        return ""
    raw = found.group(1)
    try:  # в .modules.yaml путь записан как JSON-строка: снимаем экранирование
        raw = str(json.loads(f'"{raw}"'))
    except ValueError:
        pass
    # Замерено на живом профиле 02.11.2026: там записано `C:\\\\Users\\\\...` —
    # после снятия экранирования остаётся несколько подряд разделителей. Win32
    # их сворачивает, но путь в отчёте должен быть читаемым.
    return re.sub(r"\\{2,}", r"\\", raw)


def plugin_installed(path: Path, package: str) -> str:
    """Версия пакета, установленного в профиль (пустая строка — не установлен)."""
    manifest = path / "node_modules" / Path(*package.split("/")) / "package.json"
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version") or "")
    except (OSError, ValueError):
        return ""


def hook_entry(patch: Path) -> dict[str, Any]:
    """Запись моста хуков в `cordis.patch.yml` профиля: есть ли и куда смотрит."""
    result: dict[str, Any] = {"present": False, "config_path": "", "config_exists": False,
                              "name": ""}
    if not patch.is_file():
        return result
    text = patch.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^\s*-\s*id:\s*" + re.escape(ENTRY_ID) + r"\s*$", text, re.M)
    if not match:
        return result
    result["present"] = True
    block = text[match.end():]
    stop = re.search(r"^\s*-\s*(?:id:|insert:)", block, re.M)
    if stop:
        block = block[:stop.start()]
    name = re.search(r"^\s*name:\s*['\"]?([^'\"\n]+)", block, re.M)
    config = re.search(r"^\s*configPath:\s*['\"]?([^'\"\n]+)", block, re.M)
    result["name"] = (name.group(1).strip() if name else "")
    if config:
        result["config_path"] = config.group(1).strip()
        result["config_exists"] = Path(result["config_path"]).is_file()
    return result


def hook_events(since_sessions: int = SESSIONS_TO_SCAN,
                home: Path | None = None) -> dict[str, Any]:
    """Последние события `hook/*` в стенограммах сеансов.

    Стенограммы лежат как `session.v4.jsonl.zstd`. Считаются вызовы (invoked) и
    собираются коды возврата (result): по коду видно, ДОШЁЛ ли хук до выполнения.
    Отсутствие модуля zstd (Python 3.13) — честный ответ «не проверено», а не
    «хуков не было».
    """
    home = home or dsh_home()
    sessions = home / "sessions"
    result: dict[str, Any] = {"checked": 0, "invoked": 0, "last_at": "", "skipped": "",
                              "exit_codes": {}, "points": []}
    if not sessions.is_dir():
        result["skipped"] = "каталог сеансов не найден"
        return result
    try:
        from compression import zstd  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 — PEP 784 есть только в Python 3.14+
        result["skipped"] = "нет compression.zstd: нужен Python 3.14"
        return result
    files = sorted(sessions.rglob("session.v4.jsonl.zstd"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:since_sessions]
    last = 0
    codes: dict[str, int] = {}
    points: list[str] = []
    newest_codes: dict[str, int] = {}
    for path in files:
        result["checked"] += 1
        try:
            raw = zstd.decompress(path.read_bytes()).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        session_codes: dict[str, int] = {}
        for line in raw.splitlines():
            if '"hook/invoked"' not in line and '"hook/result"' not in line:
                continue
            found_time = re.search(r'"time":\s*(\d{10,})', line)
            if found_time:
                last = max(last, int(found_time.group(1)))
            if '"hook/invoked"' in line:
                result["invoked"] += 1
                point = re.search(r'"point":\s*"([^"]+)"', line)
                if point and point.group(1) not in points:
                    points.append(point.group(1))
            else:
                code = re.search(r'"exitCode":\s*(-?\d+)', line)
                duration = re.search(r'"durationMs":\s*([\d.]+)', line)
                if duration and float(duration.group(1)) >= 59000:
                    result["timeouts"] = int(result.get("timeouts") or 0) + 1
                if code:
                    codes[code.group(1)] = codes.get(code.group(1), 0) + 1
                    session_codes[code.group(1)] = session_codes.get(code.group(1), 0) + 1
        # Коды САМОГО СВЕЖЕГО сеанса, где хуки были: по ним и судится текущее
        # состояние — история от 23.10 (код 1 от прежней формы команды) иначе
        # навсегда перекрывала бы успех после починки.
        if session_codes and not newest_codes:
            newest_codes = session_codes
    if last:
        result["last_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last / 1000))
    result["exit_codes"] = codes
    result["newest_codes"] = newest_codes
    result["points"] = points
    return result


#: Код «процесс не запустился»: STATUS_DLL_INIT_FAILED. Замерено 02.11.2026:
#: в песочной оболочке клиента его получает ЛЮБАЯ команда хука, даже `cmd.exe /c echo`.
SPAWN_FAILED_CODE = 3221225794


def hook_verdict(events: dict[str, Any]) -> dict[str, str]:
    """Объяснить состояние хуков по измеренным кодам возврата."""
    if events.get("skipped"):
        return {"state": "unknown", "reason": str(events["skipped"])}
    codes = {str(k): v for k, v in (events.get("newest_codes") or {}).items()}
    if str(SPAWN_FAILED_CODE) in codes:
        return {"state": "spawn_failed",
                "reason": (f"процесс хука не запускается: код {SPAWN_FAILED_CODE} "
                           "(0xC0000142) у каждой команды — признак песочной оболочки клиента; "
                           "лечится переключателем --plain-shell")}
    if not events.get("invoked"):
        return {"state": "not_invoked", "reason": "событий hook/invoked нет: мост не смонтирован"}
    if codes and all(code == "0" for code in codes):
        return {"state": "ok", "reason": "хуки вызываются и завершаются кодом 0"}
    if "0" in codes and set(codes) - {"0"} <= {"1"} and events.get("timeouts"):
        return {"state": "ok",
                "reason": (f"хуки работают ({codes['0']} с кодом 0), но {events['timeouts']} раз "
                           "упёрлись в timeout — это загрузка машины, а не ошибка команды")}
    if events.get("timeouts") and set(codes) <= {"1"}:
        return {"state": "timeout",
                "reason": (f"хук не уложился в отведённое время ({events['timeouts']} раз): "
                           "команда дольше timeout в конфиге; сам гейт отвечает за 0,13 с — "
                           "проверять загрузку машины")}
    return {"state": "command_error",
            "reason": "хук вызывается, но команда завершается ошибкой: " + json.dumps(codes)}


def state(profile: str = DEFAULT_PROFILE, home: Path | None = None) -> dict[str, Any]:
    """Состояние поддержки моста хуков: что есть, чего нет, что делать.

    Ответ строится по фактам: версия сборки, наличие пакетов в сборке и в
    профиле, запись в патче, файл хуков, события в стенограммах. Ни одно поле
    не выводится «по памяти»: где проверить нельзя — там `skipped` с причиной.
    """
    root = install_root()
    version = runtime_version(root)
    path = profile_dir(profile, home)
    patch = path / "cordis.patch.yml"
    entry = hook_entry(patch)
    bridge = plugin_installed(path, BRIDGE_PACKAGE)
    protocol = plugin_installed(path, PROTOCOL_PACKAGE)
    events = hook_events(home=home)

    actions: list[str] = []
    verdict = hook_verdict(events)
    if verdict["state"] == "spawn_failed":
        actions.append(verdict["reason"])
    if not root:
        actions.append("HARNESS не найден: укажите каталог установки явно")
    elif not bridge:
        actions.append(f"поставить мост: dsh plugin --profile {profile} add "
                       f"{BRIDGE_PACKAGE}@{version or '<версия рантайма>'}")
    if root and not protocol and bundled(root, PROTOCOL_PACKAGE) is False:
        actions.append(f"поставить протокол хуков: {PROTOCOL_PACKAGE}@{version} "
                       f"(в сборке его нет, а мост его импортирует)")
    if bridge and version and bridge != version:
        actions.append(f"версия моста {bridge} не совпадает с рантаймом {version}: "
                       f"клиент может отказать по совместимости")
    if not entry["present"]:
        actions.append("добавить запись hooks-shared в cordis.patch.yml")
    elif not entry["config_exists"]:
        actions.append(f"файл хуков не найден: {entry['config_path'] or 'не указан'}")
    if events["invoked"] == 0 and events["checked"]:
        actions.append("хуки не вызываются: перезапустить HARNESS и проверить снова")

    return {
        "app_version": app_version(root),
        "app_root": str(root) if root else "",
        "runtime_version": version,
        "dsh_cli": str(dsh_cli(root) or ""),
        "profile": str(path),
        "bridge": {"package": BRIDGE_PACKAGE, "installed": bridge,
                   "in_build": bundled(root, BRIDGE_PACKAGE)},
        "protocol": {"package": PROTOCOL_PACKAGE, "installed": protocol,
                     "in_build": bundled(root, PROTOCOL_PACKAGE)},
        "entry": entry,
        "hook_events": events,
        "hook_verdict": verdict,
        "actions": actions,
    }


# ---------------------------------------------------------------------------
# Оболочка хуков: песочная не может запустить процесс
# ---------------------------------------------------------------------------

#: Патч профиля, заменяющий песочную оболочку на обычную.
PLAIN_SHELL_MARK = "# RAGRAF: обычная оболочка вместо песочной"
PLAIN_SHELL_BLOCK = f"""
{PLAIN_SHELL_MARK}
# Зачем: в песочной оболочке клиента (dsh-pwsh-sandbox / dsh-bash-sandbox)
# процесс хука НЕ запускается вовсе: любая команда (включая `cmd.exe /c echo`)
# возвращает 3221225794 (0xC0000142). Замерено 02.11.2026: с обычной оболочкой
# та же команда даёт код 0, и гейт работает.
# Откат: убрать этот блок или выполнить install_harness_gate.py --sandbox-shell.
- id: pwsh-sandbox
  disabled: true
- id: bash-sandbox
  disabled: true
- insert:
    - id: pwsh-plain
      name: '@deepseek-ai/dsh-pwsh-local'
    - id: bash-plain
      name: '@deepseek-ai/dsh-bash-local'
"""


def plain_shell_enabled(patch: Path) -> bool:
    """Включён ли в профиле обход песочной оболочки."""
    if not patch.is_file():
        return False
    return PLAIN_SHELL_MARK in patch.read_text(encoding="utf-8", errors="replace")


def apply_plain_shell(profile: str = DEFAULT_PROFILE, home: Path | None = None,
                      enable: bool = True, dry_run: bool = False) -> dict[str, Any]:
    """Включить или выключить обычную оболочку вместо песочной (с копией).

    Это изменение безопасности клиента, а не косметика: песочная оболочка
    ограничивает то, что может запускать агент. Поэтому действие отдельное,
    только по явному флагу, и всегда с резервной копией.
    """
    path = profile_dir(profile, home) / "cordis.patch.yml"
    result: dict[str, Any] = {"ok": False, "patch": str(path), "enabled": False}
    if not path.is_file():
        result["error"] = "патч профиля не найден"
        return result
    text = path.read_text(encoding="utf-8")
    has = PLAIN_SHELL_MARK in text
    if has == enable:
        result.update(ok=True, enabled=has, note="уже в нужном состоянии")
        return result
    if dry_run:
        result.update(ok=True, dry_run=True, enabled=enable)
        return result

    backup = path.with_name(path.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}-shell")
    shutil.copy2(path, backup)
    result["backup"] = str(backup)

    if enable:
        new_text = text.rstrip("\n") + "\n" + PLAIN_SHELL_BLOCK
    else:
        index = text.index(PLAIN_SHELL_MARK)
        tail = text[index:]
        # Блок снимается только если он не изменён: иначе есть риск унести чужие правки.
        if tail.strip() != PLAIN_SHELL_BLOCK.strip():
            result["error"] = ("блок оболочки изменён вручную — снимите вручную, "
                              f"копия рядом: {backup.name}")
            return result
        new_text = text[:index].rstrip("\n") + "\n"

    path.write_text(new_text, encoding="utf-8", newline="\n")
    after = path.read_text(encoding="utf-8")
    ok = (PLAIN_SHELL_MARK in after) == enable
    if not ok:
        shutil.copy2(backup, path)
        result["error"] = "после правки состояние не изменилось — откат"
        return result
    result.update(ok=True, enabled=enable)
    return result


# ---------------------------------------------------------------------------
# Установка моста
# ---------------------------------------------------------------------------


def _run(cmd: list[str], timeout: int = 900,
         env: dict[str, str] | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env)
        return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()
    except FileNotFoundError:
        return 127, "команда не найдена"
    except subprocess.TimeoutExpired:
        return 124, f"таймаут {timeout} с"


def install_bridge(profile: str = DEFAULT_PROFILE, home: Path | None = None,
                   dry_run: bool = False) -> dict[str, Any]:
    """Поставить мост хуков и протокол в профиль родной командой клиента.

    Почему `dsh plugin`, а не `pnpm` напрямую: клиент сам выбирает pnpm своей
    сборки, ведёт `pnpm-workspace.yaml` профиля (`nodeLinker: hoisted`,
    `autoInstallPeers: false`), проверяет совместимость версии плагина с
    рантаймом и пишет журнал операции в `.plugin-manager/logs`. Ручной pnpm
    проходит мимо проверки совместимости — а именно она отсекает мост чужой
    версии.

    Перед запуском снимаются копии `package.json`, `pnpm-lock.yaml` и патча;
    при отказе клиента профиль возвращается к прежнему виду.
    """
    root = install_root()
    cli = dsh_cli(root)
    version = runtime_version(root)
    path = profile_dir(profile, home)
    result: dict[str, Any] = {"ok": False, "profile": str(path), "steps": []}
    if not cli:
        result["error"] = "CLI dsh не найден: установка невозможна"
        return result
    if not path.is_dir():
        result["error"] = f"каталог профиля не найден: {path}"
        return result
    if not version:
        result["error"] = "версия рантайма не прочитана из app.asar"
        return result

    want = [f"{BRIDGE_PACKAGE}@{version}"]
    if not bundled(root, PROTOCOL_PACKAGE):
        want.append(f"{PROTOCOL_PACKAGE}@{version}")
    result["wanted"] = want
    store = profile_store_dir(path)
    extra: list[str] = []
    if store:
        extra.append(f"--config.store-dir={store}")
        result["store_dir"] = store
    if dry_run:
        result["dry_run"] = True
        result["command"] = [str(cli), "plugin", "--profile", profile, "add", *want, *extra]
        return result

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backups: list[tuple[Path, Path]] = []
    for name in ("package.json", "pnpm-lock.yaml", "cordis.patch.yml"):
        src = path / name
        if src.is_file():
            dst = src.with_name(f"{src.name}.bak-{stamp}-ragraf")
            shutil.copy2(src, dst)
            backups.append((src, dst))
    result["backups"] = [str(dst) for _src, dst in backups]

    env = dict(os.environ)
    env.setdefault("DSH_HOME", str(home or dsh_home()))
    code, output = _run([str(cli), "plugin", "--profile", profile, "add", *want, *extra],
                        env=env)
    result["returncode"] = code
    result["output_tail"] = "\n".join(output.splitlines()[-8:])

    after_bridge = plugin_installed(path, BRIDGE_PACKAGE)
    after_protocol = plugin_installed(path, PROTOCOL_PACKAGE)
    result["installed"] = {"bridge": after_bridge, "protocol": after_protocol}
    ok = code == 0 and after_bridge != ""
    if not ok:
        restored = []
        for src, dst in backups:
            try:
                shutil.copy2(dst, src)
                restored.append(src.name)
            except OSError:
                pass
        result["reverted"] = restored
        result["error"] = "клиент отказал в установке: профиль возвращён к прежнему виду"
        return result
    result["ok"] = True
    result["note"] = ("мост установлен; запись hooks-shared в профиле остаётся "
                      "прежней — её проверяет install_harness_gate.py")
    return result


# ---------------------------------------------------------------------------
# Команда хука, которая не ломается в PowerShell
# ---------------------------------------------------------------------------


def _has_space(part: str) -> bool:
    return any(ch.isspace() or ch in "\"'" for ch in part)


def split_command(text: str) -> list[str]:
    """Разобрать строку команды на части, сняв кавычки.

    Нужно, чтобы переписать чужую команду (xerj, bsg) в безопасную форму: она
    записана по правилам cmd, с кавычками вокруг путей с пробелами, и таких
    путей после сборки свода быть не должно.
    """
    parts: list[str] = []
    buf: list[str] = []
    quote = ""
    for ch in text.strip():
        if quote:
            if ch == quote:
                quote = ""
            else:
                buf.append(ch)
        elif ch in "\"'":
            quote = ch
        elif ch.isspace():
            if buf:
                parts.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _short_path(part: str) -> str:
    """8.3-путь Windows (пустая строка, если сократить нельзя)."""
    if not sys.platform.startswith("win"):
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        get_short = ctypes.windll.kernel32.GetShortPathNameW
        get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short.restype = wintypes.DWORD
        size = get_short(part, None, 0)
        if not size:
            return ""
        buf = ctypes.create_unicode_buffer(size)
        if not get_short(part, buf, size):
            return ""
        return buf.value
    except Exception:  # noqa: BLE001 — сокращение пути не обязательно
        return ""


def safe_command(parts: list[str], launcher_dir: Path | None = None) -> dict[str, Any]:
    """Собрать команду хука так, чтобы её поняли и PowerShell, и cmd.

    Правило простое: **никаких кавычек и вообще ничего, что оболочки трактуют
    по-своему**. Сначала пробуем как есть; если в пути есть пробел — сокращаем
    до 8.3; если и это не вышло — пишем пусковой `.cmd` в каталог без пробелов
    (`launcher_dir`) и ссылаемся на него.

    Возвращает `command`, `form` (`plain` | `short` | `launcher` | `failed`) и
    `reason`, если форму построить не удалось.
    """
    cleaned = [str(p) for p in parts if str(p)]
    if not any(_has_space(p) for p in cleaned):
        return {"command": " ".join(cleaned), "form": "plain", "reason": ""}
    short = [_short_path(p) if _has_space(p) else p for p in cleaned]
    if all(s and not _has_space(s) for s in short):
        return {"command": " ".join(short), "form": "short", "reason": ""}
    if launcher_dir:
        try:
            launcher_dir.mkdir(parents=True, exist_ok=True)
            stamp = abs(hash(tuple(cleaned))) % 100000
            path = launcher_dir / f"hook-{stamp}.cmd"
            body = "@echo off\r\n" + " ".join(f'"{p}"' for p in cleaned) + " %*\r\n"
            path.write_bytes(body.encode("ascii", "replace"))
            if not _has_space(str(path)):
                return {"command": str(path), "form": "launcher", "reason": ""}
        except OSError as exc:
            return {"command": "", "form": "failed", "reason": f"{type(exc).__name__}: {exc}"}
    return {"command": "", "form": "failed",
            "reason": "путь с пробелом нельзя ни сократить, ни обойти пусковым файлом"}


def verify_command(command: str, payload: str = '{"session_id":"","cwd":""}') -> dict[str, Any]:
    """Прогнать команду хука так, как её запускает оболочка клиента.

    Проверяются обе оболочки: PowerShell (`-Command`, именно её ParserError
    ломал прежний хук) и `cmd /c`. Команда должна завершиться нулём в обеих.
    Вызывать для команд с побочными эффектами не нужно — это выполняет код;
    для проверки ФОРМЫ команды есть `parse_only`.
    """
    checks: dict[str, Any] = {}
    targets = []
    pwsh = shutil.which("pwsh") or shutil.which("pwsh.exe")
    if pwsh:
        targets.append(("pwsh", [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive",
                                 "-Command", command]))
    comspec = os.environ.get("ComSpec") or "cmd.exe"
    targets.append(("cmd", [comspec, "/c", command]))
    for label, argv in targets:
        try:
            proc = subprocess.run(argv, input=payload, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=180)
            checks[label] = {"code": proc.returncode,
                             "out": (proc.stdout or "").strip()[:300],
                             "err": (proc.stderr or "").strip()[:300]}
        except Exception as exc:  # noqa: BLE001
            checks[label] = {"code": -1, "err": f"{type(exc).__name__}: {exc}"}
    ok = all(item["code"] == 0 for item in checks.values())
    return {"ok": ok, "checks": checks}


def parse_only(command: str) -> dict[str, Any]:
    """Разобрать команду парсером PowerShell, НЕ выполняя её.

    Ровно этот отказ ломал гейт: хук падал `ParserError` ДО запуска, поэтому
    проверять надо именно разбор — и это можно делать без побочных эффектов
    (выполнение `session-start` пишет сеанс в состояние гейта: замерено — шесть
    синтетических записей `anon-*` появились именно от такой проверки).
    """
    pwsh = shutil.which("pwsh") or shutil.which("pwsh.exe")
    if not pwsh:
        return {"ok": True, "skipped": "PowerShell не найден"}
    script = (
        "$errs=$null; $null=[System.Management.Automation.Language.Parser]::ParseInput("
        "$args[0],[ref]$null,[ref]$errs); "
        "if ($errs.Count) { $errs | ForEach-Object { Write-Output $_.Message }; exit 1 } "
        "else { exit 0 }"
    )
    try:
        proc = subprocess.run([pwsh, "-NoLogo", "-NoProfile", "-NonInteractive",
                               "-Command", script, command],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
        return {"ok": proc.returncode == 0, "code": proc.returncode,
                "err": (proc.stdout or proc.stderr or "").strip()[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": -1, "err": f"{type(exc).__name__}: {exc}"}
