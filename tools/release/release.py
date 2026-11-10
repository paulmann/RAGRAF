#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Одна команда выпуска: сборка → ворота → подтверждение пинов → отчёт.

Зачем. Выпуск состоял из трёх ручных шагов в разных местах: поднять версию,
подтвердить пины (`query contract_pin_approve --kind all`) и запустить
`gate_release.py`. Пропуск любого виден только по отказу гейта, а подтверждение
вообще не было связано с релизом: пин мог описывать набор, который никогда не
уезжал, или не описывать тот, который уехал.

Порядок жёсткий и названный:

  1. сборка (`build_release.py`) — не собралось, дальше не идём;
  2. ворота (`gate_release.py`) — отказ означает, что выпуска нет;
  3. подтверждение пинов — только после двух предыдущих шагов, с `note` =
     версия + причина; каждая смена пишет строку в `pin_history`;
  4. отчёт: версия, оба состояния, возраст пина и дата последней смены, вердикт
     сводки с прошлым выпуском, разрешённые поломки, путь к описаниям.

Почему подтверждение последнее, а не сразу после сборки: пин — обещание, что
такой набор принят. Записать обещание для выпуска, который не прошёл ворота,
значило бы врать в журнале смен.

Автоматического подтверждения нет: без `--approved-by` команда отказывает, имя
не берётся ни из окружения, ни из git — иначе «принял человек» перестало бы
что-либо значить.

Запуск:
    python tools/release/release.py --out D:\\rel --approved-by "Михаил"
    python tools/release/release.py --out D:\\rel --approved-by "Михаил" \\
        --previous D:\\rel-0.10.0\\ops_pins.json --note "выпуск 0.11"

Коды возврата: 0 — собрано, проверено и подтверждено; 1 — отказ сборки или
ворот (пины не тронуты); 2 — ошибка аргументов.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Тот же приём, что у соседних инструментов: каталог скрипта не попадает в
# sys.path у вендоренного Python, поэтому путь добавляется явно.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_release as builder  # noqa: E402 - путь к модулю добавлен выше
import gate_release as gate  # noqa: E402 - тот же каталог
import release_report as report_mod  # noqa: E402 - общее состояние и отчёт (X21)

PY = sys.executable


def gate_markdown(state: dict) -> str:
    """Тот же markdown, что пишут ворота (единственный источник представления)."""
    return gate.summary_markdown(state)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="release.py", description="Выпуск RAGRAF одной командой.")
    parser.add_argument("--out", required=True, help="каталог собранной поставки")
    # Не `required=True`: отказ должен называть причину словами, а не печатать
    # справку argparse; код возврата тот же — 2.
    parser.add_argument("--approved-by", dest="approved_by", default="",
                        help="кто подтверждает контракт (имя человека; обязательно)")
    parser.add_argument("--source", default=None, help="корень живого дерева")
    parser.add_argument("--manifest", default=None, help="манифест поставки")
    parser.add_argument("--previous", default=None,
                        help="манифест описаний прошлого выпуска (ops_pins.json)")
    parser.add_argument("--note", default="", help="пометка к пину (почему выпуск)")
    parser.add_argument("--report", default="",
                        help="записать итог выпуска в markdown (тот же, что у ворот)")
    parser.add_argument("--allow-breaking", dest="allow_breaking", default="",
                        help="имена объектов, чьё ломающее изменение разрешено (через запятую)")
    parser.add_argument("--break-reason", dest="break_reason", default="",
                        help="почему ломающее изменение допущено (обязательно при --allow-breaking)")
    return parser.parse_args(argv)


