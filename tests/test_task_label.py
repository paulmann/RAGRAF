"""Проверка метки задачи (`RAGRAF_TASK`): цепочка «env → названная → сеанс».

Зачем проверка. Метрика «доля задач с обращением к индексу» (§3.4) считается по
метке задачи. Клиент не может передать метку в долгоживущий MCP-процесс (его
окружение задаётся при старте), поэтому метку задаёт агент вызовом `ragraf_task`,
а при её отсутствии берётся активный сеанс гейта. Если цепочка разрешается
неверно, обращения попадают в чужую задачу или в «(без задачи)», и метрика врёт.

Стиль — самостоятельный скрипт с `check`, как остальные проверки проекта.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import gate as gate_mod  # noqa: E402
from ragraf import mcp_server  # noqa: E402
from ragraf.cli import OPS  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.facade import Facade  # noqa: E402

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


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_task_"))
    # Состояние гейта — во временный файл: проверка не имеет права писать в
    # боевое состояние RAGRAF.
    saved_path_fn = gate_mod.state_path
    gate_mod.state_path = lambda: tmp / "gate_state.json"  # type: ignore[assignment]
    saved_env = os.environ.get("RAGRAF_TASK")
    try:
        cfg = load_config(REPO / "config" / "ragraf.yaml")
        root = str(cfg.root)
        st = gate_mod.state()

        print("=" * 78)
        print("1. СОСТОЯНИЕ: названная задача, срок, изоляция разработок")
        print("=" * 78)
        check(st.current_task(root) == "", "до вызова метка пуста")
        st.set_current_task("задача-А", root=root)
        check(st.current_task(root) == "задача-А", "метка названа и прочитана")
        check(st.current_task(str(tmp / "другой-проект")) == "",
              "метка не приклеивается к другой разработке")

        # Просроченная метка: правим время записи напрямую (как это сделало бы
        # время, а не вызов). Проверяются оба срока: явный (2 минуты) и общий
        # (рабочий день).
        state_obj = gate_mod.GateState(tmp / "gate_state.json")
        data = state_obj._load()  # noqa: SLF001 — свой класс
        data["current_task"]["ts"] = time.time() - 120
        state_obj._save()
        check(gate_mod.state().current_task(root, ttl=60) == "",
              "метка старше явного срока не читается")
        check(gate_mod.state().current_task(root) == "задача-А",
              "та же метка в пределах срока по умолчанию читается")

        data = state_obj._load()  # noqa: SLF001
        data["current_task"]["ts"] = time.time() - 9 * 3600
        state_obj._save()
        check(gate_mod.state().current_task(root) == "",
              "метка старше рабочего дня не применяется")

        st.set_current_task("задача-Б", root=root)
        check(st.current_task(root) == "задача-Б", "метка заменяется новой")
        st.set_current_task("", root=root)
        check(st.current_task(root) == "", "пустое имя снимает метку")

        print("\n" + "=" * 78)
        print("2. ФАСАД: порядок разрешения метки")
        print("=" * 78)
        gate_mod.state().set_current_task("задача-В", root=root)
        fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        check(fc.task == "задача-В", "фасад взял названную задачу", repr(fc.task))
        fc.close()

        env_facade = Facade(cfg, tool="selftest", telemetry=False, watch=False,
                            task="задача-из-env")
        check(env_facade.task == "задача-из-env",
              "явная метка (env/аргумент) перебивает названную", repr(env_facade.task))
        env_facade.close()

        # Сеанс гейта — следующий в очереди после названной метки.
        gate_mod.state().set_current_task("", root=root)
        gate_mod.state().open_session("sess-1", cwd=root)
        session_facade = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        check(session_facade.task == "sess-1",
              "без названной метки берётся активный сеанс", repr(session_facade.task))
        session_facade.close()

        print("\n" + "=" * 78)
        print("3. ОПЕРАЦИЯ И ПОВЕРХНОСТЬ")
        print("=" * 78)
        fc = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        result = fc.op_task_set("задача-Г")
        check((result.get("result") or {}).get("task") == "задача-Г",
              "op_task_set назвал задачу", str(result.get("result")))
        check((result.get("result") or {}).get("env_var") == "RAGRAF_TASK",
              "в ответе назван источник метки", str(result.get("result")))
        fc2 = Facade(cfg, tool="selftest", telemetry=False, watch=False)
        check(fc2.task == "задача-Г", "новый фасад видит названную задачу",
              repr(fc2.task))
        cleared = fc2.op_task_set(clear=True)
        check((cleared.get("result") or {}).get("task") == "",
              "снятие метки возвращает пустую", str(cleared.get("result")))
        check((cleared.get("result") or {}).get("previous") == "задача-Г",
              "снятие называет прежнюю метку", str(cleared.get("result")))
        fc.close()
        fc2.close()

        tools = [t["name"] for t in mcp_server.TOOLS]
        check("ragraf_task" in tools, "инструмент ragraf_task объявлен", str(len(tools)))
        check("ragraf_task_stats" in tools, "инструмент ragraf_task_stats объявлен")
        check("task_set" in OPS, "операция task_set доступна в CLI")
        check("task_stats" in OPS, "операция task_stats доступна в CLI")
        meta = {k: v for k, v in mcp_server._TOOL_META.items()  # noqa: SLF001
                if k == "ragraf_task"}
        check(bool(meta) and meta["ragraf_task"]["readOnlyHint"] is False,
              "ragraf_task помечен как изменяющий", str(meta))
        labels = {t["name"] for t in mcp_server.TOOLS}
        check(len(labels) == len(tools), "имена инструментов уникальны")

        print("\n" + "=" * 78)
        print("4. ПЕРЕМЕННАЯ ОКРУЖЕНИЯ")
        print("=" * 78)
        os.environ["RAGRAF_TASK"] = "метка-из-окружения"
        from ragraf.cli import _task_from_env  # noqa: PLC0415

        check(_task_from_env() == "метка-из-окружения",
              "CLI читает RAGRAF_TASK", _task_from_env())
        # MCP-сервер читает ту же переменную при старте процесса: его окружение
        # задаётся один раз, поэтому метка из env — это метка ВСЕГО процесса.
        server = mcp_server.McpServer(config_path=str(REPO / "config" / "ragraf.yaml"),
                                      agent="Probe", agent_kind="internal",
                                      watch=False)
        check(server.task == "метка-из-окружения",
              "сервер MCP взял метку из окружения", repr(server.task))
    finally:
        gate_mod.state_path = saved_path_fn  # type: ignore[assignment]
        if saved_env is None:
            os.environ.pop("RAGRAF_TASK", None)
        else:
            os.environ["RAGRAF_TASK"] = saved_env
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
