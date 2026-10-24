"""Хук DSH: гейт использования индекса RAGRAF (§3.2).

Запускается мостом хуков `@deepseek-ai/dsh-hooks-claude-code`:

    harness_gate.py session-start   # SessionStart  → маркер сеанса
    harness_gate.py pre-tool        # PreToolUse    → allow / ask / deny
    harness_gate.py stop            # Stop          → строка отчёта по задаче

Payload приходит на stdin JSON-ом (поля `session_id`, `cwd`, `hook_event_name`,
`tool_name`, `tool_input`). Ответ — на stdout: JSON-форма
`hookSpecificOutput` (allow/ask) либо код возврата 2 с причиной в stderr (deny).

ПОЧЕМУ ЭТО ОТДЕЛЬНЫЙ ПРОЦЕСС, А НЕ ПРАВКА ЯДРА. Клиент DSH лежит в упакованном
`app.asar`; единственная доступная точка перехвата — хук. Поэтому вся логика
живёт в RAGRAF (`src/ragraf/gate.py`), а здесь только чтение входа и ответ.

Границы, заданные протоколом хуков (проверено по коду `dsh-hook-protocol`):
`updatedInput` разбирается, но не применяется — переписать команду нельзя;
`transcript_path` всегда пуст — историю сеанса гейт не читает; конфиг хуков
читается один раз при старте процесса.

Отказ гейта НИКОГДА не должен ломать сеанс: любая ошибка внутри скрипта
завершается кодом 0 (разрешить) и предупреждением в stderr.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

DEFAULT_MODE = os.environ.get("RAGRAF_GATE_MODE", "ask")


def read_payload() -> dict:
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def emit(payload: dict) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()
    return 0


def session_start(payload: dict) -> int:
    from ragraf import gate as gate_mod

    state = gate_mod.state()
    sid = str(payload.get("session_id") or "")
    state.open_session(sid, str(payload.get("cwd") or ""))
    mode = DEFAULT_MODE.lower()
    text = (
        f"RAGRAF: гейт использования индекса активен (режим {mode}). "
        "Индекс корпуса спрашивается перед вызовом инструмента: "
        "`search_multi`/`symbol`/`impact` — вместо файлового поиска, "
        "`ensure` — если индекс отстал."
    )
    return emit({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                        "additionalContext": text}})


def pre_tool(payload: dict) -> int:
    from ragraf import gate as gate_mod

    decision = gate_mod.handle_payload(payload)
    out, code = decision.hook_response()
    if decision.verdict == "deny":
        # Код 2 — единственный код, который протокол считает блокировкой;
        # причина уходит модели из stderr.
        sys.stderr.write(decision.stderr() + "\n")
        sys.stderr.flush()
        return 2
    if out:
        return emit(json.loads(out))
    # Вердикт без ответа (например, вызов вне правил) — молча разрешаем.
    return emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                        "permissionDecision": "allow"}})


def stop(payload: dict) -> int:
    """Конец хода: приложить строку отчёта по задаче (требование §3.4)."""
    from ragraf.config import load
    from ragraf.facade import Facade

    config = os.environ.get("RAGRAF_CONFIG") or str(REPO / "config" / "ragraf.yaml")
    if not Path(config).exists():
        return 0
    cfg = load(config)
    fc = Facade(cfg, tool="hook", telemetry=False, watch=False)
    try:
        task = os.environ.get("RAGRAF_TASK") or ""
        report = fc.op_task_stats(task=task or None, limit=3)
    finally:
        fc.close()
    result = report.get("result") or {}
    lines = [line for line in str(result.get("markdown") or "").splitlines()
             if line.startswith("*")][:4]
    if not lines:
        return 0
    text = "RAGRAF, отчёт по задаче:\n" + "\n".join(lines)
    return emit({"hookSpecificOutput": {"hookEventName": "Stop",
                                        "additionalContext": text}})


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    mode_arg = next((a.split("=", 1)[1] for a in argv[1:] if a.startswith("--mode=")), None)
    if mode_arg:
        os.environ["RAGRAF_GATE_MODE"] = mode_arg
    stage = (args[0] if args else "pre-tool").strip().lower()
    payload = read_payload()
    try:
        if stage in ("session-start", "sessionstart"):
            return session_start(payload)
        if stage in ("stop",):
            return stop(payload)
        return pre_tool(payload)
    except Exception as exc:  # noqa: BLE001 — хук не имеет права ломать сеанс
        sys.stderr.write(f"[RAGRAF] гейт не сработал ({type(exc).__name__}: {exc}); "
                         f"вызов разрешён\n")
        sys.stderr.flush()
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