def _run(script: str, *args: str) -> tuple[int, str]:
    command = [PY, str(Path(__file__).resolve().parent / script), *args]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def build(out: Path, source: Path, manifest: Path | None,
          previous: str | None = None) -> tuple[int, str]:
    """Шаг 1: собрать поставку в `out`.

    Прошлый выпуск передаётся сборщику: отчёт в поставке должен знать, что
    изменилось (X21), иначе раздел «изменения» исчез бы из поставки молча.
    """
    args = ["--out", str(out), "--source", str(source)]
    if manifest is not None:
        args += ["--manifest", str(manifest)]
    if previous:
        args += ["--previous", str(previous)]
    return _run("build_release.py", *args)


def gate_check(source: Path, previous: str | None, allowed: tuple[str, ...],
               break_reason: str, manifest: Path | None) -> tuple[int, str]:
    """Шаг 2: ворота целиком — числа, реестр, переводы, пины, сборка, порчи."""
    args = ["--source", str(source)]
    if manifest is not None:
        args += ["--manifest", str(manifest)]
    if previous:
        args += ["--previous", str(previous)]
    if allowed:
        args += ["--allow-breaking", ",".join(allowed), "--break-reason", str(break_reason)]
    return _run("gate_release.py", *args)


def approve_pins(source: Path, *, by: str, note: str) -> tuple[int, str]:
    """Шаг 3: подтвердить оба набора от имени человека.

    Своё короткое соединение на запись: журнал корпуса — единственное место,
    куда пишется обещание, и второго файла состояния не заводится.
    """
    src = str(builder.package_src(source).resolve())
    if src not in sys.path:
        sys.path.insert(0, src)
    from ragraf import pin as pin_mod

    database = builder.control_db(source)
    if database is None:
        return 1, (f"журнала корпуса нет ({source}): подтверждать некуда — "
                   f"сначала проиндексируйте корпус")
    import sqlite3

    conn = sqlite3.connect(str(database))
    try:
        data = pin_mod.approve(conn, source, everything=True, by=str(by),
                               note=str(note), kind="all")
        change = pin_mod.last_change(conn)
    except pin_mod.PinRefusal as exc:
        return 1, f"подтверждение отклонено: {exc}"
    except Exception as exc:  # noqa: BLE001 — причина называется в отчёте
        return 1, f"подтверждение не выполнено: {type(exc).__name__}: {exc}"
    finally:
        conn.close()
    tail = ""
    if change:
        tail = (f"; последняя смена: {change['op']} ({change['kind']}), "
                f"было {(change['previous_hash'] or 'пина не было')[:12]}")
    return 0, f"подтверждено объектов: {data.get('total')}{tail}"


def _document(out: Path) -> dict:
    path = out / builder.PINS_ARTIFACT
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def pin_facts(source: Path) -> dict:
    """Возраст пина и дата последней смены — два разных ответа.

    Возраст отвечает «как давно принято», дата смены — «когда набор менялся
    последний раз». В сводке одного числа недостаточно: набор можно принять
    сегодня, а сменить год назад.
    """
    src = str(builder.package_src(source).resolve())
    if src not in sys.path:
        sys.path.insert(0, src)
    from ragraf import pin as pin_mod

    database = builder.control_db(source)
    if database is None:
        return {"age_seconds": None, "changed_at": "", "by": "", "op": ""}
    import sqlite3

    conn = sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True)
    try:
        change = pin_mod.last_change(conn)
    except Exception:  # noqa: BLE001 — отчёт не имеет права падать
        change = None
    finally:
        conn.close()
    if not change:
        return {"age_seconds": None, "changed_at": "", "by": "", "op": ""}
    return {"age_seconds": change["age_seconds"], "changed_at": change["approved_at"],
            "by": change["approved_by"], "op": change["op"]}


