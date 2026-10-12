"""Проверка операции ensure: свежий корпус, отставший, новый и отказы.

Стиль файла совпадает с остальными проверками проекта: самостоятельный скрипт
с собственной функцией `check`, а не pytest-функции. Так сделано потому, что
многие проверки RAGRAF требуют подготовленного окружения и запускаются
напрямую (`python tests/test_ensure.py`).

Проверяется главное свойство операции: вызывающая сторона может не знать про
RAGRAF ничего, кроме каталога проекта, и всё равно получить актуальные факты.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ragraf import ensure as ensure_mod  # noqa: E402
from ragraf.catalog import Catalog  # noqa: E402

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


def _stop_ragraf_joern() -> None:
    """Остановить серверы Joern, поднятые индексацией RAGRAF.

    Через WMI, а не по списку процессов: нужна командная строка, по ней и
    отличаются серверы RAGRAF от прочих java-процессов.
    """
    if sys.platform != "win32":
        return
    import subprocess as sp

    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='java.exe'\" | "
        "Where-Object { $_.CommandLine -like '*RAGRAF*' -and "
        "$_.CommandLine -like '*joern*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
        "-ErrorAction SilentlyContinue; Write-Output $_.ProcessId }"
    )
    out = sp.run(["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", ps],
                 capture_output=True, text=True, timeout=180)
    stopped = [line for line in (out.stdout or "").splitlines() if line.strip()]
    if stopped:
        print(f"  остановлено серверов Joern: {len(stopped)}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ragraf_ensure_"))
    created_category = None
    try:
        print("=== 1. Отказы: каталог не является проектом ===")
        home = Path.home()
        out = ensure_mod.ensure(home, create=True, index=False)
        check(out.status == "not_a_project", "домашний каталог отклонён", out.status)

        root_drive = Path(home.anchor)
        out = ensure_mod.ensure(root_drive, create=True, index=False)
        check(out.status == "not_a_project", "корень диска отклонён", out.status)

        bare = tmp / "bare"
        bare.mkdir()
        (bare / "notes.txt").write_text("не код", encoding="utf-8")
        out = ensure_mod.ensure(bare, create=True, index=False)
        check(out.status == "not_a_project", "каталог без маркеров отклонён", out.reason)

        print("\n=== 2. Проект внутри профиля пользователя НЕ отклоняется ===")
        # Регресс: первая версия запрещала любой путь, содержащий `users`,
        # то есть ни один проект в профиле пользователя завести было нельзя.
        inside_home = home / "_ragraf_ensure_probe"
        inside_home.mkdir(exist_ok=True)
        (inside_home / ".gitignore").write_text("probe\n", encoding="utf-8")
        for name in ("alpha", "beta", "gamma"):
            (inside_home / f"{name}.py").write_text(
                f"def probe_{name}(x):\n    return x\n", encoding="utf-8")
        outcome = ensure_mod.ensure(inside_home, create=True, index=True)
        check(outcome.status in ("created", "error"),
              "проект в профиле пользователя принят", f"{outcome.status}: {outcome.reason}")
        if outcome.status == "created":
            created_category = outcome.category or inside_home.name
            check(bool(outcome.config), "конфигурация создана", outcome.config)
            check(bool(outcome.rev_id), "индекс создан", outcome.rev_id)

            print("\n=== 2a. Реестр получил результат прогона ===")
            # Измеренный дефект: ensure регистрировал корпус и результат прогона не
            # записывал, поэтому реестр показывал пустую ревизию и нули при
            # фактически построенном индексе. Проверка ловит возврат дефекта.
            entry = Catalog().get(created_category)
            check(entry is not None, "разработка заведена в реестре", str(created_category))
            if entry is not None:
                check(bool(entry.rev_id),
                      "реестр знает ревизию после ensure",
                      f"rev_id={entry.rev_id!r}")
                check(int(entry.node_count or 0) > 0,
                      "реестр знает число узлов после ensure",
                      f"node_count={entry.node_count!r}")
                last = entry.runs[0] if entry.runs else None
                trigger = (last.get("trigger") if isinstance(last, dict)
                           else getattr(last, "trigger", None))
                check(trigger == "ensure:index",
                      "запись помечена прогоном ensure",
                      f"trigger={trigger!r}")

            print("\n=== 3. Повторный вызов: корпус актуален ===")
            again = ensure_mod.ensure(inside_home, create=False, index=True)
            check(again.status == "actual",
                  "повторный ensure говорит «актуален»", f"{again.status}: {again.reason}")
            check(again.category == created_category,
                  "корпус найден по каталогу", f"{again.category} != {created_category}")

            print("\n=== 4. Правка файла: индекс актуализируется сам ===")
            (inside_home / "alpha.py").write_text(
                "def probe_alpha(x):\n    return x + 1  # изменено\n", encoding="utf-8")
            after = ensure_mod.ensure(inside_home, create=False, index=True,
                                      force_reindex=True)
            check(after.status in ("reindexed", "actual"),
                  "после правки ensure не падает", f"{after.status}: {after.reason}")
            check(after.status == "reindexed",
                  "индекс пересобран после правки", after.status)

            print("\n=== 5. Состояние через CLI (то, чем пользуется агент) ===")
            cli = REPO / "tools" / "ragraf.py"
            result = subprocess.run(
                [sys.executable, str(cli), "ensure", str(inside_home), "--json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=600, cwd=str(REPO),
            )
            check(result.returncode == 0, "CLI ensure завершился успехом",
                  result.stderr[-300:])
            check('"status"' in (result.stdout or ""),
                  "CLI ensure вернул JSON", (result.stdout or "")[:200])

            print("\n=== 6. Операция без индексации ничего не меняет ===")
            only_check = ensure_mod.ensure(inside_home, create=False, index=False)
            check(only_check.status in ("actual", "not_a_project"),
                  "режим без индексации не падает", only_check.status)
        else:
            check(False, "создание корпуса в профиле пользователя", outcome.reason)

    finally:
        # Пробный корпус снимается с регистрации и стирается: иначе он остался
        # бы в реестре и попал бы в панель как «разработка».
        try:
            catalog = Catalog()
            data = catalog.load()
            for name, raw in list(data.get("entries", {}).items()):
                root = str(raw.get("root", ""))
                if "ragraf_ensure_probe" in root or str(tmp) in root:
                    data["entries"].pop(name, None)
            catalog.save()
        except Exception as exc:  # noqa: BLE001
            print(f"  примечание: реестр не очищен: {exc}")
        # Сервер Joern держит файлы `.ragraf` и сам не завершается вместе с
        # индексацией, поэтому каталог пробы не удаляется, а java остаётся на
        # порту. Останавливаются серверы, поднятые RAGRAF: признак — сочетание
        # `RAGRAF` и `joern` в командной строке (пути корпуса там нет).
        try:
            _stop_ragraf_joern()
        except Exception as exc:  # noqa: BLE001
            print(f"  примечание: Joern не остановлен: {exc}")

        for target_dir in (tmp, Path.home() / "_ragraf_ensure_probe"):
            shutil.rmtree(target_dir, ignore_errors=True)
            if target_dir.exists():
                print(f"  примечание: каталог не удалён: {target_dir}")

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
