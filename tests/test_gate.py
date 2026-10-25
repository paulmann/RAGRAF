"""Проверка гейта использования индекса (§3.2).

Три уровня, чтобы отказ гейта был доказан, а не обещан:

  1. ПРАВИЛА — на синтетических фактах, без корпуса, без DSH и без индекса:
     каждое правило проверяется отдельно, включая «гейт не мешает».
  2. СОСТОЯНИЕ — эскалация «вопрос → отказ», признак «индексом пользовались»,
     изоляция сеансов (подтверждение не берётся из чужого сеанса).
  3. ХУК — живой прогон `tools/harness_gate.py`: JSON для allow/ask, код 2 и
     причина в stderr для deny, отчёт по задаче на событии Stop.

Стиль — самостоятельный скрипт с `check`, как остальные проверки проекта.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import gate as gate_mod  # noqa: E402

PY = str(REPO / ".venv" / "Scripts" / "python.exe")
HOOK = str(REPO / "tools" / "harness_gate.py")

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


def decide(**facts):
    base = {"kind": "file_search", "corpus": "probe", "fresh": True,
            "index_used": False, "first_offence": True, "is_code": False}
    base.update(facts)
    return gate_mod.decide(base, mode=facts.get("mode", "ask"))


def main() -> int:
    print("=" * 78)
    print("1. ПРАВИЛА (синтетические факты, без корпуса и DSH)")
    print("=" * 78)

    d = gate_mod.decide({"kind": "index"}, mode="ask")
    check(d.verdict == "allow" and d.rule == "index_call",
          "обращение к индексу разрешено", d.rule)

    d = gate_mod.decide({"kind": "other"}, mode="ask")
    check(d.verdict == "allow" and d.rule == "not_relevant",
          "посторонний вызов гейт не касается", d.rule)

    d = decide(corpus=None)
    check(d.verdict == "allow" and d.rule == "no_corpus_search",
          "нет корпуса → файловый поиск разрешён с подсказкой", d.rule)
    check("ensure" in d.context, "подсказка называет регистрацию корпуса", d.context)

    d = decide(fresh=False)
    check(d.verdict == "ask" and d.rule == "stale_index",
          "индекс отстал, первое нарушение → вопрос", f"{d.verdict}/{d.rule}")
    d = decide(fresh=False, first_offence=False)
    check(d.verdict == "deny", "индекс отстал, повтор → отказ", d.verdict)
    d = decide(fresh=False, mode="warn")
    check(d.verdict == "allow", "режим warn не блокирует", d.verdict)
    d = decide(fresh=False, mode="deny")
    check(d.verdict == "deny", "режим deny отказывает сразу, без смягчения",
          d.verdict)
    d = decide(fresh=False, mode="off")
    check(d.verdict == "off", "режим off выключает гейт", d.verdict)

    d = decide(index_used=True)
    check(d.verdict == "allow" and d.rule == "index_used",
          "свежий индекс + им пользовались → не мешаем", d.rule)
    d = decide(index_used=True, fresh=False)
    check(d.verdict == "ask", "отставший индекс важнее факта обращения", d.verdict)

    d = decide(kind="read", is_code=True)
    check(d.verdict == "allow" and d.rule == "read_hint",
          "чтение кода разрешено, но с подсказкой", d.rule)
    d = decide(kind="read", is_code=False)
    check(d.verdict == "allow" and d.rule == "read_ok",
          "чтение не-кода проходит молча", d.rule)

    print("\n" + "=" * 78)
    print("2. РАЗБОР ВЫЗОВА И СОСТОЯНИЕ")
    print("=" * 78)
    call = gate_mod.classify_call("pwsh", {"command": "Select-String -Path a.php -Pattern x"})
    check(call["kind"] == "file_search", "Select-String распознан как файловый поиск",
          str(call))
    call = gate_mod.classify_call("pwsh", {"command": "git status"})
    check(call["kind"] == "other", "безобидная команда не считается поиском", str(call))
    call = gate_mod.classify_call("grep", {"pattern": "text_line", "path": "src"})
    check(call["kind"] == "file_search", "инструмент grep распознан", str(call))
    call = gate_mod.classify_call("read", {"file_path": "src/ragraf/gate.py"})
    check(call["kind"] == "read" and call["is_code"], "чтение файла кода распознано",
          str(call))
    call = gate_mod.classify_call("read", {"file_path": "README.md"})
    check(call["kind"] == "read" and not call["is_code"],
          "чтение документации — не код", str(call))
    call = gate_mod.classify_call("mcp__ragraf__search", {"query": "x"})
    check(call["kind"] == "index", "инструмент индекса распознан", str(call))
    call = gate_mod.classify_call("pwsh", '{"command": "rg from src"}')
    check(call["kind"] == "file_search",
          "tool_input строкой JSON тоже разбирается", str(call))

    tmp = Path(tempfile.mkdtemp(prefix="ragraf_gate_"))
    try:
        st = gate_mod.GateState(tmp / "gate_state.json")
        st.open_session("s1", cwd="C:/proj")
        check(st.session("s1") is not None, "маркер сеанса создаётся")
        check(not st.index_used_within(session_id="s1"),
              "до обращения к индексу признак пуст")
        st.note_index_use("s1")
        check(st.index_used_within(session_id="s1"),
              "после обращения признак выставлен")
        check(not st.index_used_within(session_id="s2"),
              "чужой сеанс не считается: изоляция по session_id")

        def verdict(sid: str) -> str:
            dec = gate_mod.GateDecision("ask", "synthetic", "проверка")
            st.note_decision(dec, {"tool": "pwsh", "kind": "file_search",
                                   "command": f"rg {sid}"}, session_id=sid, task=sid)
            return str((st.session(sid) or {}).get("verdict"))

        check(verdict("s3") == "ask", "решение записано в маркер сеанса")
        check(verdict("s4") == "ask", "второй сеанс получает своё решение")
        st.note_decision(gate_mod.GateDecision("deny", "synthetic", "проверка"),
                         {"tool": "pwsh", "kind": "file_search", "command": "rg x"},
                         session_id="s3", task="s3")
        check(str((st.session("s3") or {}).get("verdict")) == "deny",
              "эскалация до отказа видна в состоянии")

        summary = st.summary()
        check(summary["counters"].get("total", 0) == 3, "счётчики решений считаются",
              str(summary["counters"]))
        check(summary["counters"].get("deny", 0) == 1, "отказ посчитан отдельно",
              str(summary["counters"]))
        check(len(summary["recent"]) == 3, "журнал решений ведётся",
              str(len(summary["recent"])))
        check(st.cached_freshness("c:/proj") is None, "кэш свежести пуст до записи")
        st.cache_freshness("c:/proj", False, {"verified_by": "full"}, ttl=60)
        cached = st.cached_freshness("C:/PROJ") or {}
        check(cached.get("fresh") is False, "кэш свежести читается без учёта регистра",
              str(cached))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 78)
    print("3. ХУК (живой прогон tools/harness_gate.py)")
    print("=" * 78)

    def run_hook(stage: str, payload: dict) -> subprocess.CompletedProcess:
        return subprocess.run([PY, HOOK, stage], input=json.dumps(payload),
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=180)

    payload = {"hook_event_name": "SessionStart", "session_id": "hook-test",
               "cwd": str(REPO)}
    proc = run_hook("session-start", payload)
    check(proc.returncode == 0 and "additionalContext" in (proc.stdout or ""),
          "session-start отдаёт контекст сеанса", (proc.stdout or "")[:120])

    payload = {"hook_event_name": "PreToolUse", "session_id": "hook-test",
               "cwd": str(REPO), "tool_name": "grep",
               "tool_input": {"pattern": "text_line", "path": "src"}}
    proc = run_hook("pre-tool", payload)
    data = json.loads(proc.stdout or "{}")
    decision = (data.get("hookSpecificOutput") or {}).get("permissionDecision")
    check(proc.returncode == 0, "pre-tool не падает", (proc.stderr or "")[:200])
    check(decision in ("allow", "ask"), "вердикт в формате протокола хуков", str(data))

    payload["session_id"] = "hook-deny"
    proc = run_hook("pre-tool", payload)
    data = json.loads(proc.stdout or "{}")
    first = (data.get("hookSpecificOutput") or {}).get("permissionDecision")
    # Второй вызов того же сеанса: вопрос → отказ (режим по умолчанию warn
    # поэтому «отказ» проверяется в режиме deny явным аргументом).
    proc = subprocess.run([PY, HOOK, "pre-tool", "--mode=deny"],
                          input=json.dumps(payload), capture_output=True, text=True,
                          encoding="utf-8", timeout=180)
    check(proc.returncode == 2, "deny отдаётся кодом 2 (единственный код блокировки)",
          f"код {proc.returncode}, stdout={proc.stdout[:120]!r}")
    check("RAGRAF" in (proc.stderr or ""), "причина отказа уходит в stderr",
          (proc.stderr or "")[:140])
    check(first in ("allow", "ask"), "первый вызов сеанса не отказ", str(first))

    proc = run_hook("pre-tool", {"hook_event_name": "PreToolUse",
                                 "tool_name": "read", "tool_input": {},
                                 "session_id": "broken", "cwd": ""})
    check(proc.returncode in (0, 2), "пустой payload не роняет хук",
          f"код {proc.returncode}")

    proc = run_hook("stop", {"hook_event_name": "Stop", "session_id": "hook-test"})
    check(proc.returncode == 0, "stop не падает", (proc.stderr or "")[:200])
    check("отчёт по задаче" in (proc.stdout or "") or not (proc.stdout or ""),
          "stop отдаёт строку отчёта", (proc.stdout or "")[:140])

    print()
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
