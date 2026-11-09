"""Кейсы поведения и парный прогон: один формат, два потребителя.

Зачем отдельный модуль. Контур качества (`quality_loop.py`) измеряет ИНДЕКС:
размер чанков, обрезанные сигнатуры, вектор, честность режима. Он не видит
второго класса дефектов — «ответ верный, а получен обходным путём»: агент
поискал файлы через `Grep`, нашёл нужное и лишь потом (или вовсе не) позвал
индекс. Такое не измеряется числами индекса, потому что индекс тут ни при чём.

Что делает модуль:

  * читает кейсы из файла корпуса `quality-cases.yaml` (закрытая схема);
  * синтезирует кейсы из бенчмарка `bench/tasks.yaml` — машинно, без модели;
  * проверяет кейс по трём источникам: файлы ревизии, журнал вызовов
    (`usage_call`), решения гейта (журнал состояния гейта);
  * сравнивает два прогона ПО КЕЙСАМ (`pair`), не позволяя среднему скрыть
    сломанный кейс.

Три правила, без которых это было бы украшением:

  1. **Ничего не пишется.** Модуль только читает: ни таблиц, ни файлов. Второго
     хранилища фактов не заводится (C1 плана).
  2. **Пин ревизии — часть кейса.** Кейс, привязанный к `rev-A`, на прогоне
     `rev-B` НЕ «прошёл»: он `unknown` с названной причиной. Иначе парный прогон
     сравнивал бы разные вопросы и называл это регрессией.
  3. **`unknown` — не «хорошо».** Нет журнала вызовов, нет сеанса, нет пинa —
     состояние называется, а не превращается в зелёное.

Формат кейса (закрытые ключи, лишний ключ — отказ, а не «пропустим»):

```yaml
schema: 1
cases:
  - id: C-01
    kind: trace            # index | trace | bench
    expected: ["src/auth/validate.py"]
    required_steps: [ensure, search_multi]
    forbidden: [file_search, raw_read_of_code]
    rev: rev-0001          # может быть пустым: тогда пин не задан
    note: "почему кейс существует"
```

Автор: Mikhail Deynekin
Сайт: https://Deynekin.com
Почта: Mikhail@Deynekin.com
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: Имя файла кейсов в корне корпуса.
CASES_NAME = "quality-cases.yaml"

#: Имя файла бенчмарка (путь от корня корпуса).
BENCH_NAME = "bench/tasks.yaml"

#: Версия схемы файла кейсов. Другая — отказ с причиной.
SCHEMA = 1

#: Закрытые списки. Значение вне списка — отказ, а не молчаливое «наверное, ok».
CASE_KINDS = ("index", "trace", "bench")
CASE_STATUSES = ("ok", "fail", "unknown")
VERDICTS = ("better", "worse", "not_sure")

#: Закрытый список ключей кейса.
CASE_KEYS = ("id", "kind", "expected", "required_steps", "forbidden", "rev", "note")

#: Пределы. Полное число кейсов возвращается рядом с усечённым списком.
MAX_CASES = 500
MAX_LISTED = 200

#: Виды решений гейта, которые считаются ОБХОДОМ, если кейс их запретил.
BYPASS_VERDICTS = ("deny", "ask")

#: Признак пути в ожидании: имя файла с расширением и хотя бы одним слэшем.
_PATH_LIKE = re.compile(
    r"^[\w.\-]+(?:/[\w.\-]+)+\.(?:py|php|js|mjs|ts|cs|go|java|rb|rs|sql|md|yaml|yml|"
    r"json|toml|sh|ps1|html|css|vue|jsx|tsx)$")


class CaseRefusal(ValueError):
    """Файл кейсов не читается или не проходит схему. Ошибка входа, не сбоя."""


def _display_path(target: Path, root: str | Path) -> str:
    """Путь для ответа: относительно корпуса, а не машины разработчика.

    Абсолютный путь в измерении — это утечка раскладки диска в журнал и в
    сравнение прогонов: два прогона на одной и той же копии корпуса в разных
    каталогах дали бы разные ответы на один вопрос.
    """
    try:
        return Path(target).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(target).as_posix().replace(str(Path(root).as_posix()) + "/", "", 1)


def _as_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        raise CaseRefusal(f"{where}: ожидается список строк, получено "
                          f"{type(value).__name__}")
    out: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise CaseRefusal(f"{where}[{index}]: ожидается строка, получено "
                              f"{type(item).__name__}")
        text = item.strip()
        if text:
            out.append(text)
    return out


def is_path_like(value: str) -> bool:
    """Похоже ли ожидание на путь файла. Нужно, чтобы отделить проверяемое."""
    return bool(_PATH_LIKE.match(str(value or "").strip()))


def _case_lines(raw: str) -> dict[str, int]:
    """Строка объявления каждого кейса — адрес находки.

    Номер ищется по тексту (`- id: X`), а не через `yaml.compose`: кейсов
    сотни, а не тысячи, идентификаторы уникальны и проверены схемой, поэтому
    поиск подстроки здесь не ошибается — и на сто строк дешевле разметки YAML.
    """
    lines: dict[str, int] = {}
    pattern = re.compile(r"^\s*-\s*id\s*:\s*(\S+)\s*$")
    for number, line in enumerate(str(raw or "").splitlines(), start=1):
        match = pattern.match(line)
        if match:
            lines.setdefault(match.group(1).strip().strip("'\""), number)
    return lines


def normalize_case(item: Any, where: str, *, default_kind: str = "index") -> dict[str, Any]:
    """Привести один кейс к одной форме. Лишний ключ — отказ с адресом."""
    if not isinstance(item, dict):
        raise CaseRefusal(f"{where}: ожидается отображение, получено "
                          f"{type(item).__name__}")
    unknown_keys = [key for key in item if key not in CASE_KEYS]
    if unknown_keys:
        raise CaseRefusal(f"{where}: ключи вне закрытого списка: "
                          f"{', '.join(sorted(unknown_keys))}; допустимы "
                          f"{', '.join(CASE_KEYS)}")
    case_id = str(item.get("id") or "").strip()
    if not case_id:
        raise CaseRefusal(f"{where}: пустой id")
    kind = str(item.get("kind") or default_kind).strip().lower()
    if kind not in CASE_KINDS:
        raise CaseRefusal(f"{where}: kind={kind!r} вне списка {', '.join(CASE_KINDS)}")
    required = _as_list(item.get("required_steps"), f"{where}.required_steps")
    forbidden = _as_list(item.get("forbidden"), f"{where}.forbidden")
    overlap = sorted(set(required) & set(forbidden))
    if overlap:
        raise CaseRefusal(f"{where}: шаг одновременно обязателен и запрещён: "
                          f"{', '.join(overlap)}")
    return {
        "id": case_id,
        "kind": kind,
        "expected": _as_list(item.get("expected"), f"{where}.expected"),
        "required_steps": required,
        "forbidden": forbidden,
        "rev": str(item.get("rev") or "").strip(),
        "note": str(item.get("note") or "").strip(),
        "source": "cases",
    }


def load_cases(root: str | Path, path: str | Path | None = None) -> dict[str, Any]:
    """Прочитать файл кейсов корпуса.

    Ответ — всегда словарь: `ok`, `path`, `cases`, `reason`. Отсутствие файла —
    НЕ ошибка (кейсов может не быть), поэтому `ok=True`, `declared=0` и причина
    `no cases`. Ошибка схемы — `ok=False` с названной причиной и адресом: терять
    кейсы молча из-за опечатки значило бы «кейсов нет» вместо «файл сломан».
    """
    target = Path(path) if path else Path(root) / CASES_NAME
    display = _display_path(target, root)
    out: dict[str, Any] = {"ok": True, "path": display, "cases": [],
                           "declared": 0, "reason": "", "lines": {}}
    if not target.is_file():
        out["reason"] = "no cases"
        return out
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        out.update(ok=False, reason=f"файл кейсов не читается: {type(exc).__name__}")
        return out
    try:
        import yaml
    except ImportError:
        out.update(ok=False, reason="PyYAML не установлен: кейсы разобрать нечем")
        return out
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        out.update(ok=False, reason=f"файл кейсов не разбирается как YAML: {exc}")
        return out
    if data is None:
        data = {}
    if not isinstance(data, dict):
        out.update(ok=False, reason="верхний уровень файла кейсов должен быть "
                                    "отображением")
        return out
    schema = data.get("schema")
    if schema != SCHEMA:
        out.update(ok=False, reason=f"schema файла кейсов {schema!r} не "
                                    f"поддерживается: ожидается {SCHEMA}")
        return out
    raw_cases = data.get("cases")
    if raw_cases is None:
        raw_cases = []
    if not isinstance(raw_cases, list):
        out.update(ok=False, reason="cases: ожидается список")
        return out
    if len(raw_cases) > MAX_CASES:
        out.update(ok=False, reason=f"кейсов {len(raw_cases)} больше предела "
                                    f"{MAX_CASES}: файл объявлен вручную и столько "
                                    f"не читается человеком")
        return out
    lines = _case_lines(raw)
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_cases):
        try:
            case = normalize_case(item, f"cases[{index}]")
        except CaseRefusal as exc:
            out.update(ok=False, reason=str(exc))
            return out
        if case["id"] in seen:
            out.update(ok=False, reason=f"кейс {case['id']} объявлен дважды")
            return out
        seen.add(case["id"])
        case["line"] = int(lines.get(case["id"]) or 1)
        case["address"] = f"{display}:{case['line']}"
        cases.append(case)
    out["cases"] = cases
    out["declared"] = len(cases)
    return out


def load_bench(root: str | Path, path: str | Path | None = None) -> dict[str, Any]:
    """Кейсы из бенчмарка: ожидаемые пути становятся машинной проверкой.

    Чего это НЕ доказывает, названо прямо: проверяется наличие ожидаемого пути
    в ревизии индекса, а не то, что операция `search` его вернёт. Второе
    проверяется видом `trace` — он смотрит фактические вызовы сеанса.

    Задача без единого пути (`«метод-обработчик с указанием строки»`) получает
    состояние `unknown` с причиной `not_machine_checkable`: она не выброшена
    молча и не выдана за проверенную.
    """
    target = Path(path) if path else Path(root) / BENCH_NAME
    display = _display_path(target, root)
    out: dict[str, Any] = {"ok": True, "path": display, "cases": [],
                           "declared": 0, "reason": "", "not_machine_checkable": 0}
    if not target.is_file():
        out["reason"] = "no bench"
        return out
    try:
        import yaml

        data = yaml.safe_load(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — причина называется
        out.update(ok=False, reason=f"бенчмарк не читается: {type(exc).__name__}: {exc}")
        return out
    tasks = (data or {}).get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        out.update(ok=False, reason="bench: нет списка tasks")
        return out
    # Включение машинной проверки — ЯВНОЕ (`machine: true`), а не «раз файл есть,
    # то и проверяем». Причина измерена: `bench/tasks.yaml` самого RAGRAF
    # описывает чужой корпус (пути `src/Http/...`), и без этого ключа каждый
    # прогон на RAGRAF давал бы 14 красных кейсов про файлы, которых здесь не
    # должно быть. Ложная краснота хуже отсутствия проверки: к ней привыкают.
    if data.get("machine") is not True:
        out["reason"] = ("bench: машинные ожидания не объявлены (нет `machine: true`) — "
                         "задачи бенчмарка не сравниваются с этим корпусом")
        return out
    cases: list[dict[str, Any]] = []
    checkable = 0
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or f"T-{index + 1}").strip()
        expected = _as_list(task.get("expected"), f"bench[{index}].expected")
        paths = [item for item in expected if is_path_like(item)]
        if paths:
            checkable += 1
        cases.append({
            "id": f"BENCH-{task_id}",
            "kind": "bench",
            "expected": expected,
            "required_steps": [],
            "forbidden": [],
            "rev": "",
            "note": str(task.get("question") or "").strip(),
            "source": "bench",
            "line": 1,
            "address": display,
            "machine_checkable": bool(paths),
            "paths": paths,
        })
    out["cases"] = cases
    out["declared"] = len(cases)
    out["not_machine_checkable"] = len(cases) - checkable
    return out


# ---------------------------------------------------------------------------
# Проверка одного кейса
# ---------------------------------------------------------------------------


def _file_present(graph: Any, rev: str, path: str) -> bool:
    try:
        row = graph.execute(
            "SELECT 1 FROM file WHERE rev_id = ? AND path = ? LIMIT 1",
            (rev, path)).fetchone()
    except Exception:  # noqa: BLE001 — нет таблицы: значит и файла не доказать
        return False
    return row is not None


def _session_ops(control: Any, session: str) -> list[str]:
    try:
        rows = control.execute(
            "SELECT op FROM usage_call WHERE session_id = ? ORDER BY ts, id",
            (session,)).fetchall()
    except Exception:  # noqa: BLE001 — журнала нет: шагов не доказать
        return []
    return [str(row[0] if not isinstance(row, tuple) else row[0]) for row in rows]


def _gate_rows(gate_state: Any, session: str) -> list[dict[str, Any]]:
    """Решения гейта этого сеанса: вид вызова, вердикт, причина.

    Читается СУЩЕСТВУЮЩИЙ журнал состояния гейта (`gate.GateState.decisions`),
    а не заводится вторая запись решений: два журнала одного события расходятся
    ровно так же, как любые две копии.
    """
    if gate_state is None:
        return []
    try:
        rows = gate_state.decisions(session_id=session)
    except Exception:  # noqa: BLE001 — состояния нет: обхода не доказать
        return []
    return [dict(item) for item in rows if isinstance(item, dict)]


def session_ops(control: Any, session: str) -> list[str]:
    """Шаги сеанса — для трассы сбоя. Обёртка над тем же чтением журнала."""
    return _session_ops(control, session)


def gate_decisions(gate_state: Any, session: str) -> list[dict[str, Any]]:
    """Решения гейта сеанса — для трассы сбоя (тот же журнал, второй копии нет)."""
    return _gate_rows(gate_state, session)


def evaluate_one(case: dict[str, Any], *, graph: Any, control: Any, rev: str,
                 session: str, gate_state: Any) -> dict[str, Any]:
    """Проверить один кейс. Ответ всегда несёт `status` и `address`."""
    result: dict[str, Any] = {
        "id": case["id"],
        "kind": case["kind"],
        "status": "unknown",
        "address": case.get("address") or "",
        "expected": list(case.get("expected") or []),
        "required_steps": list(case.get("required_steps") or []),
        "forbidden": list(case.get("forbidden") or []),
        "missing_steps": [],
        "violations": [],
        "missing_paths": [],
        "reason": "",
    }
    pinned = str(case.get("rev") or "")
    if pinned and pinned != rev:
        result["reason"] = (f"кейс привязан к {pinned}, прогон на {rev}: "
                            f"сравнивать нечего — это разные вопросы")
        return result

    if case["kind"] in ("index", "bench"):
        paths = list(case.get("paths") or []) or [item for item in
                                                  case.get("expected") or []
                                                  if is_path_like(item)]
        if not paths:
            result["reason"] = ("в ожиданиях нет ни одного пути: проверять нечем "
                                "(not_machine_checkable)")
            return result
        missing = [path for path in paths if not _file_present(graph, rev, path)]
        result["missing_paths"] = missing
        if missing:
            result["status"] = "fail"
            result["reason"] = (f"ожидаемых путей нет в ревизии: {', '.join(missing)}")
        else:
            result["status"] = "ok"
            result["reason"] = f"все ожидаемые пути есть в {rev}: {len(paths)}"
        return result

    # trace: шаги сеанса и решения гейта
    if not session:
        result["reason"] = ("сеанс не назван: шаги взять неоткуда — передайте "
                            "session или запустите после работы агента")
        return result
    if case["kind"] == "trace":
        ops = _session_ops(control, session)
        if not ops:
            result["reason"] = (f"в журнале нет вызовов сеанса {session}: "
                                f"трассу восстановить нечем")
            return result
        missing = [step for step in case["required_steps"] if step not in ops]
        hits = [step for step in case["forbidden"] if step in ops]
        bypass = []
        for row in _gate_rows(gate_state, session):
            kind = str(row.get("kind") or "")
            verdict = str(row.get("verdict") or "")
            if kind and kind in case["forbidden"] and verdict in BYPASS_VERDICTS:
                bypass.append({"kind": kind, "verdict": verdict,
                               "rule": str(row.get("rule") or ""),
                               "reason": str(row.get("reason") or "")[:200]})
        result["missing_steps"] = missing
        result["violations"] = hits
        result["bypass"] = bypass[:MAX_LISTED]
        if missing or hits or bypass:
            result["status"] = "fail"
            parts = []
            if missing:
                parts.append("нет обязательных шагов: " + ", ".join(missing))
            if hits:
                parts.append("запрещённые шаги в журнале: " + ", ".join(hits))
            if bypass:
                parts.append("обход подтверждён решением гейта: "
                             + ", ".join(sorted({str(item['kind']) for item in bypass})))
            result["reason"] = "; ".join(parts)
        else:
            result["status"] = "ok"
            result["reason"] = (f"шаги на месте ({len(ops)} вызовов), запрещённых "
                                f"шагов и обхода нет")
        return result

    result["reason"] = f"вид кейса {case['kind']!r} не проверяется"
    return result


def evaluate(cases: list[dict[str, Any]], *, graph: Any, control: Any, rev: str,
             session: str = "", gate_state: Any = None) -> dict[str, Any]:
    """Проверить набор кейсов. Порядок результата — порядок объявления."""
    results = [evaluate_one(case, graph=graph, control=control, rev=rev,
                            session=session, gate_state=gate_state)
               for case in cases]
    totals = {status: 0 for status in CASE_STATUSES}
    for item in results:
        totals[item["status"]] = totals.get(item["status"], 0) + 1
    return {
        "declared": len(cases),
        "session": session,
        "rev": rev,
        "totals": totals,
        "cases": results[:MAX_LISTED],
        "shown": min(len(results), MAX_LISTED),
    }


# ---------------------------------------------------------------------------
# Парный прогон
# ---------------------------------------------------------------------------


def _index_of(block: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in block.get("cases") or []:
        if isinstance(item, dict) and item.get("id"):
            out[str(item["id"])] = item
    return out


def pair(before: dict[str, Any], after: dict[str, Any], *,
         from_run: dict[str, Any], to_run: dict[str, Any]) -> dict[str, Any]:
    """Сравнить два прогона ПО КЕЙСАМ.

    Почему отдельно от `quality_diff`. `quality_diff` сравнивает показатели
    индекса и НЕ выносит вердикт между разными ревизиями: правка кода законно
    меняет числа. Здесь вопрос другой — «тот же набор вопросов стал отвечаться
    хуже или лучше», и ревизии называются в ответе, а не прячутся.

    Блокирующее правило: если хотя бы один ранее проходивший кейс упал, общий
    вердикт НЕ может быть «не хуже». Среднее по кейсам скрыло бы именно тот
    случай, ради которого затевался парный прогон.
    """
    old = _index_of(before or {})
    new = _index_of(after or {})
    if not old and not new:
        raise CaseRefusal("в обоих прогонах нет блока кейсов: сравнивать нечего — "
                          "прогоны сделаны без файла кейсов")
    ids = sorted(set(old) | set(new))
    rows: list[dict[str, Any]] = []
    regressions: list[str] = []
    improvements: list[str] = []
    for case_id in ids:
        prev = old.get(case_id) or {}
        curr = new.get(case_id) or {}
        before_status = str(prev.get("status") or "unknown")
        after_status = str(curr.get("status") or "unknown")
        if before_status == "ok" and after_status == "fail":
            verdict = "worse"
            regressions.append(case_id)
        elif before_status == "fail" and after_status == "ok":
            verdict = "better"
            improvements.append(case_id)
        else:
            verdict = "not_sure"
        rows.append({
            "id": case_id,
            "kind": str(curr.get("kind") or prev.get("kind") or ""),
            "before": before_status,
            "after": after_status,
            "verdict": verdict,
            "address": str(curr.get("address") or prev.get("address") or ""),
            "reason": str(curr.get("reason") or prev.get("reason") or ""),
        })
    rev_before = str(from_run.get("rev") or "")
    rev_after = str(to_run.get("rev") or "")
    digest_before = str((from_run.get("suite") or {}).get("digest") or "")
    digest_after = str((to_run.get("suite") or {}).get("digest") or "")
    same_state = (rev_before == rev_after and digest_before == digest_after
                  and bool(digest_before))
    if same_state:
        for row in rows:
            row["verdict"] = "not_sure"
        verdict = "not_sure"
        reason = ("прогоны на одном состоянии (ревизия и отпечаток совпадают): "
                  "изменений нет, и «улучшение» здесь было бы выдумкой")
    elif regressions:
        verdict = "worse"
        reason = ("регрессия: ранее проходившие кейсы упали — "
                  + ", ".join(regressions))
    elif improvements:
        verdict = "better"
        reason = "кейсы, падавшие раньше, теперь проходят: " + ", ".join(improvements)
    else:
        verdict = "not_sure"
        reason = ("изменений в кейсах нет: прогоны отличаются только тем, что не "
                  "входит в кейсы")
    return {
        "pair_basis": "fixed_cases",
        "from_run": int(from_run.get("id") or 0),
        "to_run": int(to_run.get("id") or 0),
        "from_rev": rev_before,
        "to_rev": rev_after,
        "from_digest": digest_before,
        "to_digest": digest_after,
        "same_state": same_state,
        "verdict": verdict,
        "reason": reason,
        "regressions": regressions,
        "improvements": improvements,
        "cases": rows[:MAX_LISTED],
        "shown": min(len(rows), MAX_LISTED),
        "total": len(rows),
        "differs_from_quality_diff": (
            "quality_diff сравнивает показатели индекса и не выносит вердикт между "
            "ревизиями; здесь сравниваются фиксированные кейсы, ревизии названы"),
    }
