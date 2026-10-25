"""Гейт использования индекса: правила, состояние сеанса, журнал решений (§3.2).

Зачем модуль. Правило «пользуйся RAGRAF» работой не стало: замер на корпусе BWO
дал 7 обращений за сессию, из них 0 в ходе самой задачи по коду, а в документах
корпуса z четыре отчёта подряд фиксируют «инструментов `ragraf_*` в сеансе нет —
работал через CLI». Гейт ставит на этом месте проверку: он смотрит вызов
инструмента ДО выполнения и решает `allow` / `ask` / `deny` — по фактам, а не по
догадке о намерениях.

Где он включается. Штатным мостом хуков DSH
(`@deepseek-ai/dsh-hooks-claude-code`, событие `PreToolUse`): мост передаёт
`tool_name`, `tool_input`, `cwd`, `session_id` и читает решение из JSON
(`hookSpecificOutput.permissionDecision`) либо блокирует вызов кодом возврата 2 с
причиной в stderr. Значит гейт не требует правки ядра DSH — он подключается как
хук, владельцем которого является RAGRAF.

Чего гейт НЕ делает (границы механизма, а не оговорки):
  * не переписывает команду: `updatedInput` в протоколе разбирается, но не
    применяется, поэтому замена только предлагается текстом;
  * не может заставить агента переделать шаг: отказ блокирует КОНКРЕТНЫЙ вызов;
  * не читает историю сеанса: `transcript_path` всегда пуст, поэтому факт
    «индексом пользовались» берётся из журнала RAGRAF, а не из переписки;
  * не переиндексирует корпус: проверка свежести дешёвая и без пересборки —
    иначе гейт работал бы минутами на каждом вызове.

Решения принимаются по трём фактам: есть ли корпус для каталога вызова, свеж ли
он и пользовались ли индексом в окне сеанса.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Режим гейта: `warn` (только подсказка), `ask` (подтверждение), `deny`
#: (отказ), `off` (выключен).
#:
#: ПОЧЕМУ ПО УМОЛЧАНИЮ `warn`. В клиенте с включёнными подтверждениями `ask`
#: означает вопрос человеку, а в клиенте, где подтверждения отключены политикой,
#: тот же `ask` автоматически отклоняется — то есть молча превращается в `deny`.
#: Замер 14.10.2026: DeepSeek Harness в этой установке сообщает «approval prompts
#: are disabled: actions that require approval are rejected automatically».
#: Ставить в таком клиенте `ask` по умолчанию значило бы блокировать файловый
#: поиск без явного решения владельца. Поэтому по умолчанию — подсказка, а
#: строгие режимы включаются явно: `RAGRAF_GATE_MODE=ask|deny`.
DEFAULT_MODE = "warn"
MODES = ("warn", "ask", "deny", "off")

#: Окно, в котором вызов индекса считается «в этом сеансе». Пятнадцать минут —
#: компромисс: задача по коду длиннее, но и «пользовались час назад» уже не
#: оправдание для файлового поиска.
INDEX_USE_WINDOW_SEC = 900

#: Сколько живёт запомненный ответ о свежести корпуса. Дешёвая сверка по
#: отпечатку отвечает «свежо» только когда вход точно не менялся; когда отпечаток
#: разошёлся (обычное состояние во время работы над кодом), честный ответ даёт
#: полная сверка — а её нельзя запускать на каждом вызове инструмента. Поэтому
#: результат запоминается: одна полная сверка в минуту вместо одной на вызов.
GATE_FRESH_TTL_SEC = 60

#: Признаки файлового поиска в командной строке. Проверяются по тексту команды,
#: а не по имени инструмента: тот же `pwsh` может запускать сборку (это законно),
#: и запрещать инструмент целиком нельзя.
SEARCH_PATTERNS = (
    r"Select-String",
    r"\brg\b",
    r"\bfindstr\b",
    r"\bgrep\b",
    r"Get-ChildItem[^\n]*(-Recurse|-Filter)",
    r"ls\s+-R",
    r"find\s+\.\s+-name",
)

#: Расширения кода: по ним `read` считается чтением кода, а не конфигурации.
CODE_EXTS = {
    ".php", ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".java", ".cs", ".c",
    ".h", ".cpp", ".hpp", ".rb", ".lua", ".go", ".pl", ".pm", ".t", ".ps1",
    ".psm1", ".sh", ".bash", ".bat", ".cmd", ".css", ".html", ".htm", ".sql",
}

#: Какие операции индекса считаются «обращением к индексу».
INDEX_TOOLS = ("ragraf_", "mcp__ragraf__")

SEARCH_RE = re.compile("|".join(SEARCH_PATTERNS), re.IGNORECASE)


# ---------------------------------------------------------------------------
# Разбор вызова инструмента
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> dict:
    """`tool_input` приходит и словарём, и строкой JSON — приводим к словарю."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"raw": value}
        except Exception:
            return {"raw": value}
    return {}


