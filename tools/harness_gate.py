"""Хук клиента: гейт использования индекса RAGRAF (§3.2).

Один и тот же файл обслуживает два клиента, потому что протокол хуков у них
общий (Claude Code и его мост в DSH):

    DeepSeek Harness — через `@deepseek-ai/dsh-hooks-claude-code`;
    Claude Code      — напрямую из `~/.claude/settings.json`.

Стадии (аргумент командной строки):

    harness_gate.py session-start   # SessionStart     → маркер сеанса + факты корпуса
    harness_gate.py prompt          # UserPromptSubmit → метка задачи сеанса
    harness_gate.py pre-tool        # PreToolUse       → allow / ask / deny
    harness_gate.py stop            # Stop             → строка отчёта по задаче
    harness_gate.py selftest        # проверка связки, состояние не меняет

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

ВЫВОД ТОЛЬКО БАЙТАМИ UTF-8. Клиент запускает хук с окружением, в котором
стандартный вывод оказался в cp1252; `print` русского текста падал
`UnicodeEncodeError`, а обработчик по правилу «не ломать сеанс» писал
«вызов разрешён» и возвращал 0 — то есть гейт молча превращался в разрешение
(измерено 02.11.2026). Поэтому JSON пишется прямо в `sys.stdout.buffer` в
UTF-8, а текст ответа кодируется с `ensure_ascii=True`: получается чистый
ASCII, который одинаково читается при любой кодировке канала.

РЕЖИМ ПО УМОЛЧАНИЮ БЕРЁТСЯ ИЗ ЯДРА. Здесь больше нет своей константы: прежняя
говорила `ask`, тогда как `src/ragraf/gate.py` объявляет `warn`, и текст
SessionStart расходился с фактическим поведением.

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


def _write(stream, text: str) -> None:
    """Записать текст в поток байтами UTF-8, минуя кодировку канала.

    `sys.stdout.buffer` есть у обычного процесса; если поток подменён (тесты,
    перенаправление в объект), остаётся обычный `write`.
    """
    data = text.encode("utf-8", "replace")
    buf = getattr(stream, "buffer", None)
    if buf is not None:
        buf.write(data)
        buf.flush()
        return
    stream.write(text)
    stream.flush()


def read_payload() -> dict:
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
    """Ответ хука: ASCII-безопасный JSON (русский текст — в \\u-последовательностях)."""
    _write(sys.stdout, json.dumps(payload, ensure_ascii=True))
    return 0


def effective_mode(gate_mod) -> str:
    """Режим, который реально применится: переменная окружения либо умолчание ядра."""
    mode = str(os.environ.get("RAGRAF_GATE_MODE") or gate_mod.DEFAULT_MODE).lower()
    return mode if mode in gate_mod.MODES else gate_mod.DEFAULT_MODE


def _corpus_facts(cwd: str) -> tuple[dict | None, str]:
    """Корпус каталога и одна строка о нём: имя, ревизия, свежесть.

    Зачем в хуке, а не только в инструменте. Клиент не сообщает агенту, какой
    корпус к нему подключён и свеж ли он, поэтому первый шаг задачи («привести
    индекс в актуальное состояние») начинался с догадки. Строка в контексте
    сеанса снимает догадку: имя корпуса, ревизия и вердикт свежести приходят
    ДО первого обращения.

    Свежесть проверяется дешёвым путём (`gate.corpus_freshness`: отпечаток
    входа), без пересборки — иначе старт сеанса ждал бы индексацию.
    """
    from ragraf import gate as gate_mod

    corpus = gate_mod.find_corpus(cwd)
    if not corpus:
        return None, ("RAGRAF: корпус для этого каталога не заведён — "
                      "`ragraf init <каталог>`, затем `ragraf ensure`.")
    fresh, detail = gate_mod.corpus_freshness(corpus)
    name = corpus.get("category") or corpus.get("project") or corpus.get("root") or ""
    rev = str(corpus.get("rev_id") or detail.get("rev_id")
              or detail.get("indexed_rev") or "")
    verdict = ("индекс свежий" if fresh is True
               else "индекс ОТСТАЛ — сначала `ensure`" if fresh is False
               else "свежесть не доказана — `ensure` перед работой")
    parts = [f"RAGRAF: корпус «{name}»"]
    if rev:
        parts.append(f"ревизия {rev}")
    parts.append(verdict)
    return corpus, ", ".join(parts) + "."


#: Замер 03.11.2026: полный `ensure` корпуса GIT (155 файлов, 94,5 с пересбора)
#: занял 192,68 с, а SessionStart получает от профиля 30 с. Поэтому здесь НЕ
#: запускается пересборка: хук обязан отвечать за доли секунды, иначе старт чата
#: ждёт индексацию. Актуализацию выполняет первый вызов агента — и он же
#: требуется гейтом: при отставшем индексе PreToolUse отклоняет файловый поиск
#: в любом режиме, кроме `warn`/`off`.
ACTUALIZE_HINT = (
    " RAGRAF: индекс отстал — первым вызовом `ensure` (`ragraf_ensure`) "
    "(он сам пересоберёт корпус; замер на GIT: до ~3 мин при полном пересборе), "
    "иначе гейт отклонит файловый поиск."
)


def _actualize_hint(corpus: dict | None, fresh: bool | None) -> str:
    """Строка о необходимости актуализации — без запуска пересборки.

    Почему без запуска. Полная пересборка измеренно длится минуты (см.
    ACTUALIZE_HINT), а SessionStart-хук ограничен 30 с; синхронный `ensure`
    здесь уже приводил к таймауту 25,2 с и оставлял корпус отставшим. Поэтому
    решение об актуализации передаётся агенту как обязательный первый вызов, а
    исполнение гарантирует гейт: файловый поиск при `stale` не проходит.
    """
    if corpus is None or fresh is True:
        return ""
    return ACTUALIZE_HINT


def session_start(payload: dict) -> int:
    from ragraf import gate as gate_mod

    state = gate_mod.state()
    sid = str(payload.get("session_id") or "")
    cwd = str(payload.get("cwd") or "")
    state.open_session(sid, cwd)
    mode = effective_mode(gate_mod)
    corpus, facts = _corpus_facts(cwd)
    fresh: bool | None = None
    if corpus is not None:
        fresh, _detail = gate_mod.corpus_freshness(corpus)
    actualize = _actualize_hint(corpus, fresh)
    if cwd:
        project_hint = f"`use_project` (`ragraf_use_project`) с project={cwd!r}"
    else:
        project_hint = "`use_project` (`ragraf_use_project`) с путём к каталогу проекта"
    text = (
        (facts + " " if facts else "")
        + actualize
        + f" Гейт использования индекса активен (режим {mode}). "
        "MCP-сервер стартует с корпусом RAGRAF, поэтому первым делом выберите корпус "
        f"проекта: {project_hint}, затем берите факты из индекса "
        "(`search_multi`, `symbol`, `impact`) — вместо файлового поиска."
    )
    # hookEventName — имя события протокола, а не наше: SessionStart.
    return emit({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                        "additionalContext": text}})


def pre_tool(payload: dict) -> int:
    from ragraf import gate as gate_mod

    decision = gate_mod.handle_payload(payload)
    out, code = decision.hook_response()
    if decision.verdict == "deny":
        # Код 2 — единственный код, который протокол считает блокировкой;
        # причина уходит модели из stderr.
        _write(sys.stderr, decision.stderr() + "\n")
        return 2
    if out:
        return emit(json.loads(out))
    # Вердикт без ответа (например, вызов вне правил) — молча разрешаем.
    return emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                        "permissionDecision": "allow"}})


def prompt(payload: dict) -> int:
    """UserPromptSubmit: назвать задачу сеанса меткой для статистики.

    Зачем. Клиент не передаёт метку задачи в долгоживущий MCP-процесс:
    переменная окружения задаётся при старте, а задача меняется в ходе сеанса.
    Поэтому метка НАЗЫВАЕТСЯ здесь, в состоянии гейта, а фасад читает её
    (`facade.py`: `current_task`/`active_task`) — иначе все обращения сеанса
    попадали бы в «(без задачи)» и вопрос «сколько работал индекс в этой
    задаче» ответа не имел.

    Правило: первый запрос сеанса задаёт метку, последующие её не меняют.
    Иначе каждый ход пользователя дробил бы одну задачу на десять, и отчёт по
    задаче показывал бы обрывки. Метка — начало запроса, сжатое до 60 символов.
    """
    from ragraf import gate as gate_mod

    cwd = str(payload.get("cwd") or "")
    sid = str(payload.get("session_id") or "")
    text_prompt = " ".join(str(payload.get("prompt") or "").split())
    if not text_prompt:
        return 0
    state = gate_mod.state()
    corpus = gate_mod.find_corpus(cwd)
    if not corpus:
        return 0
    root = str(corpus.get("root") or "")
    known = state.current_task(root)
    if known:
        return 0
    label = text_prompt[:60].strip()
    state.set_current_task(label, root=root, session_id=sid)
    state.open_session(sid, cwd, task=label)
    return emit({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": (f"RAGRAF: задача помечена «{label}» — обращения к индексу "
                              "будут учтены в ней."),
    }})


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
        from ragraf import gate as gate_mod

        # Метка берётся ещё и из состояния гейта: хук `prompt` называет задачу
        # там, а переменная окружения у клиента может быть не задана вовсе.
        task = (os.environ.get("RAGRAF_TASK")
                or gate_mod.active_task(str(payload.get("cwd") or "")))
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


def selftest() -> int:
    """Проверить связку, ничего не меняя: ядро импортируется, режим известен.

    Этим же вызовом установщик проверяет команду хука в PowerShell и cmd —
    без записи в состояние гейта и без побочных эффектов.
    """
    from ragraf import gate as gate_mod

    info = {
        "ok": True,
        "repo": str(REPO),
        "python": sys.version.split()[0],
        "stdout_encoding": (getattr(sys.stdout, "encoding", "") or ""),
        "mode": effective_mode(gate_mod),
        "modes": list(gate_mod.MODES),
        "stdin_encoding": (getattr(sys.stdin, "encoding", "") or ""),
    }
    _write(sys.stdout, json.dumps(info, ensure_ascii=True))
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    mode_arg = next((a.split("=", 1)[1] for a in argv[1:] if a.startswith("--mode=")), None)
    if mode_arg:
        os.environ["RAGRAF_GATE_MODE"] = mode_arg
    stage = (args[0] if args else "pre-tool").strip().lower()
    try:
        if stage == "selftest":
            return selftest()
        payload = read_payload()
        if stage in ("session-start", "sessionstart"):
            return session_start(payload)
        if stage in ("prompt", "user-prompt-submit"):
            return prompt(payload)
        if stage in ("stop",):
            return stop(payload)
        return pre_tool(payload)
    except Exception as exc:  # noqa: BLE001 — хук не имеет права ломать сеанс
        _write(sys.stderr, f"[RAGRAF] гейт не сработал ({type(exc).__name__}: {exc}); "
                           f"вызов разрешён\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
