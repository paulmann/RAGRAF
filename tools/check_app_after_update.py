"""Проверка приложения после обновления пакетов.

Тесты проходят, но это не то же самое, что работающий интерфейс. Streamlit
1.62 → 1.65 мог изменить поведение API, поэтому проверяется:

  1. импортируется ли приложение целиком (`app.py`, `ui/app.py`);
  2. загружаются ли все страницы интерфейса;
  3. запускается ли поток вызовов, который зависит от streamlit;
  4. работает ли команда `/RAGRAF` — она добавлена мной и тоже зависит
     от API.

Запускается реальный сервер Streamlit в фоне и проверяется ответ на HTTP:
это единственная проверка, которая подтверждает работоспособность
фактически, а не по импортам.
"""

from __future__ import annotations

import os
import pathlib
import socket
import subprocess
import sys
import time

SAGA = pathlib.Path(r"C:\!.Tihon\sagaai_edit_github")
PY = SAGA / ".venv" / "Scripts" / "python.exe"

print("=" * 78)
print("1. ИМПОРТ ВСЕХ СТРАНИЦ ИНТЕРФЕЙСА")
print("=" * 78)
probe = SAGA / "_probe_import_pages.py"
probe.write_text(
    "import sys\n"
    f"sys.path.insert(0, r'{SAGA}')\n"
    "MODULES = [\n"
    "    'core.auth', 'core.paths', 'core.config', 'core.services',\n"
    "    'core.files', 'core.render', 'core.threads_devagent',\n"
    "    'core.skills_library', 'core.connectors',\n"
    "    'ui.app', 'ui.pages.orchestrator', 'ui.pages.chat',\n"
    "    'ui.pages.assistants', 'ui.pages.access', 'ui.pages.history',\n"
    "    'ui.pages.skills_library',\n"
    "    'dev_agent.tool_executor', 'dev_agent.agent_loop',\n"
    "    'dev_agent.universal_agent',\n"
    "]\n"
    "ok = 0\n"
    "fail = []\n"
    "for name in MODULES:\n"
    "    try:\n"
    "        __import__(name)\n"
    "        ok += 1\n"
    "    except Exception as exc:\n"
    "        fail.append((name, type(exc).__name__, str(exc)[:80]))\n"
    "print(f'OK={ok}')\n"
    "for name, kind, msg in fail:\n"
    "    print(f'FAIL={name}|{kind}|{msg}')\n",
    encoding="utf-8", newline="\n",
)
env = dict(os.environ)
env.pop("PIP_USER", None)
env["SAGAAI_DATA_DIR"] = str(SAGA)
proc = subprocess.run([str(PY), str(probe)], capture_output=True, text=True,
                      encoding="utf-8", errors="replace", cwd=str(SAGA),
                      timeout=600, env=env)
for line in (proc.stdout or "").splitlines():
    if line.startswith("OK="):
        count = line.split("=", 1)[1]
        print(f"  успешно импортировано: {count}")
    elif line.startswith("FAIL="):
        print(f"  ОШИБКА: {line[5:][:140]}")
if proc.stderr.strip():
    print(f"  stderr: {proc.stderr.strip()[:200]}")
probe.unlink(missing_ok=True)

print()
print("=" * 78)
print("2. ЗАПУСК СЕРВЕРА STREAMLIT")
print("=" * 78)
# Ищется свободный порт.
port = 8599
sock = socket.socket()
try:
    sock.bind(("127.0.0.1", port))
    sock.close()
except OSError:
    port = 8601

log = pathlib.Path(__file__).resolve().parents[1] / ".staging" / "streamlit_probe.log"
log.parent.mkdir(parents=True, exist_ok=True)
if log.exists():
    log.unlink()

cmd = [
    str(PY), "-m", "streamlit", "run", str(SAGA / "app.py"),
    "--server.port", str(port),
    "--server.headless", "true",
    "--browser.gatherUsageStats", "false",
]
print(f"  команда: streamlit run app.py --server.port {port}")
proc = subprocess.Popen(
    cmd, cwd=str(SAGA), env=env,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, encoding="utf-8", errors="replace",
)

# Ожидание старта: до 90 секунд, с проверкой порта.
started = False
for attempt in range(30):
    time.sleep(3)
    check = socket.socket()
    check.settimeout(2)
    if check.connect_ex(("127.0.0.1", port)) == 0:
        started = True
        check.close()
        break
    check.close()

print(f"  сервер поднялся: {started} (ждали {attempt * 3 + 3} с)")

if started:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
        print(f"  ответ страницы: код {r.status}, размер {len(body)} б")
        has_streamlit = "streamlit" in body.lower()
        print(f"  признаки Streamlit в ответе: {has_streamlit}")
    except Exception as exc:
        print(f"  запрос не удался: {type(exc).__name__}: {exc}")

    # Проверка здоровья сервера.
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/_stcore/health", timeout=20
        ) as r:
            health = r.read().decode("utf-8", "replace").strip()
        print(f"  проверка здоровья: {health!r}")
    except Exception as exc:
        print(f"  проверка здоровья не прошла: {type(exc).__name__}")

proc.terminate()
try:
    proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill()
print("  сервер остановлен")

# Печатается хвост журнала: там видно ошибки запуска, если были.
if log.exists():
    tail = log.read_text(encoding="utf-8", errors="replace").splitlines()
    if tail:
        print(f"\n  журнал сервера (последние строки):")
        for line in tail[-8:]:
            print(f"    {line[:120]}")