def classify_call(tool_name: str, tool_input: Any) -> dict:
    """Что это за вызов: обращение к индексу, файловый поиск, чтение кода, иное.

    Возврат: `{"kind", "command", "path", "is_code"}`. Классификация — по
    аргументам, а не по имени инструмента: `pwsh` с `git status` и `pwsh` с
    `Select-String` — разные вещи, и запрет первого был бы ложным.
    """
    name = str(tool_name or "")
    payload = _as_dict(tool_input)
    command = str(payload.get("command") or payload.get("cmd") or payload.get("raw") or "")
    path = str(payload.get("file_path") or payload.get("path") or payload.get("file")
               or payload.get("notebook_path") or "")

    if any(name.startswith(prefix) for prefix in INDEX_TOOLS) or name in INDEX_TOOLS:
        return {"kind": "index", "command": command, "path": path, "is_code": False}

    # Инструмент поиска по содержимому — тот же файловый поиск, что `grep` в
    # оболочке: у агента он есть как отдельный инструмент (`grep`), и без этой
    # ветви гейт его не видел бы вовсе.
    if name.lower() in ("grep", "search_files", "ripgrep"):
        pattern = str(payload.get("pattern") or payload.get("query") or "")
        where = str(payload.get("path") or payload.get("glob") or "")
        return {"kind": "file_search", "command": f"{name} {pattern} {where}".strip(),
                "path": where, "is_code": False, "match": name}

    search = SEARCH_RE.search(command)
    if search:
        return {"kind": "file_search", "command": command, "path": path,
                "is_code": False, "match": search.group(0)}

    if name.lower() in ("read", "read_file", "view", "open") and path:
        is_code = Path(path).suffix.lower() in CODE_EXTS
        return {"kind": "read", "command": command, "path": path, "is_code": is_code}

    if command and SEARCH_RE.search(command):
        return {"kind": "file_search", "command": command, "path": path, "is_code": False}

    return {"kind": "other", "command": command, "path": path, "is_code": False}


# ---------------------------------------------------------------------------
# Решение
# ---------------------------------------------------------------------------


@dataclass
class GateDecision:
    """Решение гейта: вердикт, правило, причина и то, что видит модель."""

    verdict: str                  # allow | ask | deny | off
    rule: str                     # короткое имя правила
    reason: str                   # почему так (уходит модели)
    context: str = ""             # что сделать вместо (доп. контекст)
    found: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "rule": self.rule,
            "reason": self.reason,
            "context": self.context,
            "facts": self.found,
        }

    def hook_response(self) -> tuple[str, int]:
        """Ответ мосту хуков: (текст в stdout, код возврата).

        `deny` отдаётся кодом 2 с причиной в stderr — ровно этот код протокол
        считает блокировкой (`BLOCKING_EXIT_CODE`); любой другой ненулевой код
        был бы «не блокирующей ошибкой», то есть отказ не сработал бы.
        `allow` и `ask` отдаются JSON-формой `hookSpecificOutput`.
        """
        if self.verdict == "deny":
            return "", 2
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow" if self.verdict in ("allow", "off") else "ask",
            }
        }
        if self.verdict == "ask":
            payload["hookSpecificOutput"]["permissionDecisionReason"] = self.reason
        text = self.context or self.reason
        if text:
            payload["hookSpecificOutput"]["additionalContext"] = text
        return json.dumps(payload, ensure_ascii=False), 0

    def stderr(self) -> str:
        if self.verdict != "deny":
            return ""
        return f"[RAGRAF] {self.reason}" + (f" {self.context}" if self.context else "")