def sample_facts(source: Path) -> dict:
    """Состояние проб корпуса — для строки отчёта выпуска.

    Отдельно от пина: «описания приняты» и «поведение сверено» — разные ответы,
    и склеивать их в одно слово значило бы обещать больше, чем проверено.
    """
    src = str(builder.package_src(source).resolve())
    if src not in sys.path:
        sys.path.insert(0, src)
    from ragraf import pin as pin_mod

    database = builder.control_db(source)
    if database is None:
        return {"total": 0, "checked": 0, "changed": 0, "state": "none",
                "degraded": 0, "not_sampled": 0}
    import sqlite3

    conn = sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True)
    try:
        state = pin_mod.sample_state(conn)
        rows = conn.execute("SELECT status, COUNT(*) FROM pin_sample GROUP BY status").fetchall()
    except Exception:  # noqa: BLE001 — отчёт не имеет права падать
        return {"total": 0, "checked": 0, "changed": 0, "state": "none",
                "degraded": 0, "not_sampled": 0}
    finally:
        conn.close()
    counted = {str(row[0] or ""): int(row[1]) for row in rows}
    return {"total": int(state.get("total") or 0),
            "checked": int(state.get("checked") or 0),
            "changed": int(state.get("changed") or 0),
            "state": str(state.get("state") or "none"),
            "degraded": counted.get("sampled_degraded", 0),
            "not_sampled": counted.get("not_sampled", 0)}


def release_state(source: Path, out: Path, *, previous: str | None) -> tuple[dict, str]:
    """Состояние выпуска для отчёта — ТО ЖЕ, что у ворот и у поставки.

    Одна сборка состояния на три потребителя (сборщик, ворота, отчёт выпуска):
    пока их было две, тексты расходились формулировками. Источник состояния —
    `ops_pins.json` в поставке, а не переменные прогона: отчёт обязан описывать
    то, что уезжает клиенту.
    """
    state = report_mod.tree_state(out, previous=Path(previous).resolve() if previous else None,
                                  source=source)
    return state, ""


def report(source: Path, out: Path, *, by: str, note: str, allowed: tuple[str, ...],
           break_reason: str, gate_message: str, approve_message: str,
           facts: dict | None = None, samples: dict | None = None,
           samples_note: str = "") -> str:
    """Шаг 4: отчёт выпуска. Печатается целиком, а не «готово»."""
    document = _document(out)
    state = str(document.get("pin_state") or "(не названо)")
    ops_state = str(document.get("pin_state_ops") or "?")
    tools_state = str(document.get("pin_state_tools") or "?")
    if facts is None:
        facts = pin_facts(source)
    if samples is None:
        samples = sample_facts(source)
    age = gate.age_text(facts.get("age_seconds"))
    if not facts.get("changed_at"):
        changed = "смен набора не было (подтверждений нет)"
    else:
        changed = (f"последняя смена набора: {facts.get('changed_at')} "
                   f"({facts.get('op')}, {facts.get('by')})")
    probes = int(samples.get("total") or 0)
    if not probes:
        behaviour_line = "поведение: проб нет — поведение не проверено"
    elif int(samples.get("changed") or 0):
        behaviour_line = (f"поведение: изменилось у {samples['changed']} из {probes} "
                          f"(форма могла не меняться)")
    elif not int(samples.get("checked") or 0):
        behaviour_line = f"поведение: {probes} проб, ни одна не сверялась"
    else:
        tail = []
        if int(samples.get("degraded") or 0):
            tail.append(f"деградированных {samples['degraded']}")
        if int(samples.get("not_sampled") or 0):
            tail.append(f"не снято {samples['not_sampled']}")
        behaviour_line = (f"поведение: сверено {samples['checked']} из {probes}"
                          + (f" ({', '.join(tail)})" if tail else ""))
    lines = [
        f"выпуск: версия {builder.version_of(source)} → {out}",
        f"контракт: операции {ops_state}, инструменты {tools_state} (сводно {state})",
        behaviour_line,
        f"пин: {age}",
        changed,
    ]
    if samples_note:
        lines.append(f"с пробы прошлого выпуска: {samples_note}")
    lines.extend([
        f"описания: {out / builder.PINS_ARTIFACT}",
        f"подтвердил: {by}" + (f" (пометка: {note})" if note else ""),
        f"сводка с прошлым: {gate_message}",
    ])
    if allowed:
        lines.append(f"разрешено человеком: {', '.join(allowed)} — причина: {break_reason}")
    lines.append(f"подтверждение: {approve_message}")
    return "\n".join(lines)


