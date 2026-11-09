"""Отпечаток сборки: живой процесс обязан отличать свой код от кода на диске.

Стиль как у остальных проверок проекта: самостоятельный скрипт с функцией
`check`, запускается напрямую (`python tests/test_code_drift.py`).

Какой дефект закрывается (измерен 06.11.2026). `build_fingerprint()` считался
по ОДНОМУ файлу (`mcp_server.py`), поэтому правки в `watch.py`, `catalog.py`,
`ensure.py` живой сервер не замечал: отпечаток не менялся, и клиент видел
«тот же build» у процесса, который работает старым кодом.

Проверки:
  1. отпечаток воспроизводим: два вызова подряд дают одно значение;
  2. отпечаток покрывает ВСЕ модули пакета: появление нового файла `.py`
     меняет его (проверяется временным файлом, который удаляется в `finally`);
  3. `McpServer.code_drift()` молчит, когда сборка совпала, и называет оба
     отпечатка, когда разошлась.

Ничего не оставляет на диске: временный модуль удаляется, файлы пакета не
меняются.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf import mcp_server as mcp  # noqa: E402

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


def main() -> int:
    package = Path(mcp.__file__).resolve().parent

    print("=== 1. Отпечаток воспроизводим ===")
    first = mcp.build_fingerprint()
    second = mcp.build_fingerprint()
    check(first == second and len(first) == 12,
          "два вызова дают одно значение из 12 знаков", f"{first} / {second}")

    print("\n=== 2. Отпечаток покрывает весь пакет ===")
    probe = package / "_probe_drift_module.py"
    try:
        probe.write_text("# временный модуль проверки\n", encoding="utf-8", newline="\n")
        after = mcp.build_fingerprint()
    finally:
        probe.unlink(missing_ok=True)
    restored = mcp.build_fingerprint()
    check(after != first, "новый модуль пакета меняет отпечаток",
          f"{first} → {after}")
    check(restored == first, "после удаления отпечаток вернулся", restored)

    print("\n=== 3. code_drift называет расхождение ===")
    server = mcp.McpServer.__new__(mcp.McpServer)  # без запуска сервера
    server.build = mcp.build_fingerprint()
    server._code_checked_at = 0.0
    server._code_drift = ""
    check(server.code_drift() == "", "при совпадении сборки расхождения нет",
          server.code_drift())
    server.build = "не-тот-код"
    server._code_checked_at = 0.0
    drift = server.code_drift()
    check(bool(drift) and "не-тот-код" in drift and "перезапуск" in drift,
          "расхождение названо обоими отпечатками и что делать", drift)

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