def decide(facts: dict, *, mode: str = DEFAULT_MODE) -> GateDecision:
    """Правила гейта. Все входы — уже собранные факты, без обращения к диску.

    `facts`: `kind` (классификация вызова), `corpus` (имя корпуса или None),
    `corpus_root`, `fresh` (True/False/None — неизвестно), `stale_paths`,
    `index_used` (были ли вызовы индекса в окне сеанса), `mode`, `first_offence`.

    Разделение фактов и правил нужно для проверки: правила проверяются на
    синтетических наборах без корпуса, DSH и живого индекса.
    """
    mode = str(mode or DEFAULT_MODE).lower()
    if mode not in MODES:
        mode = DEFAULT_MODE
    kind = str(facts.get("kind") or "other")
    corpus = facts.get("corpus")
    fresh = facts.get("fresh")
    index_used = bool(facts.get("index_used"))
    first = bool(facts.get("first_offence", True))
    found = {
        "kind": kind, "corpus": corpus, "fresh": fresh,
        "index_used": index_used, "mode": mode,
    }
    if mode == "off":
        return GateDecision("off", "gate_off", "гейт выключен", "", found)
    if kind == "index":
        return GateDecision(
            "allow", "index_call", "обращение к индексу разрешено",
            "RAGRAF: вызов индекса отмечен в сеансе", found,
        )
    if kind == "other":
        return GateDecision("allow", "not_relevant", "гейт не касается этого вызова",
                            "", found)

    # Дальше — файловый поиск или чтение кода.
    if not corpus:
        if kind == "file_search":
            return GateDecision(
                "allow", "no_corpus_search",
                "корпуса RAGRAF для этого каталога нет: файловый поиск разрешён",
                "RAGRAF: корпуса нет — факты будут из чтения файлов. "
                "Зарегистрировать: `ensure <каталог>`",
                found,
            )
        return GateDecision("allow", "no_corpus_read", "корпуса нет", "", found)

    if kind == "read":
        # Чтение кода законно: запрет вызывал бы ложные отказы. Гейт только
        # подсказывает, что есть операция точнее.
        if facts.get("is_code") and not index_used:
            return GateDecision(
                "allow", "read_hint",
                "чтение кода разрешено, но индекс в этом сеансе не использован",
                "RAGRAF: перед правкой полезнее `symbol`/`search_multi`, "
                "а `impact` покажет, что затронет изменение",
                found,
            )
        return GateDecision("allow", "read_ok", "чтение кода разрешено", "", found)

    # file_search в проекте с корпусом
    if fresh is False:
        # `deny` — воля владельца, и смягчать её «первым нарушением» нельзя:
        # замер 14.10.2026 показал, что при `--mode=deny` вызов всё равно
        # получал `ask`. Эскалация «вопрос → отказ» принадлежит режиму `ask`.
        if mode == "warn":
            verdict = "allow"
        elif mode == "deny":
            verdict = "deny"
        else:
            verdict = "ask" if first else "deny"
        return GateDecision(
            verdict, "stale_index",
            "индекс отстал от кода: файловый поиск даст неполную картину",
            "RAGRAF: сначала `ensure` (или `freshness --force`), затем "
            "`search_multi` — он найдёт сразу несколько запросов и назовёт копии",
            found,
        )
    if not index_used:
        if mode == "warn":
            return GateDecision(
                "allow", "index_unused_warn",
                "индекс актуален, но в этом сеансе не использован",
                "RAGRAF: `search_multi`/`symbol` ответят точнее и дешевле",
                found,
            )
        verdict = "deny" if mode == "deny" else ("ask" if first else "deny")
        return GateDecision(
            verdict, "index_unused",
            "индекс актуален, но в этом сеансе им не пользовались",
            "RAGRAF: используйте `search_multi` (несколько строк одним вызовом), "
            "`symbol` (где определён), `impact` (что затронет)",
            found,
        )
    # Свежо и индексом пользовались — правило не должно мешать.
    return GateDecision("allow", "index_used", "индекс использован в этом сеансе",
                        "", found)