def builder_age(source: Path) -> object:
    """Возраст последней смены набора (совместимое имя для проб)."""
    return pin_facts(source).get("age_seconds")


def run_release(*, out: Path, source: Path, approved_by: str, previous: str | None = None,
                note: str = "", allowed: tuple[str, ...] = (), break_reason: str = "",
                manifest: Path | None = None, report_path: str = "", build=build,
                gate=gate_check, approve=approve_pins) -> tuple[int, str]:
    """Весь выпуск по порядку. Шаги можно подставить — так они и проверяются."""
    if not str(approved_by).strip():
        return 2, ("не назван подтверждающий: --approved-by обязателен, "
                   "имя не берётся из окружения или git")
    if allowed and not str(break_reason).strip():
        return 2, "--allow-breaking требует --break-reason: разрешение без причины не принимается"

    # Прошлый выпуск передаётся только когда он есть: протокол шага остаётся
    # трёхаргументным, чтобы подставленные шаги в проверках не переписывались.
    code, output = (build(out, source, manifest, previous=previous) if previous
                    else build(out, source, manifest))
    if code != 0:
        return 1, (f"сборка не состоялась (код {code}), пины не тронуты:\n"
                   f"{output.strip()[:600]}")

    gate_code, gate_output = gate(source, previous, allowed, break_reason, manifest)
    if gate_code != 0:
        return 1, (f"ворота не пропустили выпуск (код {gate_code}), пины не тронуты:\n"
                   f"{gate_output.strip()[:600]}")
    gate_message = ""
    for line in reversed(gate_output.strip().splitlines()):
        if line.startswith(("OK: сводка", "WARN: --previous", "OK: --previous")):
            gate_message = line
            break
    if not gate_message:
        gate_message = "сводка не запрашивалась" if not previous else "(строка сводки не найдена)"

    # Пометка к пину связывает набор с версией: по истории видно, какой выпуск
    # приняли и почему.
    version = builder.version_of(source) or "(версия не названа)"
    stamp = f"{version}: {note}" if str(note).strip() else version
    approve_code, approve_message = approve(source, by=str(approved_by), note=stamp)
    if approve_code != 0:
        return 1, f"пины не подтверждены: {approve_message}"

    # Отчёт для человека пишется из ТОГО ЖЕ состояния, что и вердикт ворот:
    # иначе два текста об одном выпуске расходятся формулировками (X20).
    state, samples_note = release_state(source, out, previous=previous)
    if not samples_note:
        samples_note = "прошлый выпуск не передан — сравнивать не с чем"
    markdown = gate_markdown(state)
    if report_path:
        target = Path(report_path).expanduser()
        if target.parent and str(target.parent):
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8", newline="\n")
    message = report(source, out, by=str(approved_by), note=str(note), allowed=allowed,
                     break_reason=str(break_reason), gate_message=gate_message,
                     approve_message=approve_message, samples_note=samples_note)
    if report_path:
        message += f"\nотчёт записан: {Path(report_path).name}"
    return 0, message


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    source = Path(args.source).resolve() if args.source else builder.rel.default_source()
    out = Path(args.out).expanduser().resolve()
    manifest = Path(args.manifest).resolve() if args.manifest else None
    allowed = tuple(item.strip() for item in str(args.allow_breaking or "").split(",")
                    if item.strip())
    code, message = run_release(out=out, source=source, approved_by=str(args.approved_by),
                                previous=args.previous, note=str(args.note),
                                allowed=allowed, break_reason=str(args.break_reason),
                                manifest=manifest,
                                report_path=str(getattr(args, "report", "") or ""))
    print(("OK:\n" if code == 0 else "FAIL: ") + message)
    return code


if __name__ == "__main__":
    sys.exit(main())
