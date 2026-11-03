"""Проверка операции ensure: свежий корпус, отставший, новый и отказы.

Стиль файла совпадает с остальными проверками проекта: самостоятельный скрипт
с собственной функцией `check`, а не pytest-функции. Так сделано потому, что
многие проверки RAGRAF требуют подготовленного окружения и запускаются
напрямую (`python tests/test_ensure.py`).

Проверяется главное свойство операции: вызывающая сторона может не знать про
RAGRAF ничего, кроме каталога проекта, и всё равно получить актуальные факты.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Состояние прогона — в своём каталоге: реестр разработок и настройки
# пишутся туда, а не в живой `catalog/`. Измерено 30.10.2026: прежняя
# редакция оставляла записи в живом реестре, и следующий прогон начинался
# с изменённого состояния (этап 7). Переменная ставится ДО импорта модулей.
_STATE = Path(tempfile.mkdtemp(prefix="ragraf-ensure-state-"))
os.environ["RAGRAF_CATALOG_DIR"] = str(_STATE)
# Конфигурация корпуса пишется туда же. Без этого `ensure(create=True)` клал
# её в живой `config/` проекта — а `config/**` входит в корпус RAGRAF, и имя
# файла содержит pid. Замер 31.10.2026: две проверки подряд обе запускали
# переиндексацию (49,6 и 42,8 с) — корпус «устаревал» от одного прогона
# набора. Переменная ставится ДО импорта модулей.
os.environ["RAGRAF_CONFIG_DIR"] = str(_STATE / "config")
(_STATE / "config").mkdir(parents=True, exist_ok=True)

REPO = Path(__file__).resolve().parents[1]

#: Снимок живого состояния: проверка утверждает, что прогон его не тронул.
#:
#: Сравнивается СОДЕРЖАНИЕ, а не байты: `Catalog.save()` двигает `updated_at`
#: при каждом сохранении, в том числе когда сохраняет ДРУГОЙ процесс на том же
#: реестре (долгоживущий MCP-сервер, другой агент). Замер 31.10.2026: набор
#: падал на «живой catalog/catalog.json не изменён прогоном» примерно в одном
#: прогоне из трёх-шести, и его собственные вызовы шли в отведённый каталог.
#: Отметка времени называется отдельно: она двигается и без участия набора.
_LIVE = REPO / "catalog"
_VOLATILE = ("updated_at",)


def _semantic(raw: bytes | None) -> str | None:
    """Содержательная часть файла состояния: без подвижных отметок времени."""
    import hashlib
    import json

    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return hashlib.sha256(raw).hexdigest()
    if isinstance(data, dict):
        for key in _VOLATILE:
            data.pop(key, None)
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def _live_entry_names(raw: bytes | None) -> set[str]:
    """Имена разработок в живом реестре: состав, а не отметки времени."""
    import json

    if raw is None:
        return set()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return set()
    entries = data.get("entries") if isinstance(data, dict) else None
    return set(entries) if isinstance(entries, dict) else set()


_LIVE_BEFORE = {name: (_LIVE / name).read_bytes()
                for name in ("catalog.json", "settings.json", "registry_history.jsonl")
                if (_LIVE / name).exists()}
#: Снимок конфигураций пробы в ЖИВОМ `config/`: прогон не должен добавить ни
#: одной. Файл пробы входит в корпус RAGRAF (`config/**`), а имя содержит pid.
_CONFIG_BEFORE = sorted(q.name for q in (REPO / "config").glob("_ragraf_ensure_probe_*"))
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
    created_config = ""
    inside_home: Path | None = None
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
        # Имя уникально для прогона. Прежняя редакция переиспользовала общий
        # каталог `_ragraf_ensure_probe`, и остаток прошлого прогона давал
        # `actual` вместо `created`: 12 из 17 проверок не запускались —
        # «3 из 17» (воспроизведено 30.10.2026). Остатки снимаются явно
        # и называются в выводе, а не убираются молча.
        inside_home = home / f"_ragraf_ensure_probe_{os.getpid()}"
        for stale in sorted(home.glob("_ragraf_ensure_probe*")):
            if stale.name == inside_home.name or not stale.is_dir():
                continue
            shutil.rmtree(stale, ignore_errors=True)
            if stale.exists():
                print(f"  [ВНИМАНИЕ] остаток прошлого прогона не убран: {stale}")
            else:
                print(f"  убран остаток прошлого прогона: {stale.name}")
        inside_home.mkdir(parents=True, exist_ok=True)
        (inside_home / ".gitignore").write_text("probe\n", encoding="utf-8")
        for name in ("alpha", "beta", "gamma"):
            (inside_home / f"{name}.py").write_text(
                f"def probe_{name}(x):\n    return x\n", encoding="utf-8")
        outcome = ensure_mod.ensure(inside_home, create=True, index=True)
        # Путь конфигурации запоминается СРАЗУ, а не в ветке `created`.
        # Измеренный дефект (31.10.2026): при статусе `error` конфигурация уже
        # лежит в `config/` проекта, но `created_config` оставался пустым, и
        # уборка ниже ничего не снимала — за прогон `test_state_isolation`
        # (он запускает этот набор несколько раз) накопилось пять файлов пробы.
        created_config = str(getattr(outcome, "config", "") or "")
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

        for target_dir in [d for d in (tmp, inside_home, _STATE) if d is not None]:
            shutil.rmtree(target_dir, ignore_errors=True)
            if target_dir.exists():
                print(f"  примечание: каталог не удалён: {target_dir}")

    # Уборка того, что создал ПРОДУКТ, а не тест. `ensure(create=True)` кладёт
    # конфигурацию корпуса в `config/` проекта RAGRAF (это его штатное место), а
    # корпус пробы живёт в профиле пользователя. Раньше конфиг оставался в
    # дереве: замер 31.10.2026 — шесть файлов `config/_ragraf_ensure_probe_*.yaml`
    # после прогонов, из-за них число файлов корпуса уехало 961 → 967, а гейт
    # публикации отказал «числа README не совпадают с активной ревизией».
    # Остаток снимается по имени ЭТОГО прогона, а не по маске: чужие файлы
    # трогать нельзя.
    if created_config:
        leftover = Path(created_config)
        if leftover.exists():
            try:
                leftover.unlink()
                print(f"  убран конфиг пробы: {leftover.name}")
            except OSError as exc:
                print(f"  примечание: конфиг пробы не удалён ({leftover.name}): {exc}")

    print("\n=== 7. Живое состояние не изменилось ===")
    # Проверяется то, за что набор ОТВЕЧАЕТ: проба не попала в живой реестр.
    # Сравнение байтов живого `catalog.json` ложно обвиняло набор (измерено
    # 31.10.2026: провал в одном прогоне из трёх): живой корпус переиндексировал
    # ДРУГОЙ процесс — долгоживущий MCP-сервер со сторожем, который реагирует на
    # правки файлов, — и реестр честно менял rev_id и indexed_at, к набору это
    # отношения не имеет. Свойство набора — «проба не зарегистрирована в живом
    # реестре и живой настройки не тронуты», и оно проверяемо независимо.
    live_names_before = _live_entry_names(_LIVE_BEFORE.get("catalog.json"))
    live_names_now = _live_entry_names(
        (_LIVE / "catalog.json").read_bytes() if (_LIVE / "catalog.json").exists() else None)
    check(live_names_now == live_names_before,
          "живой catalog/catalog.json не получил разработку пробы",
          f"было {sorted(live_names_before)}, стало {sorted(live_names_now)}")
    probe_marks = (str(_STATE).replace("\\", "/"), "ragraf-ensure-", "probe")
    polluted = [name for name in live_names_now - live_names_before]
    check(not polluted, "в живом реестре нет записи пробы", str(polluted))
    history_now = ((_LIVE / "registry_history.jsonl").read_bytes()
                   if (_LIVE / "registry_history.jsonl").exists() else b"")
    check(not any(mark.encode("utf-8") in history_now for mark in probe_marks),
          "журнал состава живого реестра не упоминает пробу")
    settings_now = ((_LIVE / "settings.json").read_bytes()
                    if (_LIVE / "settings.json").exists() else None)
    check(_semantic(settings_now) == _semantic(_LIVE_BEFORE.get("settings.json")),
          "живой catalog/settings.json не изменён прогоном")
    if live_names_now == live_names_before:
        print("  [примечание] живой реестр мог сменить rev_id/indexed_at: это "
              "делает другой процесс на том же реестре, а не набор")
    # Конфигурация пробы: живой `config/` проекта входит в корпус RAGRAF, а имя
    # файла содержит pid — такой файл делает корпус устаревшим после каждого
    # прогона (замер 31.10.2026: две проверки подряд — две переиндексации).
    config_now = sorted(q.name for q in (REPO / "config").glob("_ragraf_ensure_probe_*"))
    check(config_now == _CONFIG_BEFORE,
          "живой config/ не получил конфигурацию пробы",
          f"было {_CONFIG_BEFORE}, стало {config_now}")
    # Отрицательный контроль: проверка выше не должна проходить «на пустоте».
    # Смотрим на путь, который вернул сам продукт: файл к этому моменту уже
    # убран уборкой, а путь остался.
    redirected = Path(created_config).parent if created_config else None
    check(redirected is not None and redirected.resolve() == (_STATE / "config").resolve(),
          "конфигурация пробы создана В отведённом каталоге (RAGRAF_CONFIG_DIR)",
          f"путь конфигурации: {created_config or 'не создана'} — "
          f"ожидался каталог {_STATE / 'config'}")

    print("\n=== ИТОГ ===")
    print(f"пройдено: {PASS}, провалено: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