# ---------------------------------------------------------------------------
# Состояние гейта: сеансы и журнал решений
# ---------------------------------------------------------------------------


def _path_key(path: str | Path) -> str:
    """Ключ каталога для состояния гейта: одна форма записи и чтения.

    ПОЧЕМУ ЭТО ОТДЕЛЬНАЯ ФУНКЦИЯ. Ключи писались одной формой (`C:/Users/...`),
    а инвалидация приходила другой (`C:\\Users\\<пользователь>\\...`), и «сброс кэша»
    молча ничего не сбрасывал: измерено 14.10.2026 в приёмочном прогоне — после
    правки файла гейт продолжал считать индекс свежим. Нормализация та же, что в
    реестре разработок (`catalog._norm`): POSIX-форма, нижний регистр, длинная
    форма пути вместо 8.3.
    """
    try:
        from .catalog import _norm

        return _norm(path)
    except Exception:  # noqa: BLE001
        return str(path).replace("\\", "/").rstrip("/").lower()


#: Сколько живёт метка задачи, названная агентом. Рабочий день: задача по коду
#: редко длиннее, а вчерашняя метка, приклеенная к сегодняшним вызовам, — это
#: ложная атрибуция, из-за которой метрика §3.4 считает чужое.
TASK_TTL_SEC = 8 * 3600


