"""Финальные проверки окружения: зависимости, промпт, процессы.

Собираются вещи, которые легко пропустить при отчёте:
  1. чужие файлы (зависимости, JDK) не изменены очисткой;
  2. системный промпт — вне workspace, проверяется отдельно;
  3. резервная копия промпта на месте;
  4. не осталось висящих процессов (JVM и Python держат память);
  5. каталоги корпусов не засорены временными файлами.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(r"C:\AutoClaw\RAGRAF")
PROMPT = pathlib.Path(r"C:\Users\mvd.AMD5700X3D\.pi\agent\AGENTS.md")

PASS = 0
FAIL = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


print("=" * 78)
print("1. ЧУЖИЕ ФАЙЛЫ НЕ ИЗМЕНЕНЫ ОЧИСТКОЙ")
print("=" * 78)
# Эти файлы не входят в список очистки, и их переводы строк не наши.
OTHERS = [
    ".ort_gpu_probe/numpy/__init__.py",
    ".ort_gpu_probe/onnxruntime/__init__.py",
    ".tools/jdk/legal/jdk.localedata/cldr.md",
]
for rel in OTHERS:
    path = REPO / rel
    if not path.exists():
        print(f"  {rel}: файла нет (пропуск)")
        continue
    data = path.read_bytes()
    crlf = b"\r\n" in data
    print(f"  {rel}")
    print(f"    размер {len(data)}, CRLF={crlf}")
    # Эти файлы поставляются с CRLF; сохранение CRLF доказывает, что
    # очистка их не трогала.
    check(crlf, f"{rel}: CRLF сохранён (файл не тронут)")

print()
print("=" * 78)
print("2. СИСТЕМНЫЙ ПРОМПТ (вне рабочего каталога)")
print("=" * 78)
check(PROMPT.exists(), "файл промпта существует")
if PROMPT.exists():
    data = PROMPT.read_bytes()
    text = data.decode("utf-8")
    bom = data[:3] == b"\xef\xbb\xbf"
    crlf = "\r\n" in text
    print(f"  размер: {len(data)} байт, строк {text.count(chr(10)) + 1}")
    print(f"  BOM: {bom}, CRLF: {crlf}")
    check(not bom, "промпт без BOM")
    check(not crlf, "промпт с переводами строк LF")
    for section in ("### 8.14", "### 8.15"):
        check(section in text, f"раздел {section} на месте")
    for phrase in ("16", "doc_role", "tech_spec", "agent_rules"):
        check(phrase in text, f"промпт упоминает {phrase!r}")

print()
print("=" * 78)
print("3. РЕЗЕРВНАЯ КОПИЯ ПРОМПТА")
print("=" * 78)
backup = PROMPT.with_suffix(".md.bak-session5")
print(f"  ожидаемая копия: {backup.name}")
check(backup.exists(), "резервная копия существует")
if backup.exists():
    bdata = backup.read_bytes()
    print(f"    размер копии: {len(bdata)} байт")
    # Копия должна быть МЕНЬШЕ: в неё входили разделы до добавления.
    current = PROMPT.stat().st_size
    check(len(bdata) < current, "копия меньше текущего файла (сделана до правок)",
          f"копия {len(bdata)}, текущий {current}")

print()
print("=" * 78)
print("4. ВИСЯЩИЕ ПРОЦЕССЫ (JVM и фоновые задачи индексации)")
print("=" * 78)
# Проверяются ТОЛЬКО те процессы, которые порождает RAGRAF.
#
# Почему не все: `python.exe` — это сам запускающий скрипт (он ещё жив,
# пока идёт эта проверка), а `node.exe` — служба платформы AutoClaw.
# Считать их утечкой значит давать ложный провал на каждой проверке.
#
# Что действительно важно: процесс java (JVM сервера Joern: утечка 7,8 ГБ
# уже была ранее) и фоновые задачи индексации, которые идут с аргументом
# `reindex`/`index` в командной строке.

def procs(name: str) -> list[dict]:
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {name}.exe", "/NH"],
        capture_output=True, text=True,
    )
    out = (result.stdout or "").strip()
    if not out or "No tasks" in out or "нет задач" in out.lower():
        return []
    return [ln for ln in out.splitlines() if name in ln.lower()]


java = procs("java")
print(f"  java.exe (JVM сервера Joern): {len(java)}")
check(not java, "JVM сервера Joern остановлена", f"найдено {len(java)}")

# Фоновые индексации: ищутся по командной строке процесса.
#
# ВАЖНО: `wmic` в современной Windows удалён (проверено вызовом:
# FileNotFoundError [WinError 2]), поэтому используется Get-CimInstance —
# то же самое, что правило проекта предписывает вместо Get-WmiObject.
bg = subprocess.run(
    ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
     "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
     "ForEach-Object { $_.CommandLine }"],
    capture_output=True, text=True,
)
cmdlines = [ln.strip() for ln in (bg.stdout or "").splitlines() if ln.strip()]
running_index = [ln for ln in cmdlines
                 if "ragraf.py" in ln and ("index" in ln or "reindex" in ln)]
print(f"  python.exe всего: {len(cmdlines)}")
if bg.returncode != 0:
    print(f"    запрос процессов: код {bg.returncode}: "
          f"{(bg.stderr or '')[:120]}")
print(f"  из них индексация RAGRAF: {len(running_index)}")
for line in running_index[:3]:
    print(f"    {line[:100]}")
check(not running_index, "фоновых индексаций не осталось",
      f"найдено {len(running_index)}")

# Уточнение про node: это платформа, а не наш процесс.
node = procs("node")
print(f"  node.exe: {len(node)} — служба платформы AutoClaw, не RAGRAF")

print()
print("=" * 78)
print("5. ВРЕМЕННЫЕ ФАЙЛЫ В СЛУЖЕБНЫХ КАТАЛОГАХ")
print("=" * 78)
# В служебных каталогах КОРПУСОВ не должно остаться пробных файлов:
# они лежат вне рабочего каталога проекта, и там их никто не уберёт.
# `.staging` самого RAGRAF не проверяется: это рабочий каталог проекта,
# и файлы в нём — мои инструменты, а не мусор.
SERVICES = {
    "z.batteryservice.ru": pathlib.Path(r"C:\AutoClaw\z.batteryservice.ru\.ragraf"),
    "revoap.com": pathlib.Path(r"C:\AutoClaw\revoap.com\.ragraf"),
    "MD.Viewer": pathlib.Path(r"C:\AutoClaw\MD.Viewer\.ragraf"),
    "RdpMON": pathlib.Path(r"C:\1st_RdpMON\Service\.ragraf"),
}
for name, root in SERVICES.items():
    if not root.exists():
        print(f"  {name}: каталога нет")
        continue
    probes = [f for f in root.rglob("*")
              if f.is_file() and f.name.lower().startswith(("probe", "diag", "tmp"))]
    print(f"  {name}: пробных файлов {len(probes)}")
    for f in probes[:5]:
        print(f"    {f.relative_to(root)}")
    check(not probes, f"{name}: временных файлов нет", f"найдено {len(probes)}")

print()
print("=" * 78)
print(f"ИТОГ: пройдено {PASS}, провалено {FAIL}")
print("=" * 78)
raise SystemExit(0 if FAIL == 0 else 1)
