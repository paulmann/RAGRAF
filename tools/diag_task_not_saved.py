"""Диагностика: почему метка задачи не попала в журнал.

Проверено: код читает RAGRAF_TASK (`cli.py:260, 271`) и команда `query`
создаёт фасад с задачей. Но в журнале `task = None`.

Возможные причины:
  1. запись задачи в INSERT не сработала (дефект правки);
  2. метка не доходит до процесса (окружение);
  3. проверка читает не тот журнал.

Проверяется прямым вызовом CLI с изолированным журналом и печатью
каждого шага.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys

REPO = pathlib.Path(r"C:\AutoClaw\RAGRAF")
SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
STAGING = REPO / ".staging"

print("=" * 78)
print("1. ПРОВЕРКА: ЧИТАЕТ ЛИ CLI МЕТКУ ИЗ ОКРУЖЕНИЯ")
print("=" * 78)
# Прямой запуск: импортируется функция чтения метки.
probe = STAGING / "probe_task_env.py"
probe.write_text(
    "import os, sys\n"
    f"sys.path.insert(0, r'{REPO / 'src'}')\n"
    "from ragraf.cli import _task_from_env, _agent_from_env\n"
    "print('TASK=' + repr(_task_from_env()))\n"
    "print('AGENT=' + repr(_agent_from_env()))\n",
    encoding="utf-8", newline="\n",
)
env = dict(os.environ)
env["RAGRAF_TASK"] = "probe-task-env-001"
env["RAGRAF_AGENT"] = "ProbeAgent"
env["RAGRAF_AGENT_KIND"] = "external"
env["PYTHONIOENCODING"] = "utf-8"
proc = subprocess.run([sys.executable, str(probe)], capture_output=True,
                      text=True, encoding="utf-8", errors="replace", env=env)
print(f"  вывод: {(proc.stdout or '').strip()}")
if proc.stderr:
    print(f"  ошибки: {proc.stderr.strip()[:300]}")

print()
print("=" * 78)
print("2. ЗАПИСЬ В ЖУРНАЛ: ПРОВЕРКА INSERT")
print("=" * 78)
# Проверяется напрямую: создаётся телеметрия с задачей и читается строка.
iso_dir = STAGING / "diag_task_iso"
shutil.rmtree(iso_dir, ignore_errors=True)
iso_dir.mkdir(parents=True)
iso_db = iso_dir / "control.sqlite"

sys.path.insert(0, str(REPO / "src"))
from ragraf.config import load as load_config  # noqa: E402
from ragraf.revisions import ControlStore  # noqa: E402
from ragraf.telemetry import CallRecord, Telemetry  # noqa: E402

cfg = load_config(REPO / "config" / "sagaai.yaml")
ctrl = ControlStore.__new__(ControlStore)
ctrl.cfg = cfg
ctrl.conn = sqlite3.connect(str(iso_db))
ctrl.conn.row_factory = sqlite3.Row

tel = Telemetry(ctrl, tool="cli", agent="ProbeAgent",
                agent_kind="external", task="diag-task-001")
tel.record(CallRecord(op="search", rev_id="t", capability="full",
                      duration_ms=1.0, found=True, fact_count=1,
                      facts_with_span=1, result_chars=10,
                      error=None, tool="cli", arg_hash="", arg_digest=""))

row = ctrl.conn.execute(
    "SELECT task, agent, tool FROM usage_call ORDER BY id DESC LIMIT 1"
).fetchone()
print(f"  запись вызова: task={row['task']!r} agent={row['agent']!r} "
      f"tool={row['tool']!r}")
sess = ctrl.conn.execute(
    "SELECT task, agent FROM usage_session ORDER BY started_at DESC LIMIT 1"
).fetchone()
print(f"  запись сессии: task={sess['task']!r} agent={sess['agent']!r}")
print(f"  схема usage_call: "
      f"{[r[1] for r in ctrl.conn.execute('PRAGMA table_info(usage_call)')][-6:]}")
ctrl.conn.close()

print()
print("=" * 78)
print("3. РЕАЛЬНЫЙ ВЫЗОВ CLI С МЕТКОЙ (изолированный журнал)")
print("=" * 78)
# Конфиг направляется на изолированный каталог: журнал и индекс.
cfg_text = (REPO / "config" / "sagaai.yaml").read_text(encoding="utf-8")
# Подменяется путь к каталогу .ragraf.
cfg_text = cfg_text.replace("C:/!.Tihon/sagaai_edit_github/.ragraf",
                            str(iso_dir).replace("\\", "/"))
iso_cfg = STAGING / "diag_task_iso.yaml"
iso_cfg.write_text(cfg_text, encoding="utf-8", newline="\n")
print(f"  конфиг: {iso_cfg.name}")

# Копируется индекс из рабочего корпуса (нужен для запроса).
live_index = SAGA / ".ragraf" / "index"
if live_index.exists():
    print("  индекс рабочего корпуса используется напрямую (только чтение)")
    cfg_text = (REPO / "config" / "sagaai.yaml").read_text(encoding="utf-8")
    iso_cfg.write_text(cfg_text, encoding="utf-8", newline="\n")

# Журнал подменяется через переменную: RAGRAF читает его из конфига,
# поэтому используется обёртка.
runner = STAGING / "diag_task_runner.py"
runner.write_text(
    "import sys, pathlib\n"
    f"sys.path.insert(0, r'{REPO / 'src'}')\n"
    "from ragraf.config import ProjectConfig\n"
    f"_probe = pathlib.Path(r'{iso_db}')\n"
    "ProjectConfig.control_db = property(lambda self: _probe)\n"
    "from ragraf.cli import main\n"
    "raise SystemExit(main())\n",
    encoding="utf-8", newline="\n",
)

env = dict(os.environ)
env["RAGRAF_TASK"] = "diag-cli-001"
env["RAGRAF_AGENT"] = "ProbeAgent"
env["RAGRAF_AGENT_KIND"] = "external"
env["PYTHONIOENCODING"] = "utf-8"
proc = subprocess.run(
    [sys.executable, str(runner), "--json", "-p", str(iso_cfg),
     "query", "search", "DevAgent", "--limit", "2"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    env=env, timeout=600,
)
print(f"  код возврата: {proc.returncode}")
if proc.stderr:
    print(f"  stderr: {proc.stderr.strip()[:200]}")

conn = sqlite3.connect(str(iso_db))
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT task, agent, tool, op FROM usage_call ORDER BY id DESC LIMIT 3"
).fetchall()
print(f"\n  записи в изолированном журнале:")
for r in rows:
    print(f"    task={str(r['task']):<16} agent={str(r['agent']):<12} "
          f"tool={str(r['tool']):<6} op={r['op']}")
conn.close()

# Уборка.
shutil.rmtree(iso_dir, ignore_errors=True)
probe.unlink(missing_ok=True)
runner.unlink(missing_ok=True)
iso_cfg.unlink(missing_ok=True)
print("\n  временные файлы удалены")