class GateState:
    """Состояние гейта между процессами: маркеры сеансов и последние решения.

    Почему файл, а не база корпуса. Хук запускается отдельным процессом, знает
    `session_id` и каталог, но НЕ знает, какой корпус ему соответствует; запись
    маркера в базу незнакомого корпуса была бы действием без основания. Поэтому
    маркеры и счётчики живут рядом с реестром разработок — там, где RAGRAF
    хранит состояние О СЕБЕ, а не о проекте.

    Конкурентная запись: несколько хуков могут писать одновременно. Запись
    атомарная (временный файл + замена), а счётчики — вспомогательные: потеря
    одной записи не делает метрику неверной, но и не скрывается (поле
    `write_failures`).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict | None = None
        self.write_failures = 0

    def _load(self) -> dict:
        if self._data is None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._data = raw if isinstance(raw, dict) else {}
            except Exception:
                self._data = {}
            self._data.setdefault("sessions", {})
            self._data.setdefault("decisions", [])
            self._data.setdefault("counters", {})
        return self._data

    def _save(self) -> None:
        data = self._load()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8", newline="\n")
            os.replace(tmp, self.path)
        except Exception:
            self.write_failures += 1

    def open_session(self, session_id: str, cwd: str = "", task: str = "") -> dict:
        """Отметить начало сеанса (вызывает `SessionStart`-хук)."""
        data = self._load()
        sid = str(session_id or "").strip() or f"anon-{uuid.uuid4().hex[:8]}"
        record = {
            "session_id": sid,
            "started_at": time.time(),
            "cwd": cwd or "",
            "task": task or sid,
            "index_calls": 0,
        }
        data["sessions"][sid] = record
        # Чистка старых сеансов: файл не должен расти бесконечно.
        cutoff = time.time() - 7 * 24 * 3600
        data["sessions"] = {
            k: v for k, v in data["sessions"].items()
            if float(v.get("started_at") or 0) >= cutoff
        }
        self._save()
        return record

    def session(self, session_id: str) -> dict | None:
        return (self._load()["sessions"] or {}).get(str(session_id or "")) or None

    def note_decision(self, decision: GateDecision, call: dict, *,
                      session_id: str = "", task: str = "") -> None:
        """Записать решение в состояние: счётчики и короткий журнал."""
        data = self._load()
        counters = data["counters"]
        key = decision.verdict
        counters[key] = int(counters.get(key, 0)) + 1
        counters["total"] = int(counters.get("total", 0)) + 1
        digest = hashlib.sha1(
            (call.get("command") or call.get("path") or "").encode("utf-8")
        ).hexdigest()[:12]
        data["decisions"].insert(0, {
            "ts": time.time(),
            "session_id": session_id,
            "task": task,
            "tool": call.get("tool", ""),
            "kind": call.get("kind", ""),
            "verdict": decision.verdict,
            "rule": decision.rule,
            "reason": decision.reason[:300],
            "arg_digest": digest,
        })
        data["decisions"] = data["decisions"][:500]
        sid = str(session_id or "")
        if sid:
            # Маркер сеанса создаётся и здесь: без этого эскалация «вопрос →
            # отказ» не работала бы у клиента без `SessionStart`-хука (измерено:
            # три нарушения подряд оставались `ask`).
            sessions = data.setdefault("sessions", {})
            record = sessions.setdefault(sid, {
                "session_id": sid, "started_at": time.time(), "cwd": "",
                "task": task or sid, "index_calls": 0,
            })
            record["last_seen"] = time.time()
            record["verdict"] = decision.verdict
        self._save()

    def note_index_use(self, session_id: str = "", task: str = "") -> None:
        """Отметить, что индексом пользовались (вызывает фасад при вызове).

        Запись создаётся и для сеанса, которого нет в маркерах: `SessionStart`
        есть не у всех клиентов, а обращение к индексу — само по себе факт
        («индексом пользовались»), и терять его из-за отсутствия маркера нельзя.
        """
        data = self._load()
        data["last_index_call"] = time.time()
        sid = str(session_id or "")
        if sid:
            sessions = data.setdefault("sessions", {})
            record = sessions.setdefault(sid, {
                "session_id": sid, "started_at": time.time(), "cwd": "",
                "task": task or sid, "index_calls": 0,
            })
            record["index_calls"] = int(record.get("index_calls") or 0) + 1
            record["last_index_call"] = time.time()
        self._save()

    def index_used_within(self, seconds: int = INDEX_USE_WINDOW_SEC, *,
                          session_id: str = "") -> bool:
        """Пользовались ли индексом — в ЭТОМ сеансе, а при его отсутствии недавно.

        Различие принципиально: «вызов был в окне 15 минут» и «вызов был в этом
        сеансе» — разные утверждения. Первое оправдывает файловый поиск чужими
        обращениями (измерено: сеанс `s3` получал `allow`, потому что индекс
        вызывали в сеансе `s2`), поэтому при известном `session_id` смотрим
        только счётчик сеанса.
        """
        data = self._load()
        sid = str(session_id or "")
        if sid:
            record = (data.get("sessions") or {}).get(sid) or {}
            return int(record.get("index_calls") or 0) > 0
        last = float(data.get("last_index_call") or 0)
        return bool(last and (time.time() - last) <= max(1, int(seconds)))

    def cached_freshness(self, root: str) -> dict | None:
        """Запомненный ответ о свежести корпуса (свежесть сверяется дорого)."""
        return (self._load().get("freshness") or {}).get(_path_key(root))

    def cache_freshness(self, root: str, fresh: bool | None, detail: dict,
                        ttl: int = GATE_FRESH_TTL_SEC) -> None:
        data = self._load()
        cache = data.setdefault("freshness", {})
        cache[_path_key(root)] = {
            "fresh": fresh, "detail": detail, "ts": time.time(),
            "ttl": max(30, int(ttl)),
        }
        if len(cache) > 50:
            newest = sorted(cache.items(), key=lambda kv: float(kv[1].get("ts") or 0),
                            reverse=True)[:50]
            data["freshness"] = dict(newest)
        self._save()

    def forget_freshness(self, root: str = "") -> None:
        """Забыть запомненную свежесть — явная инвалидация вместо TTL=0.

        Нужна там, где ответ заведомо устарел (например сразу после чужой
        переиндексации или правки кода): полагаться на `ttl=0` нельзя, у кэша
        есть нижняя граница в 30 секунд, и «сброс» молча не срабатывал —
        измерено 14.10.2026 в приёмочном прогоне гейта: отставший индекс
        продолжал считаться свежим.
        """
        data = self._load()
        cache = data.get("freshness") or {}
        key = _path_key(root)
        if not str(root or ""):
            data["freshness"] = {}
        else:
            cache.pop(key, None)
            data["freshness"] = cache
        self._save()

    def summary(self, *, limit: int = 10) -> dict:
        data = self._load()
        return {
            "counters": dict(data.get("counters") or {}),
            "sessions": len(data.get("sessions") or {}),
            "recent": list(data.get("decisions") or [])[:limit],
            "write_failures": self.write_failures,
        }

    # ---------------- метка задачи (RAGRAF_TASK) ----------------

    def set_current_task(self, name: str, *, root: str = "",
                         session_id: str = "") -> dict:
        """Назвать текущую задачу — так работает `RAGRAF_TASK` без клиента.

        Клиент не может передать метку в долгоживущий MCP-процесс: переменная
        окружения задаётся при старте, а задача меняется в ходе сеанса. Поэтому
        метку можно НАЗВАТЬ вызовом (`ragraf_task` / `task_set`), а состояние
        общее для процессов: его читают и MCP-сервер, и CLI.
        """
        data = self._load()
        record = {
            "name": str(name or "").strip()[:120],
            "root": _path_key(root) if root else "",
            "session_id": str(session_id or ""),
            "ts": time.time(),
        }
        data["current_task"] = record
        self._save()
        return record

    def current_task(self, root: str = "", ttl: int | None = None) -> str:
        """Метка текущей задачи для этого корпуса (пусто — если нет или стара).

        `ttl=None` — срок по умолчанию (`TASK_TTL_SEC`); явное значение
        используется как есть. Раньше здесь стояла нижняя граница 60 секунд, и
        проверка «срок истёк» не могла быть выражена: измерено в
        `tests/test_task_label.py`, где `ttl=1` продолжал возвращать метку.
        """
        record = self._load().get("current_task") or {}
        if not record:
            return ""
        limit = TASK_TTL_SEC if ttl is None else max(0, int(ttl))
        if (time.time() - float(record.get("ts") or 0)) > limit:
            return ""
        saved_root = str(record.get("root") or "")
        if saved_root and root and _path_key(root) != saved_root:
            # Метка названа для ДРУГОЙ разработки: приклеивать её к этому корпусу
            # значило бы записать чужие обращения в эту задачу.
            return ""
        return str(record.get("name") or "")


def state_path() -> Path:
    """Где лежит состояние гейта: рядом с реестром разработок.

    Рядом с реестром, потому что это состояние RAGRAF о СЕБЕ (маркеры сеансов и
    решения гейта), а не о конкретном проекте. Файл служебный и в индекс не
    попадает: каталог `catalog/` — часть самого RAGRAF.
    """
    return Path(__file__).resolve().parents[2] / "catalog" / "gate_state.json"


def state() -> GateState:
    return GateState(state_path())


#: Сколько живёт сеанс, если считать по маркеру. Два часа: дольше задача по коду
#: обычно не идёт, а «привязать» обращение к вчерашнему сеансу было бы ложью.
SESSION_TTL_SEC = 2 * 3600


def active_task(root: str | Path = "", st: GateState | None = None) -> str:
    """Метка активного сеанса гейта — чтобы обращения CLI/MCP не теряли задачу.

    Зачем: клиент не передаёт свой `session_id` ни CLI, ни MCP-серверу, поэтому
    вызовы из этих путей попадали в «(без задачи)», и метрика «доля задач с
    обращением к индексу» считала бы провал там, где индексом пользовались
    (в документах корпуса z четыре отчёта подряд фиксируют «инструментов
    `ragraf_*` в сеансе нет — работал через CLI»).

    Правило: самый свежий сеанс не старше `SESSION_TTL_SEC`, чей каталог —
    тот же проект или его часть. Пусто — если маркеров нет.
    """
    state_obj = st or state()
    sessions = (state_obj._load().get("sessions") or {})  # noqa: SLF001 — свой класс
    target = str(root or "").replace("\\", "/").rstrip("/").lower()
    best: tuple[float, str] = (0.0, "")
    now = time.time()
    for record in sessions.values():
        started = float(record.get("started_at") or 0)
        if not started or (now - started) > SESSION_TTL_SEC:
            continue
        cwd = str(record.get("cwd") or "").replace("\\", "/").rstrip("/").lower()
        if target and cwd and not (target == cwd or target.startswith(cwd + "/")
                                   or cwd.startswith(target + "/")):
            continue
        if started > best[0]:
            best = (started, str(record.get("task") or record.get("session_id") or ""))
    return best[1]


# ---------------------------------------------------------------------------
# Сбор фактов: корпус, свежесть, «индексом пользовались»
# ---------------------------------------------------------------------------


def find_corpus(cwd: str) -> dict | None:
    """Корпус, которому принадлежит каталог вызова (самый длинный корень).

    Сопоставление по префиксу пути, а не по точному равенству: инструмент
    вызывается из подкаталога проекта, и гейт обязан найти корпус и там.

    Пути сравниваются нормализованно — через `catalog._norm`: на Windows
    временный каталог и профиль пользователя отдаются КОРОТКИМИ 8.3-путями
    (`C:\\Users\\<пользователь>\\AppData\\...`), и без приведения к длинной форме один
    и тот же каталог не находился вовсе (измерено: `find_corpus` возвращал `None`
    для только что зарегистрированного корпуса).
    """
    if not cwd:
        return None
    from .catalog import Catalog, _norm

    target = _norm(cwd)
    best: dict | None = None
    for row in Catalog().summary()["entries"]:
        root = _norm(row.get("root") or "")
        if not root or not row.get("config"):
            continue
        if target == root or target.startswith(root + "/"):
            if best is None or len(root) > len(best["root"]):
                best = {"category": row.get("category"), "project": row.get("project"),
                        "root": root, "config": row.get("config"),
                        "rev_id": row.get("rev_id"), "state": row.get("state")}
    return best


def corpus_freshness(corpus: dict | None, st: GateState | None = None) -> tuple[bool | None, dict]:
    """Свеж ли корпус: сравнение отпечатка входа, без пересборки и без полного скана.

    ПОЧЕМУ ИМЕННО ОТПЕЧАТОК. Полная сверка (`watch.check`) внутри гейта
    неудобна дважды: она стоит прохода по корпусу и попадает в 15-секундный кэш
    сторожа — измерено 14.10.2026, когда гейт считал свежим корпус, в котором
    только что изменили файл. Отпечаток (пути, размеры, время правки) отвечает
    ровно на вопрос гейта: «менялся ли вход с момента индексации». Расхождение
    отпечатка — это ДОКАЗАТЕЛЬСТВО изменения, а не «доказать не удалось»,
    поэтому оно даёт `fresh=False` за доли секунды.

    Границы честные: отпечаток различает размер и время правки, поэтому файл,
    изменённый с сохранением и того, и другого, увидит только полная сверка —
    ошибаться в эту сторону безопасно (пропущенное «отстал»), в обратную
    (объявить свежим изменившееся) запрещено.

    `None` — «доказать не удалось» (нет ни отпечатка, ни индекса); отказывать по
    догадке нельзя. Результат запоминается в состоянии гейта: отставший корпус
    остаётся отставшим без повторных проверок, свежий перепроверяется по TTL.
    """
    if not corpus:
        return None, {}
    state_obj = st or state()
    root = corpus.get("root") or ""
    cached = state_obj.cached_freshness(root)
    if cached:
        age = time.time() - float(cached.get("ts") or 0)
        # Отставший корпус не «стареет»: он остаётся отставшим, пока его не
        # пересоберут, и перепроверять это на каждом вызове незачем.
        if cached.get("fresh") is False and age < 24 * 3600:
            return False, {**(cached.get("detail") or {}), "cached": True}
        if age < float(cached.get("ttl") or 0):
            return cached.get("fresh"), {**(cached.get("detail") or {}), "cached": True}
    try:
        from . import corpus as corpus_mod
        from . import watch as watch_mod
        from .config import load as load_config

        cfg = load_config(Path(corpus["config"]))
        started = time.perf_counter()
        stored = watch_mod.read_fingerprint(cfg) or {}
        fresh: bool | None = None
        detail: dict = {}
        if stored.get("fingerprint"):
            try:
                fingerprint, files_now, _ = corpus_mod.stat_fingerprint(cfg)
            except Exception as exc:  # noqa: BLE001
                return None, {"why": f"отпечаток не посчитан: {type(exc).__name__}"}
            fresh = bool(fingerprint == stored.get("fingerprint"))
            detail = {
                "verified_by": "fingerprint",
                "indexed_rev": str(stored.get("rev_id") or ""),
                "files_now": int(files_now),
                "files_indexed": int(stored.get("files") or 0),
                "cause": ("вход не менялся с индексации" if fresh
                          else "вход изменился после индексации"),
            }
        else:
            # Отпечатка нет (корпус собран прежней версией или чужим процессом):
            # честный путь — полная сверка БЕЗ пересборки.
            full = watch_mod.check(cfg, auto_reindex=False)
            fresh = not bool(getattr(full, "changed", False))
            detail = {"verified_by": "full",
                      "rev_id": str(getattr(full, "rev_id", "") or ""),
                      "files_now": int(getattr(full, "files_now", 0) or 0)}
        detail["seconds"] = round(time.perf_counter() - started, 3)
        # Свежий ответ перепроверяется по TTL, отставший — нет (см. выше).
        ttl = int(max(GATE_FRESH_TTL_SEC, detail["seconds"] * 10))
        state_obj.cache_freshness(root, fresh, detail, ttl)
        return fresh, detail
    except Exception as exc:  # noqa: BLE001 — гейт не имеет права падать
        return None, {"why": f"{type(exc).__name__}: {exc}"}


def handle_payload(payload: dict, *, mode: str | None = None,
                   gate_state: GateState | None = None) -> GateDecision:
    """Полный проход: разобрать payload → собрать факты → решить → записать.

    Это единственная точка входа для хука и для операции `gate`: обе обязаны
    решать одинаково, иначе «проверка гейта» перестанет что-либо значить.
    """
    event = str(payload.get("hook_event_name") or payload.get("event") or "")
    st = gate_state or state()
    if event == "SessionStart":
        st.open_session(str(payload.get("session_id") or ""), str(payload.get("cwd") or ""))
        return GateDecision("off", "session_start", "сеанс отмечен", "", {})

    tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
    call = classify_call(tool_name, payload.get("tool_input"))
    call["tool"] = tool_name
    session_id = str(payload.get("session_id") or "")
    cwd = str(payload.get("cwd") or "")

    resolved_mode = mode or os.environ.get("RAGRAF_GATE_MODE") or DEFAULT_MODE
    use_mode = str(resolved_mode).lower()
    if use_mode not in MODES:
        use_mode = DEFAULT_MODE

    if use_mode == "off":
        return GateDecision("off", "gate_off", "гейт выключен", "", {"mode": "off"})

    if call["kind"] not in ("file_search", "read"):
        # Обращение к индексу отмечается как «индексом пользовались».
        if call["kind"] == "index":
            st.note_index_use(session_id)
        decision = decide({"kind": call["kind"]}, mode=use_mode)
        st.note_decision(decision, call, session_id=session_id, task=session_id)
        return decision

    corpus = find_corpus(cwd)
    fresh, fresh_detail = (None, {}) if not corpus else corpus_freshness(corpus, st)
    index_used = st.index_used_within(session_id=session_id)
    session = st.session(session_id) or {}
    task = str(session.get("task") or session_id or "")
    first = str(session.get("verdict") or "") not in ("ask", "deny")
    facts = {
        "kind": call["kind"],
        "corpus": (corpus or {}).get("project") or (corpus or {}).get("category"),
        "corpus_root": (corpus or {}).get("root"),
        "fresh": fresh,
        "index_used": index_used,
        "is_code": bool(call.get("is_code")),
        "mode": use_mode,
        "first_offence": first,
        **({"freshness_detail": fresh_detail} if fresh_detail else {}),
    }
    decision = decide(facts, mode=use_mode)
    st.note_decision(decision, call, session_id=session_id, task=task)
    return decision

