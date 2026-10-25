"""Расширение RAGRAF: статистика за сессию (для команды /RAGRAF в SagaAI).

ЗАЧЕМ. Владелец хочет команду `/RAGRAF`, которая показывает статистику
использования RAGRAF за текущую сессию решения задачи. Разведка показала,
что это НЕДОСТУПНО из коробки:

  * «сессия» в RAGRAF — это ОДИН запуск процесса `tools/ragraf.py`,
    идентификатор вида `s<epoch_ms>-<pid>`;
  * каждый вызов инструмента из SagaAI запускает НОВЫЙ процесс, поэтому
    «сессия RAGRAF» живёт доли секунды и не совпадает с задачей пользователя;
  * CLI `usage` вообще не имеет флага «за сессию» — читает общую сводку.

РЕШЕНИЕ. Вводится понятие «метка задачи» (task): агент, решающий задачу,
передаёт один и тот же идентификатор во всех вызовах RAGRAF. Тогда
статистика собирается по метке и отвечает на вопрос «сколько RAGRAF
сделал для этой задачи».

Как передаётся. Переменная окружения `RAGRAF_TASK` — тот же механизм,
что уже используется для имени агента: он работает для любого способа
вызова (CLI, скрипт, кастомная функция) и не меняет синтаксис команды.

Что добавляется:
  1. поле `task` в таблицах журнала + догоняющая миграция;
  2. чтение `RAGRAF_TASK` из окружения в CLI;
  3. фильтр `--task` в команде `usage`;
  4. агрегат по задаче в сводке.

Ничего не удаляется: `task` необязательна, прежние записи видны как пустые.
"""

from __future__ import annotations

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]

# --- 1. Схема журнала: поле task ---
print("=" * 78)
print("1. ПОЛЕ task В СХЕМЕ ЖУРНАЛА")
print("=" * 78)
target = REPO / "src" / "ragraf" / "telemetry.py"
text = target.read_text(encoding="utf-8")

OLD_INIT = """    def __init__(self, control, tool: str = "unknown", agent: str = "unknown",
                 agent_kind: str = "unknown") -> None:"""
NEW_INIT = """    def __init__(self, control, tool: str = "unknown", agent: str = "unknown",
                 agent_kind: str = "unknown", task: str = "") -> None:"""
if OLD_INIT in text and "task: str" not in OLD_INIT:
    text = text.replace(OLD_INIT, NEW_INIT, 1)
    print("  сигнатура дополнена полем task")

OLD_ASSIGN = """        self.agent = agent or "unknown"
        self.agent_kind = agent_kind or "unknown"
        self._ensure_schema()"""
NEW_ASSIGN = """        self.agent = agent or "unknown"
        self.agent_kind = agent_kind or "unknown"
        # Метка задачи: позволяет собрать статистику за ОДНУ задачу
        # пользователя, а не за один запуск процесса. Нужна команде
        # `/RAGRAF` в SagaAI: там каждый вызов инструмента — новый процесс,
        # поэтому «сессия RAGRAF» с задачей не совпадает.
        self.task = (task or "").strip()
        self._ensure_schema()"""
if OLD_ASSIGN in text:
    text = text.replace(OLD_ASSIGN, NEW_ASSIGN, 1)
    print("  поле task сохраняется в объекте")

# Догоняющая миграция: добавляется колонка task.
OLD_MIGR = """        for column, table in (("agent", "usage_session"),
                              ("agent_kind", "usage_session"),
                              ("agent", "usage_call"),
                              ("agent_kind", "usage_call"),
                              ("tool", "usage_call")):"""
NEW_MIGR = """        for column, table in (("agent", "usage_session"),
                              ("agent_kind", "usage_session"),
                              ("task", "usage_session"),
                              ("agent", "usage_call"),
                              ("agent_kind", "usage_call"),
                              ("task", "usage_call"),
                              ("tool", "usage_call")):"""
if OLD_MIGR in text:
    text = text.replace(OLD_MIGR, NEW_MIGR, 1)
    print("  догоняющая миграция дополнена колонкой task")

# Индекс по задаче.
OLD_IDX = """            "CREATE INDEX IF NOT EXISTS idx_usage_call_agent "
            "ON usage_call(agent)","""
NEW_IDX = """            "CREATE INDEX IF NOT EXISTS idx_usage_call_agent "
            "ON usage_call(agent)",
            "CREATE INDEX IF NOT EXISTS idx_usage_call_task "
            "ON usage_call(task)","""
if OLD_IDX in text:
    text = text.replace(OLD_IDX, NEW_IDX, 1)
    print("  индекс по задаче добавлен")

# Запись задачи в сессию.
OLD_SESSION = """                "INSERT INTO usage_session(session_id, started_at, tool, agent,"
                " agent_kind, note) VALUES(?,?,?,?,?,?)",
                (sid, time.time(), self.tool, self.agent, self.agent_kind, ""),"""
NEW_SESSION = """                "INSERT INTO usage_session(session_id, started_at, tool, agent,"
                " agent_kind, task, note) VALUES(?,?,?,?,?,?,?)",
                (sid, time.time(), self.tool, self.agent, self.agent_kind,
                 self.task, ""),"""
if OLD_SESSION in text:
    text = text.replace(OLD_SESSION, NEW_SESSION, 1)
    print("  задача записывается в сессию")

# Запись задачи в вызов.
OLD_CALL = """                       replaces, error, tool, agent, agent_kind, arg_hash, arg_digest)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)\"\"\","""
NEW_CALL = """                       replaces, error, tool, agent, agent_kind, task,
                       arg_hash, arg_digest)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)\"\"\","""
if OLD_CALL in text:
    text = text.replace(OLD_CALL, NEW_CALL, 1)
    print("  схема INSERT для вызова обновлена")

OLD_VALUES = """                    self.agent,
                    self.agent_kind,
                    rec.arg_hash,
                    rec.arg_digest,"""
NEW_VALUES = """                    self.agent,
                    self.agent_kind,
                    self.task,
                    rec.arg_hash,
                    rec.arg_digest,"""
if OLD_VALUES in text:
    text = text.replace(OLD_VALUES, NEW_VALUES, 1)
    print("  значения INSERT для вызова обновлены")

target.write_text(text, encoding="utf-8", newline="\n")
print(f"\n  файл: {target.relative_to(REPO)} ({len(text.encode('utf-8'))} б)")
