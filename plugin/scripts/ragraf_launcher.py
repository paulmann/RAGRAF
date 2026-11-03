"""Лаунчер RAGRAF для плагина Claude Code: находит установку и передаёт ей работу.

Зачем лаунчер, а не прямой путь в `.mcp.json`. Плагин ставится в каталог
клиента (`~/.claude/plugins/...`), а сам RAGRAF живёт в другом месте: в рабочей
копии (`C:\\AutoClaw\\RAGRAF`) или в установленном пакете. Жёсткий абсолютный
путь в манифесте означал бы «плагин работает только у автора»; переменная
окружения в манифесте — «пользователь обязан прочитать README до установки».
Лаунчер снимает оба ограничения: он ищет установку по известным правилам и
называет причину отказа, если не нашёл.

Порядок поиска (первое найденное выигрывает):

  1. `RAGRAF_HOME` — явное указание (рабочая копия или каталог пакета);
  2. известные места рабочей копии: `C:\\AutoClaw\\RAGRAF`, `~/RAGRAF`,
     `~/src/RAGRAF`, `/opt/ragraf`, `/usr/local/share/ragraf`;
  3. установленный пакет: `import ragraf` из текущего интерпретатора.

Что запускается:

    ragraf_launcher.py mcp  [args…]   # MCP-сервер (stdio)
    ragraf_launcher.py hook <стадия>  # хук клиента (JSON на stdin)
    ragraf_launcher.py where          # только показать, что найдено (JSON)

Границы, названные прямо:

  * **Хук требует рабочей копии.** Стадии хука живут в `tools/harness_gate.py`;
    в установленный пакет этот файл не входит. Если найдена только установка
    пакетом, хук не подменяется суррогатом: он завершается кодом 0 (сеанс не
    ломается) и пишет в stderr, чего не хватает. Молча «разрешить» было бы
    хуже: гейт выглядел бы работающим.
  * **Пересборка индекса не запускается.** Лаунчер не индексирует: это делает
    агент вызовом `ragraf_ensure` (или человек командой `ragraf ensure`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

#: Известные места рабочей копии. Порядок: сначала машинный каталог проекта,
#: затем домашние варианты, затем системные — от самого вероятного к редкому.
KNOWN_HOMES = (
    Path(r"C:\AutoClaw\RAGRAF"),
    Path.home() / "RAGRAF",
    Path.home() / "src" / "RAGRAF",
    Path("/opt/ragraf"),
    Path("/usr/local/share/ragraf"),
)


def _is_checkout(path: Path | None) -> bool:
    """Похож ли каталог на рабочую копию: есть сервер и пакет исходников."""
    if not path:
        return False
    return ((path / "tools" / "ragraf-mcp.py").is_file()
            and (path / "src" / "ragraf" / "__init__.py").is_file())


def _package_available() -> dict | None:
    """Установленный пакет: путь к нему и версия (для отчёта)."""
    try:
        import ragraf  # noqa: PLC0415 — импорт нужен только здесь
    except Exception:  # noqa: BLE001 — отсутствие пакета не ошибка, а факт
        return None
    return {"root": str(Path(ragraf.__file__).resolve().parent),
            "version": str(getattr(ragraf, "__version__", ""))}


def locate() -> dict:
    """Что найдено: рабочая копия, пакет либо ничего.

    Результат — структура, а не пара значений: вызывающему нужен и способ
    запуска, и основание выбора (его печатает `where` и называет отказ).
    """
    env_home = os.environ.get("RAGRAF_HOME")
    if env_home:
        candidate = Path(env_home).expanduser()
        if _is_checkout(candidate):
            return {"mode": "checkout", "root": str(candidate),
                    "why": "RAGRAF_HOME"}
        # Явное указание сильнее догадок: если оно неверно, честнее сказать
        # об этом, чем молча взять другое место.
        return {"mode": "none", "root": "", "why": f"RAGRAF_HOME={env_home} не похож на RAGRAF"}
    for candidate in KNOWN_HOMES:
        if _is_checkout(candidate):
            return {"mode": "checkout", "root": str(candidate),
                    "why": f"известное место {candidate}"}
    package = _package_available()
    if package:
        return {"mode": "package", "root": package["root"], "why": "пакет ragraf",
                "version": package["version"]}
    return {"mode": "none", "root": "",
            "why": "ни RAGRAF_HOME, ни известных мест, ни пакета ragraf"}


def pick_python(root: str) -> str:
    """Интерпретатор для RAGRAF: явный выбор, .venv рабочей копии, затем свой.

    Почему не текущий по умолчанию. Клиент запускает лаунчер тем `python`, который
    нашёлся в PATH, и это может быть чужой интерпретатор без зависимостей RAGRAF
    (`fastembed`, `tree_sitter`, `sqlite_vec`). Замер 03.11.2026: плагин в
    песочнице давал `CONNECTION_CLOSED`, потому что `python` в PATH — вендоренный
    интерпретатор AutoClaw без этих пакетов. Поэтому первым берётся `.venv`
    рабочей копии: он и создан для того, чтобы зависимости были на месте.
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


def _run(argv: list[str], env_extra: dict | None = None) -> int:
    """Запустить дочерний процесс, наследуя stdio.

    Потоки наследуются, поэтому MCP-обмен идёт напрямую: родитель не читает и не
    буферизует сообщения. `execve` здесь не годится: на Windows его эмуляция в
    этой среде завершалась аварийно (код 0xC0000005 при первом же запуске
    03.11.2026), а наследование stdio ведёт себя одинаково на всех платформах.
    """
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    try:
        return subprocess.call(argv, env=env)
    except Exception as exc:  # noqa: BLE001 — отказ должен быть назван
        sys.stderr.write(f"[RAGRAF] запуск не удался: {type(exc).__name__}: {exc}\n")
        return 3


def run_mcp(found: dict, rest: list[str]) -> int:
    python = pick_python(found.get("root") or "")
    if found["mode"] == "checkout":
        server = str(Path(found["root"]) / "tools" / "ragraf-mcp.py")
        return _run([python, server, *rest])
    if found["mode"] == "package":
        return _run([python, "-m", "ragraf.mcp_server", *rest])
    sys.stderr.write("[RAGRAF] сервер не запущен: " + found["why"]
                     + ". Установите RAGRAF (`pip install ragraf`) или укажите "
                       "RAGRAF_HOME на рабочую копию.\n")
    return 3


def run_hook(found: dict, stage: str) -> int:
    # Рабочая копия: стадии хука — в tools/harness_gate.py, единственная копия
    # логики. Пакетная установка хук не обслуживает (см. границы в docstring).
    if found["mode"] == "checkout":
        gate = str(Path(found["root"]) / "tools" / "harness_gate.py")
        return _run([pick_python(found["root"]), gate, stage])
    sys.stderr.write("[RAGRAF] хук пропущен: " + found["why"]
                     + ". Стадии хука есть только в рабочей копии RAGRAF; "
                       "сервер и навык при этом работают.\n")
    return 0


def main(argv: list[str]) -> int:
    args = argv[1:]
    command = (args[0] if args else "where").strip().lower()
    rest = args[1:]
    found = locate()
    if command == "where":
        sys.stdout.write(json.dumps(found, ensure_ascii=False) + "\n")
        return 0 if found["mode"] != "none" else 1
    if command == "mcp":
        return run_mcp(found, rest)
    if command == "hook":
        return run_hook(found, (rest[0] if rest else "pre-tool"))
    sys.stderr.write("неизвестная команда: " + command + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
