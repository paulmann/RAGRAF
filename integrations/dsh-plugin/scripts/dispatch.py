"""Единая точка входа RAGRAF для DeepSeek Harness: сервер, хуки, проверка.

Зачем отдельный файл, если у RAGRAF уже есть свой MCP-сервер. Клиент запускает
процесс по одному пути, а путь установки заранее неизвестен: RAGRAF может быть
рабочей копией (`C:\\AutoClaw\\RAGRAF`), другой рабочей копией на другой машине
или установленным пакетом `ragraf`. Жёсткий путь в манифесте плагина означал бы
«плагин работает только у автора», переменная окружения — «пользователь обязан
прочитать README до установки». Поэтому пакет ставит этот файл в
`$DSH_HOME/ragraf/`, а он находит установку сам и называет причину отказа.

Разделы, которые вызывает клиент (их пишет установщик в hooks.json и в запись
MCP-сервера):

    dispatch.py mcp  [--agent X --agent-kind Y]  # MCP-сервер, stdio
    dispatch.py hook <стадия> [--mode=…]         # стадия гейта, JSON на stdin
    dispatch.py where                            # что найдено (JSON)
    dispatch.py doctor                           # проверка связки (JSON)

Границы, названные прямо:

  * **Хук живёт в рабочей копии.** Стадии гейта реализованы в
    `tools/harness_gate.py`, в установленный пакет этот файл не входит. Если
    найдена только установка пакетом, хук НЕ подменяется суррогатом: он
    завершается кодом 0 (сеанс не ломается) и пишет в stderr, чего не хватает.
    Молча «разрешить» было бы хуже: гейт выглядел бы работающим.
  * **Хук всегда fail-open.** Любая ошибка внутри — код 0: сломанный гейт не
    имеет права блокировать работу. Отказ выдаёт только сам гейт, своим JSON.
  * **Пересборка индекса не запускается.** Полный `ensure` корпуса занимает
    минуты, а SessionStart получает 30 с. Актуализацию делает агент первым
    действием, и требует её гейт, а не этот файл.
  * **Вывод пишется байтами UTF-8.** Замер 02.11.2026: при кодировке канала
    cp1252 печать кириллицы падала `UnicodeEncodeError`, обработчик по правилу
    fail-open писал «разрешено» — то есть гейт молча выключался.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

#: Известные места рабочей копии: сначала машинный каталог проекта, затем
#: домашние варианты, затем системные — от самого вероятного к редкому.
KNOWN_HOMES = (
    Path("C:/AutoClaw/RAGRAF"),
    Path.home() / "RAGRAF",
    Path.home() / "src" / "RAGRAF",
    Path("/opt/ragraf"),
    Path("/usr/local/share/ragraf"),
)

#: Имя файла, которым установщик записывает, что именно он настроил.
RECORD = "install.json"


def _write(text: str) -> None:
    """Печать в UTF-8 независимо от кодировки канала (см. docstring)."""
    try:
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:  # noqa: BLE001 — печать не имеет права ронять гейт
        pass


def _warn(text: str) -> None:
    try:
        sys.stderr.buffer.write(("[RAGRAF] " + text + "\n").encode("utf-8"))
        sys.stderr.buffer.flush()
    except Exception:  # noqa: BLE001
        pass


def dsh_home() -> Path:
    """$DSH_HOME, иначе ~/.dsh — то же правило, что у dsh-home-paths."""
    value = os.environ.get("DSH_HOME")
    if value:
        return Path(value)
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".") / ".dsh"


def _is_checkout(path: Path | None) -> bool:
    """Похож ли каталог на рабочую копию: есть сервер и пакет исходников."""
    if not path:
        return False
    return ((path / "tools" / "ragraf-mcp.py").is_file()
            and (path / "src" / "ragraf" / "__init__.py").is_file())


def _recorded_home() -> Path | None:
    """Каталог, записанный установщиком: он сильнее догадок о местах."""
    try:
        data = json.loads((dsh_home() / "ragraf" / RECORD).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — записи может не быть, это не ошибка
        return None
    root = data.get("ragraf_home") or ""
    return Path(root) if root and _is_checkout(Path(root)) else None


def _package_root() -> dict | None:
    """Установленный пакет `ragraf`: путь и версия (для отчёта)."""
    try:
        import ragraf  # noqa: PLC0415 — импорт нужен только здесь
    except Exception:  # noqa: BLE001 — отсутствие пакета не ошибка, а факт
        return None
    return {"root": str(Path(ragraf.__file__).resolve().parent),
            "version": str(getattr(ragraf, "__version__", ""))}


def locate() -> dict:
    """Что найдено: рабочая копия, пакет либо ничего — с основанием выбора."""
    env_home = os.environ.get("RAGRAF_HOME")
    if env_home:
        candidate = Path(env_home).expanduser()
        if _is_checkout(candidate):
            return {"mode": "checkout", "root": str(candidate), "why": "RAGRAF_HOME"}
        # Явное указание сильнее догадок: неверное лучше назвать, чем обойти.
        return {"mode": "none", "root": "",
                "why": "RAGRAF_HOME=%s не похож на RAGRAF" % env_home}
    recorded = _recorded_home()
    if recorded:
        return {"mode": "checkout", "root": str(recorded), "why": "запись установщика"}
    for candidate in KNOWN_HOMES:
        if _is_checkout(candidate):
            return {"mode": "checkout", "root": str(candidate),
                    "why": "известное место %s" % candidate}
    package = _package_root()
    if package:
        return {"mode": "package", "root": package["root"], "why": "пакет ragraf",
                "version": package["version"]}
    return {"mode": "none", "root": "",
            "why": "ни RAGRAF_HOME, ни записи установщика, ни известных мест, ни пакета ragraf"}


def pick_python(root: str) -> str:
    """Интерпретатор: явный выбор, .venv рабочей копии, затем текущий.

    Клиент запускает dispatch тем `python`, который нашёлся в PATH, а это может
    быть чужой интерпретатор без зависимостей RAGRAF (fastembed, tree_sitter,
    sqlite_vec). Замер 03.11.2026: плагин в песочнице падал с CONNECTION_CLOSED
    именно по этой причине — `python` в PATH был вендоренным, без пакетов.
    """
    forced = os.environ.get("RAGRAF_PYTHON")
    if forced and Path(forced).exists():
        return forced
    if root:
        base = Path(root) / ".venv"
        for candidate in (base / "Scripts" / "python.exe", base / "bin" / "python"):
            if candidate.exists():
                return str(candidate)
    return sys.executable


def _run(argv: list[str], cwd: str | None = None) -> int:
    """Запустить дочерний процесс, наследуя stdio и добавляя UTF-8 в канал.

    Потоки наследуются: MCP-обмен идёт напрямую, без буферизации родителем.
    """
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    try:
        return subprocess.call(argv, env=env, cwd=cwd)
    except Exception as exc:  # noqa: BLE001 — отказ должен быть назван
        _warn("запуск не удался: %s: %s" % (type(exc).__name__, exc))
        return 3


def run_mcp(found: dict, rest: list[str]) -> int:
    python = pick_python(found.get("root") or "")
    if found["mode"] == "checkout":
        server = str(Path(found["root"]) / "tools" / "ragraf-mcp.py")
        # Рабочий каталог сервера — каталог RAGRAF. Не педантизм: корпус при
        # старте определяется по рабочему каталогу процесса, а без этого он
        # оказался бы каталогом приложения. Чужой проект — это `use_project`,
        # а не угадывание по каталогу клиента (MCP-клиент HARNESS объявляет
        # capabilities: {}, корней сервер не получает).
        return _run([python, server, *rest], cwd=found["root"])
    if found["mode"] == "package":
        return _run([python, "-m", "ragraf.mcp_server", *rest])
    _warn("сервер не запущен: " + found["why"]
          + ". Установите RAGRAF (`pip install ragraf`) или укажите RAGRAF_HOME "
            "на рабочую копию.")
    return 3


def run_hook(found: dict, stage: str, rest: list[str]) -> int:
    """Стадия гейта. Код 0 при любой невозможности: сеанс не ломаем."""
    if found["mode"] != "checkout":
        _warn("хук пропущен: " + found["why"]
              + ". Стадии гейта есть только в рабочей копии RAGRAF; сервер и "
                "навык при этом работают.")
        return 0
    gate = Path(found["root"]) / "tools" / "harness_gate.py"
    if not gate.is_file():
        _warn("хук пропущен: нет файла %s" % gate)
        return 0
    # stdin клиент подаёт сам: он приходит в этот процесс и уходит в гейт как есть.
    return _run([pick_python(found["root"]), str(gate), stage, *rest])


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _hook_modes(config: dict) -> set:
    """Режимы, записанные в командах хуков (`--mode=…`)."""
    modes = set()
    hooks = config.get("hooks") or {}
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks") or []:
                if not isinstance(hook, dict):
                    continue
                for part in str(hook.get("command", "")).split():
                    if part.startswith("--mode="):
                        modes.add(part.split("=", 1)[1])
    return modes


def _budget(profile: str) -> dict:
    """Что объявляет слой профиля и влезает ли файл в базовый бюджет.

    Здесь НАМЕРЕННО нет вердикта «бюджет поднят». Замер 03.11.2026 на клиенте
    44.7.0: в развёрнутом дереве несколько записей `agent-instructions`, верхняя
    отключена базовым слоем, а действует бюджет пресета агента (65 536); сеанс
    шёл с `"agentPreset":"standard"`, и в его стенограмме осталось
    `Workspace instruction budget 65536 bytes: omitted ~/.dsh/AGENTS.md` — то
    есть объявленные в профиле 131 072 не применялись вовсе.
    Поэтому doctor показывает и объявление, и базовый порог, и отправляет к
    проверке, которая читает дерево целиком (`tools/install_dsh_plugin.py check`).
    """
    base = dsh_home()
    declared = None
    path = base / "profiles" / profile / "cordis.patch.yml"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("maxBytes:"):
                value = stripped.split(":", 1)[1].strip()
                if value.isdigit():
                    declared = int(value)
    except Exception:  # noqa: BLE001
        pass
    agents = base / "AGENTS.md"
    size = agents.stat().st_size if agents.is_file() else 0
    return {"declared_in_profile": declared, "agents_md_bytes": size,
            "base_budget": 65536, "fits_base": size == 0 or size <= 65536}


def doctor(profile: str) -> dict:
    """Проверка связки: что настроено, что сломается и как это починить.

    Проверяются ровно те причины, которые были измерены как «гейт молчит, а
    ошибки нет»: бюджет инструкций, режим гейта, наличие моста хуков, наличие
    файлов, на которые смотрят записи профиля, и найденная установка RAGRAF.
    """
    base = dsh_home()
    home = base / "ragraf"
    profile_dir = base / "profiles" / profile
    found = locate()
    record = _read_json(home / RECORD)
    hooks = _read_json(home / "hooks.json")
    checks: list[dict] = []

    def add(name: str, status: str, detail: str, fix: str = "") -> None:
        checks.append({"check": name, "status": status, "detail": detail, "fix": fix})

    if found["mode"] == "none":
        add("ragraf", "fail", found["why"],
            "задать RAGRAF_HOME или установить пакет: pip install ragraf")
    else:
        add("ragraf", "ok", "%s (%s)" % (found["root"], found["why"]))

    for name, path in (("dispatch", home / "dispatch.py"), ("hooks", home / "hooks.json")):
        if path.is_file():
            add(name, "ok", "%s (%d байт)" % (path, path.stat().st_size))
        else:
            add(name, "fail", "нет файла %s" % path,
                "python tools/install_dsh_plugin.py install --profile %s" % profile)

    modes = _hook_modes(hooks)
    if not modes:
        add("gate-mode", "fail", "в hooks.json нет ни одной стадии гейта",
            "переустановить: python tools/install_dsh_plugin.py install")
    elif modes == {"deny"}:
        add("gate-mode", "ok", "deny во всех стадиях")
    else:
        add("gate-mode", "warn", "режимы: " + ", ".join(sorted(modes)),
            "режим warn только подсказывает, и подсказку игнорируют; нужен deny")

    bridge = profile_dir / "node_modules" / "@deepseek-ai" / "dsh-hooks-claude-code"
    protocol = profile_dir / "node_modules" / "@deepseek-ai" / "dsh-hook-protocol"
    if bridge.is_dir() and protocol.is_dir():
        add("hook-bridge", "ok", "мост и протокол стоят в профиле %s" % profile)
    elif bridge.is_dir() or protocol.is_dir():
        add("hook-bridge", "fail", "стоит только часть моста (нужны оба пакета)",
            "dsh plugin --profile %s add @deepseek-ai/dsh-hook-protocol" % profile)
    else:
        add("hook-bridge", "fail", "моста хуков нет в профиле %s" % profile,
            "dsh plugin --profile %s add ragraf-dsh" % profile)

    plugin = profile_dir / "node_modules" / "ragraf-dsh"
    add("bundle", "ok" if plugin.is_dir() else "warn",
        "пакет ragraf-dsh %s" % ("стоит" if plugin.is_dir() else "не стоит"),
        "" if plugin.is_dir() else "dsh plugin --profile %s add <каталог>/integrations/dsh-plugin" % profile)

    budget = _budget(profile)
    if budget["fits_base"]:
        add("instructions-budget", "ok",
            "AGENTS.md %d байт влезает в базовый бюджет 65536" % budget["agents_md_bytes"])
    else:
        add("instructions-budget", "warn",
            "AGENTS.md %d байт НЕ влезает в базовый бюджет 65536; объявлено в профиле: %s — "
            "действует бюджет пресета агента, поэтому объявление может не применяться"
            % (budget["agents_md_bytes"], budget["declared_in_profile"]),
            "измерить действующий бюджет: python tools/install_dsh_plugin.py check")

    if record:
        add("install-record", "ok", "профиль %s, %s" % (record.get("profile", "?"),
                                                        record.get("installed_at", "?")))
    else:
        add("install-record", "warn", "нет записи установщика %s" % (home / RECORD),
            "python tools/install_dsh_plugin.py install --profile %s" % profile)

    failed = [c for c in checks if c["status"] == "fail"]
    return {"ok": not failed, "profile": profile, "dsh_home": str(base),
            "ragraf": found, "checks": checks}


def main(argv: list[str]) -> int:
    args = argv[1:]
    command = (args[0] if args else "where").strip().lower()
    rest = args[1:]
    profile = "desktop"
    for index, part in enumerate(rest):
        if part == "--profile" and index + 1 < len(rest):
            profile = rest[index + 1]
    found = locate()
    if command == "where":
        _write(json.dumps(found, ensure_ascii=False))
        return 0 if found["mode"] != "none" else 1
    if command == "mcp":
        return run_mcp(found, rest)
    if command == "hook":
        stage = rest[0] if rest else "pre-tool"
        return run_hook(found, stage, rest[1:])
    if command == "doctor":
        report = doctor(profile)
        _write(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    _warn("неизвестная команда: " + command)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
