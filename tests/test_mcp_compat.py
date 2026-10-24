"""Совместимость MCP-сервера с внешними клиентами (DeepSeek Harness, AutoClaw, SagaAI).

Проверяется то, что измерено как дефект, а не общие соображения:

  1. Окружение УРЕЗАНО, как его передаёт официальный MCP SDK (и как его ещё
     чистит DeepSeek Harness): ни `PYTHONUTF8`, ни `PYTHONIOENCODING` в него
     не попадают. Раньше сервер из-за этого падал на первом же ответе с русским
     текстом (`UnicodeEncodeError: 'charmap' codec` на кодировке cp1252 в
     `send()`), то есть клиент получал мёртвый процесс вместо инструментов.

  2. `--project` не обязателен: корпус определяется по рабочему каталогу через
     реестр разработок. Иначе один клиент, запускающий сервер без аргументов,
     не мог бы работать ни с одним проектом.

  3. Корпус переключается во время работы (`ragraf_use_project`): один сервер
     обслуживает несколько проектов, и смена обязана быть видна в ответе
     (ревизия берётся у НОВОГО корпуса, а не у прежнего).

Запуск: python tests/test_mcp_compat.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Тот же урок, что и у сервера: проверка печатает русский текст, а локаль
# процесса может быть cp1252. Окружение теста чистится намеренно, поэтому
# кодировка вывода ставится явно, а не берётся из PYTHONIOENCODING.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "tools" / "ragraf-mcp.py"
OWN_CONFIG = REPO / "config" / "ragraf.yaml"


def pick_interpreter() -> Path:
    """Выбрать интерпретатор, у которого есть зависимости RAGRAF.

    Здесь и живёт измеренная ловушка: внешний клиент передаёт серверу
    УРЕЗАННОЕ окружение, а зависимости RAGRAF на этой машине лежали только
    в пользовательском каталоге пакетов, достижимом ЧЕРЕЗ переменную
    `PYTHONUSERBASE` (её же и вычищает Harness). Самодостаточен только
    `.venv` проекта: там зависимости стоят на месте без единой переменной.
    Поэтому рецепт запуска — интерпретатор из `.venv`, а не `python` из PATH.
    """
    venv = REPO / ".venv" / "Scripts" / "python.exe"
    if venv.is_file():
        probe = subprocess.run(
            [str(venv), "-c", "import yaml,fastembed,sqlite_vec,tree_sitter,ijson,tiktoken"],
            capture_output=True,
        )
        if probe.returncode == 0:
            return venv
    return Path(sys.executable)

# Ровно тот набор переменных, который официальный MCP SDK наследует дочернему
# процессу на Windows (`@modelcontextprotocol/client/dist/stdio.mjs`).
SDK_INHERITED_ENV = (
    "APPDATA",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "PATH",
    "PROCESSOR_ARCHITECTURE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "USERNAME",
    "USERPROFILE",
    "PROGRAMFILES",
)

PASSED = 0
FAILED = 0


def check(condition: bool, label: str, detail: str = "") -> bool:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [OK ] {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label}")
        if detail:
            print(f"         {detail}")
    return condition


def scrubbed_env() -> dict[str, str]:
    """Окружение как у внешнего MCP-клиента: без переменных Python."""
    env = {name: os.environ[name] for name in SDK_INHERITED_ENV if name in os.environ}
    env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
    # Страховка: если что-то из PYTHON* просочилось, тест перестанет быть честным.
    for leaked in ("PYTHONUTF8", "PYTHONIOENCODING", "PYTHONPATH"):
        env.pop(leaked, None)
    return env


def run_session(requests: list[dict], cwd: Path, project: str | None,
                interpreter: Path | None = None) -> tuple[list[dict], str, int]:
    """Один прогон сервера: запросы в stdin, разобранные ответы и stderr."""
    argv = [str(interpreter or pick_interpreter()), str(SERVER)]
    if project is not None:
        argv += ["--project", project]
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(cwd), env=scrubbed_env(),
    )
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
    try:
        out, err = proc.communicate(payload.encode("utf-8"), timeout=300)
    except subprocess.TimeoutExpired:
        proc.kill()
        return [], "таймаут", -1

    responses: list[dict] = []
    for line in out.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            responses.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return responses, err.decode("utf-8", "replace"), proc.returncode


def text_of(response: dict) -> str:
    content = (response.get("result") or {}).get("content") or []
    return content[0].get("text", "") if content else ""


def first_line(text: str) -> str:
    return text.splitlines()[0] if text else ""


def main() -> int:
    print("=== окружение без PYTHON* (как передаёт MCP-клиент) ===")
    responses, stderr, _code = run_session(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05",
                        "clientInfo": {"name": "Harness/test"}, "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "ragraf_index_status", "arguments": {}}},
        ],
        cwd=REPO, project=str(OWN_CONFIG),
    )

    init = next((r for r in responses if r.get("id") == 1), None)
    check(
        bool(init) and init.get("result", {}).get("serverInfo", {}).get("name") == "ragraf",
        "initialize отвечает без переменных окружения Python",
        f"ответ: {init}",
    )

    tools = next((r for r in responses if r.get("id") == 2), None)
    names = [t.get("name") for t in ((tools or {}).get("result") or {}).get("tools", [])]
    check(len(names) >= 23, f"tools/list отдаёт полный набор инструментов ({len(names)})",
          f"имена: {names[:5]}")

    status = next((r for r in responses if r.get("id") == 3), None)
    head = first_line(text_of(status or {}))
    check(
        "rev=" in head,
        "ответ с РУССКИМ текстом доходит до клиента (раньше — падение на cp1252)",
        f"шапка ответа: {head!r}; stderr: {stderr[:300]!r}",
    )
    check(
        "UnicodeEncodeError" not in stderr,
        "в stderr нет UnicodeEncodeError",
        stderr[:400],
    )
    check(
        "ОГРАНИЧЕНИЕ:" in text_of(status or {}) or "rev=" in head,
        "текст ответа не искажён заменой символов",
    )

    print("\n=== корпус определяется без --project (по рабочему каталогу) ===")
    responses, stderr, _code = run_session(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "ragraf_index_status", "arguments": {}}},
        ],
        cwd=REPO, project=None,
    )
    status = next((r for r in responses if r.get("id") == 2), None)
    check(
        "rev=" in first_line(text_of(status or {})),
        "из корня проекта сервер находит свой корпус без --project",
        f"stderr: {stderr[:300]!r}",
    )
    check("ragraf.yaml" in stderr, "в диагностике назван найденный конфиг", stderr[:300])

    print("\n=== каталог без корпуса: сервер поднимается и честно объясняет выбор ===")
    # Намеренное поведение: клиент может держать ОДИН сервер на несколько
    # проектов, поэтому отсутствие корпуса — не отказ в запуске. Сервер обязан
    # подняться, назвать причину в stderr и ответить на служебные операции
    # (catalog, use_project), а не умереть молча.
    responses, stderr, code = run_session(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "ragraf_index_status", "arguments": {}}},
        ],
        cwd=Path(os.environ.get("TEMP", str(REPO))), project=None,
    )
    check(code == 0, "без корпуса сервер не падает и не завершается с ошибкой",
          f"код={code}; stderr={stderr[:300]!r}")
    check("корпус не определён" in stderr,
          "причина названа в stderr", stderr[:300])
    tools = next((r for r in responses if r.get("id") == 2), None)
    names = [t.get("name") for t in ((tools or {}).get("result") or {}).get("tools", [])]
    check("ragraf_use_project" in names,
          "даже без корпуса предложен инструмент выбора корпуса",
          f"имена: {names[:5]}")
    # Операция, которой корпус нужен, обязана отказать с ПРИЧИНОЙ, а не с типом исключения.
    status = next((r for r in responses if r.get("id") == 3), None)
    body = text_of(status or {})
    check("use_project" in body and "ConfigError" not in body,
          "нуждающаяся в корпусе операция отказывает с понятной подсказкой",
          f"текст: {body[:200]!r}")

    print("\n=== переключение корпуса на живом сервере (один сервер — много проектов) ===")
    responses, stderr, _code = run_session(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "ragraf_use_project", "arguments": {"project": "revoap.com"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "ragraf_use_project", "arguments": {"project": str(REPO)}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "ragraf_use_project", "arguments": {"project": "нет-такого-корпуса"}}},
        ],
        cwd=REPO, project=str(OWN_CONFIG),
    )

    own_head = first_line(text_of(next((r for r in responses if r.get("id") == 2), {})))
    back_head = first_line(text_of(next((r for r in responses if r.get("id") == 3), {})))
    check("op=use_project" in own_head and "found=True" in own_head,
          "переключение по имени разработки из реестра", own_head)
    check(own_head != back_head or "rev=" in back_head,
          "переключение по каталогу проекта возвращает состояние ДРУГОГО корпуса",
          f"первый: {own_head!r}; второй: {back_head!r}")
    if OWN_CONFIG.is_file():
        own_rev = ""
        for line in (REPO / "config" / "ragraf.yaml").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("project:"):
                own_rev = line
        check(
            "rev=" in back_head,
            "в ответе переключения видна ревизия выбранного корпуса",
            back_head,
        )

    missing = next((r for r in responses if r.get("id") == 4), None)
    missing_head = first_line(text_of(missing or {}))
    check("found=False" in missing_head,
          "неизвестный корпус даёт честный отказ, а не подмену проекта",
          missing_head)

    print("\n=== конверт ответа: обязательное поле op у каждого инструмента ===")
    # Замерено 13.10.2026 в HARNESS: `ragraf_usage` отдавал сводку журнала БЕЗ
    # конверта, и клиент, проверяющий `outputSchema`, отбрасывал ответ ЦЕЛИКОМ:
    # «Structured content does not match the tool's output schema: data must have
    # required property 'op'». Данные не доходили до агента, а шапка текстового
    # блока печатала `op=None rev=None capability=None provenance=None found=None`.
    # Поэтому проверяется не «инструмент ответил», а наличие конверта у ответа.
    responses, stderr, _code = run_session(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "ragraf_usage", "arguments": {"scope": "session"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "ragraf_context", "arguments": {}}},
        ],
        cwd=REPO, project=str(OWN_CONFIG),
    )
    for req_id, tool in ((2, "ragraf_usage"), (3, "ragraf_context")):
        resp = next((r for r in responses if r.get("id") == req_id), None) or {}
        structured = (resp.get("result") or {}).get("structuredContent") or {}
        check(
            bool(structured.get("op")),
            f"{tool}: ответ несёт обязательное поле op (иначе клиент его отбросит)",
            f"op={structured.get('op')!r}; stderr={stderr[:200]!r}",
        )
        check(
            "op=None" not in text_of(resp),
            f"{tool}: шапка называет операцию, а не op=None",
            text_of(resp)[:120],
        )

    print("\n" + "=" * 78)
    print(f"ИТОГ: пройдено {PASSED}, провалено {FAILED}")
    print("=" * 78)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
