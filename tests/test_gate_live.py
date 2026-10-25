"""Приёмочный прогон гейта на ЖИВОМ корпусе (§3.2, шаг 5).

Проверяется не логика правил (она проверена в `test_gate.py` на синтетических
фактах), а поведение на настоящем корпусе: пока индекс актуален, файловый поиск
проходит; после правки кода (индекс отстал) — получает вопрос или отказ с
названной заменой; обращение к индексу снимает вопрос.

Прогон идёт на ВРЕМЕННОМ корпусе: правки боевого корпуса в проверке недопустимы.
Один и тот же гараж `GateState` используется всеми шагами — так проверяется и
состояние сеанса, и кэш свежести.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import gate as gate_mod  # noqa: E402
from ragraf import watch as watch_mod  # noqa: E402
from ragraf.catalog import Catalog  # noqa: E402
from ragraf.config import load as load_config  # noqa: E402
from ragraf.indexer import Indexer  # noqa: E402
from ragraf.init_corpus import create_config  # noqa: E402

PASS = 0
FAIL = 0
LOG: list[str] = []
# Реестр разработок: временный корпус регистрируется так же, как боевой (через
# `watch.record_index_run`), а после прогона файл реестра возвращается как был —
# проверка не имеет права оставлять в реестре временный путь.
CATALOG = REPO / "catalog" / "catalog.json"


def check(condition: bool, title: str, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK ] {title}")
    else:
        FAIL += 1
        print(f"  [FAIL] {title}" + (f"\n      {detail}" if detail else ""))


PHP = """<?php
function step_%(i)d($x) {
    return $x * %(i)d;
}
"""


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_gate_live_"))
    catalog_backup = CATALOG.read_bytes() if CATALOG.exists() else None
    try:
        src = tmp / "src"
        src.mkdir()
        for i in range(6):
            (src / f"mod{i}.php").write_text(PHP % {"i": i}, encoding="utf-8",
                                             newline="\n")
        (tmp / ".gitignore").write_text("probe\n", encoding="utf-8", newline="\n")
        cfg_path, _ = create_config(tmp, project="gatelive",
                                    config_path=tmp / "gate.yaml")
        text = cfg_path.read_text(encoding="utf-8").replace(
            "joern_enabled: true", "joern_enabled: false")
        cfg_path.write_text(text, encoding="utf-8", newline="\n")
        cfg = load_config(cfg_path)

        indexer = Indexer(cfg)
        try:
            report = indexer.run(with_vectors=False, budget_seconds=300)
        finally:
            indexer.close()
        check(report.ok, "временный корпус проиндексирован", str(report.notes))
        watch_mod.record_index_run(cfg, report, trigger="test-gate-live")
        found = gate_mod.find_corpus(str(tmp))
        check(found is not None, "гейт находит корпус по реестру", str(found))

        state = gate_mod.GateState(tmp / "gate_state.json")
        session = "acceptance"
        state.open_session(session, cwd=str(tmp))
        search_call = {
            "hook_event_name": "PreToolUse", "session_id": session, "cwd": str(tmp),
            "tool_name": "pwsh",
            "tool_input": {"command": "Select-String -Path src -Pattern step_1"},
        }

        print("\n=== 1. Корпус свеж, индексом не пользовались → предупреждение ===")
        payload = dict(search_call)
        decision = gate_mod.handle_payload(payload, mode="warn", gate_state=state)
        check(decision.verdict == "allow" and decision.rule == "index_unused_warn",
              "warn: вызов разрешён, подсказка приложена",
              f"{decision.verdict}/{decision.rule}")
        check("search_multi" in decision.context, "подсказка называет операцию",
              decision.context)
        LOG.append(f"свеж, без обращений: {decision.verdict} ({decision.rule})")

        print("\n=== 2. То же в режиме ask → вопрос с причиной ===")
        decision = gate_mod.handle_payload(dict(search_call), mode="ask",
                                           gate_state=state)
        check(decision.verdict == "ask", "ask: вопрос вместо разрешения",
              decision.verdict)
        check(bool(decision.reason) and bool(decision.context),
              "названы и причина, и замена", f"{decision.reason} / {decision.context}")
        LOG.append(f"свеж, без обращений (ask): {decision.verdict}")

        print("\n=== 3. Обращение к индексу → вопрос снят ===")
        gate_mod.handle_payload({
            "hook_event_name": "PreToolUse", "session_id": session, "cwd": str(tmp),
            "tool_name": "mcp__ragraf__search_multi",
            "tool_input": {"queries": ["step_1|step_2"]},
        }, mode="ask", gate_state=state)
        decision = gate_mod.handle_payload(dict(search_call), mode="ask",
                                           gate_state=state)
        check(decision.verdict == "allow" and decision.rule == "index_used",
              "после обращения к индексу файловый поиск разрешён",
              f"{decision.verdict}/{decision.rule}")
        LOG.append(f"после обращения к индексу: {decision.verdict} ({decision.rule})")

        print("\n=== 4. Индекс отстал (правка кода) → отказ в режиме deny ===")
        (src / "mod0.php").write_text(PHP % {"i": 99} + "// правка\n",
                                      encoding="utf-8", newline="\n")
        # Инвалидация ЯВНАЯ: у кэша свежести есть нижняя граница 30 секунд,
        # поэтому `ttl=0` его не сбрасывал — измерено этим же прогоном, когда
        # отставший индекс продолжал считаться свежим.
        state.forget_freshness(str(tmp))
        decision = gate_mod.handle_payload(dict(search_call), mode="deny",
                                           gate_state=state)
        check(decision.verdict == "deny", "отставший индекс → отказ",
              f"{decision.verdict}/{decision.rule}")
        check(decision.rule == "stale_index", "правило названо", decision.rule)
        out, code = decision.hook_response()
        check(code == 2 and not out, "отказ отдаётся кодом 2 без stdout",
              f"код {code}, stdout={out[:60]!r}")
        check("RAGRAF" in decision.stderr() and "ensure" in decision.stderr(),
              "в причине отказа есть и метка, и замена", decision.stderr()[:140])
        LOG.append(f"индекс отстал (deny): {decision.verdict} ({decision.rule}), exit {code}")

        print("\n=== 5. Обращение к индексу не отменяет отказ при отставшем индексе ===")
        gate_mod.handle_payload({
            "hook_event_name": "PreToolUse", "session_id": session, "cwd": str(tmp),
            "tool_name": "mcp__ragraf__search", "tool_input": {"query": "step_1"},
        }, mode="deny", gate_state=state)
        decision = gate_mod.handle_payload(dict(search_call), mode="deny",
                                           gate_state=state)
        check(decision.verdict == "deny",
              "отставший индекс важнее факта обращения", decision.verdict)
        LOG.append(f"после пересборки индекса нужен ensure: {decision.verdict}")

        print("\n=== 6. Пересборка → снова разрешено ===")
        indexer = Indexer(cfg)
        try:
            report2 = indexer.run(with_vectors=False, budget_seconds=300)
        finally:
            indexer.close()
        watch_mod.record_index_run(cfg, report2, trigger="test-gate-live")
        state.forget_freshness(str(tmp))
        decision = gate_mod.handle_payload(dict(search_call), mode="deny",
                                           gate_state=state)
        check(decision.verdict == "allow",
              "после пересборки отказ снят", f"{decision.verdict}/{decision.rule}")
        LOG.append(f"после пересборки: {decision.verdict} ({decision.rule})")

        print("\n=== 7. Вне корпуса гейт не мешает ===")
        # Путь ВНЕ всех корпусов задаётся явно: временный каталог в песочнице DSH
        # лежит ВНУТРИ рабочего каталога (измерено 21.10.2026), то есть внутри
        # корпуса RAGRAF, и проверка «вне корпуса» ловила наш же корпус.
        outside = dict(search_call, cwd=r"C:\__ragraf_gate_outside__\project",
                       session_id="other")
        decision = gate_mod.handle_payload(outside, mode="deny", gate_state=state)
        check(decision.verdict == "allow" and decision.rule == "no_corpus_search",
              "файловый поиск вне корпуса разрешён",
              f"{decision.verdict}/{decision.rule}")
        LOG.append(f"вне корпуса: {decision.verdict} ({decision.rule})")

        print("\n=== 8. Отчёт по задаче видит решения гейта ===")
        summary = state.summary(limit=50)
        accepted = [d for d in summary["recent"] if d.get("task") == session]
        check(len(accepted) >= 6, "решения записаны с меткой сеанса",
              str(len(accepted)))
        check(summary["counters"].get("deny", 0) >= 2, "отказы посчитаны",
              str(summary["counters"]))
        check(summary["write_failures"] == 0, "потерь записи нет",
              str(summary["write_failures"]))
        LOG.append(f"состояние гейта: {json.dumps(summary['counters'], ensure_ascii=False)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        # Реестр возвращается как был: временный корпус не должен в нём остаться.
        if catalog_backup is not None:
            CATALOG.write_bytes(catalog_backup)
        # Проверяем ИМЕННО запись временного корпуса в реестре, а не
        # `find_corpus(tmp)`: временный каталог лежит внутри рабочего, и
        # `find_corpus` законно вернёт корпус RAGRAF.
        root = str(tmp).replace("\\", "/").lower()
        leftover = [r["category"] for r in Catalog().summary()["entries"]
                    if str(r.get("root") or "").replace("\\", "/").lower().startswith(root)]
        check(not leftover, "временный корпус убран из реестра", str(leftover))

    print()
    print("=" * 78)
    print("ХОД ПРОГОНА")
    for line in LOG:
        print(f"  · {line}")
    print("=" * 78)
    print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
