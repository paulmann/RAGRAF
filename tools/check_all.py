"""Раннер проверок проекта: один запуск, один вердикт.

Зачем. Единого раннера в проекте не было: 77 файлов в `tests/` — самостоятельные
скрипты, и запускать их приходилось по одному (в инструкции AGENTS.md так и
написано). Из-за этого «прогнать проверки» на практике значило «прогнать те,
которые вспомнил», и часть проверок не запускалась месяцами.

Что делает:
  * запускает каждый файл ОТДЕЛЬНЫМ процессом (у проверок свои фикстуры и свои
    коды возврата) с ограничением времени;
  * печатает вердикт по каждой и итог, а в конце — строку для отчёта;
  * делит проверки на группы, потому что не все они равны по требованиям:
      `core`      — ядро индекса, чужие проекты и сервисы не нужны (по умолчанию);
      `external`  — нужны другие корпуса на этой машине (z.batteryservice.ru,
                    SagaAI, RDP-Diagnostic-Tool и т.п.);
      `service`   — нужен запущенный сервис (админ-панель, MCP по HTTP, Joern,
                    модель эмбеддингов, видеокарта);
      `all`       — всё подряд (может включать заведомо красные проверки чужих
                    проектов — см. `--list`).

Почему группы заданы списком, а не «угаданы» по имени файла: угадывание
ошибается молча (проверка попадёт в `core`, потребует чужой корпус и упадёт по
чужой причине). Список видно в `--list`, и его можно поправить одной строкой.

Границы: раннер НЕ заменяет `tools/Invoke-ProjectDoctrine.ps1` (чекер конвенций
по исходникам) — он запускает проверки `tests/`. Для полного вердикта нужно и
то, и другое; в конце раннер об этом напоминает.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

#: Ядро: проверки, которым не нужны ни другие проекты, ни запущенные сервисы.
CORE = [
    "test_lang_routing.py", "test_generic_extract.py", "test_inheritance.py",
    "test_cpg_call_names.py", "test_graph_algo.py", "test_capability_consistency.py",
    "test_text_from_index.py", "test_dedupe.py", "test_ensure.py",
    "test_mcp.py", "test_mcp_compat.py", "test_access_live.py", "test_mcp_http.py",
    "test_db_index.py", "test_db_marks.py", "test_db_cross.py",
    "test_cli_ops_dispatch.py", "test_task_stats.py", "test_telemetry_facts.py",
    "test_catalog_state.py", "test_budget_stage.py", "test_evidence_lines.py",
    "test_structure_p1.py", "test_polyglot_p13.py", "test_p2_features.py",
    "test_docrole.py", "test_exclude_defaults.py", "test_auto_thresholds.py",
    "test_audit_report.py", "test_dedupe_prompt.py",
    # Проверки ядра, которым не нужны ни чужие корпуса, ни сервисы.
    "test_cli_agent_env.py", "test_codes_in_index.py", "test_csharp_records.py",
    "test_dedupe_stability.py", "test_doc_role_index.py", "test_drift_e2e.py",
    "test_exclude_configs.py", "test_excludes_summary.py", "test_init_project.py",
    "test_mcp_agent.py", "test_node_accounting.py", "test_ragraf_interpreter.py",
    "test_ragraf_mode.py", "test_script_extract.py", "test_signature_cut.py",
    "test_doc_layer.py",
    "test_doc_layer_office.py",
    "test_doc_layer_data.py",
    "test_doc_layer_archive.py",
    "test_doc_layer_mass.py",
    "test_telemetry_agent.py", "test_threshold.py", "test_watch.py",
    "test_state_isolation.py",
    # Этап 9: политика хранения. Набор герметичен (песочный корпус в %TEMP%,
    # векторы выключены, 27 с), поэтому идёт в ядро: удаление данных — это
    # то, что нельзя проверять «когда-нибудь потом».
    "test_storage_policy.py",
    # Этап 11: числа в документах. Набор был вне ВСЕХ групп, поэтому его краснота
    # никого не останавливала: он падал на неполной песочнице (не копировались
    # assets, файлы поставки и tools/surface_counts.py) и на литерале «54
    # operations», устаревшем после этапа 10. Проверка, которую никто не
    # запускает, — это проверка, которая врёт о состоянии проекта.
    "test_documented_numbers.py",
    # Этап 8: доказательность графа. Набор герметичен (песочный корпус в %TEMP%,
    # векторы выключены) и проверяет ровно то, ради чего этап затевался: вывод
    # не должен выглядеть фактом НИ В ОДНОМ ответе.
    "test_edge_evidence.py",
    # Этап 10: честность графа целиком. Набор герметичен (песочный корпус в
    # %TEMP%, векторы и CPG выключены, 33 свойства) и проверяет то, что нельзя
    # увидеть чтением: ни одно ребро не ведёт в отсутствующий узел, каждая
    # операция со связями называет вид доказательства либо прямо говорит, что
    # вопрос к ней не применяется, а порог достоверности задаётся вызовом.
    "test_graph_honesty.py",
    # Этап 12, 31.10.2026: механизм чистоты текста. Набор герметичен (песочница
    # в %TEMP%, реальный прогон инструмента только при его наличии) и проверяет
    # то, чего прежняя проверка не видела: двоичный файл и UTF-16 не считаются
    # «грязными», CRLF функциональных расширений не меняется, записи archive/**
    # и копии community/** не подаются инструменту, каталог отклоняется. Причина
    # — измеренный вред: нормализация ломала сверку по sha256 и RFC 4180 csv.
    "test_text_hygiene.py",
    # Этап 12, 31.10.2026: переиспользование векторов. Набор герметичен (временное
    # хранилище в %TEMP%) и закрепляет то, что нельзя увидеть чтением: строка vec0
    # не переписывается, если вектор уже посчитан, покрытие считается по chunk_id,
    # а не по метке ревизии, перенос вектора идёт запросом INSERT…SELECT, уборка
    # ревизии не удаляет векторы живых ревизий. Плюс страховка от возврата
    # соединения `vec_meta` с `vec_chunk` — оно стоило 16,98 с против 0,17 с.
    "test_vector_reuse.py",
    # Этап 12, находка 2: брошенная ревизия закрывается по признаку живости.
    # Набор герметичен (журнал в %TEMP%) и проверяет ровно то, что нельзя
    # проверить чтением кода: живая запись не закрывается, устаревшая —
    # закрывается однократно и с событием, запечатанная не трогается.
    "test_stale_revision.py",
    # Этап 13, 01.11.2026: одна сверка корпуса вместо трёх + лечение отпечатка.
    # Набор герметичен (временный корпус в %TEMP%) и закрепляет то, что нельзя
    # увидеть чтением: полная сверка даёт отпечаток без второго обхода дерева,
    # после доказанного равенства сумм отпечаток ЛЕЧИТСЯ (иначе дешёвый путь
    # умирает навсегда и каждая операция читает весь корпус), прогон, не
    # закрепивший ревизию, в реестр не пишется.
    "test_watch_single_scan.py",
    # Этап 13: контракт замера пользы. Проверяется не «поиск работает», а то, что
    # замер считает ТО ЖЕ, что продукт: материал хита из реестра форматов, микс
    # равен сумме, подсказка code_candidate — только при отсутствии кода в выдаче,
    # арифметика метрик на синтетике и отказ наборе, противоречащем корпусу.
    "test_usefulness_contract.py",
    # Этап 14: границы безопасности. Набор герметичен (хранилище и реестр в
    # %TEMP%) и проверяет отрицательные утверждения — их нельзя подтвердить
    # чтением: уровень доступа отказывает ДО обработчика, значение секрета не
    # уходит в list/show, каждая выдача и каждый отказ попадают в аудит.
    "test_secret_isolation.py",
    # Этап 15: установка с нуля. Состояние не пишется внутрь каталога
    # интерпретатора (проверяется на воспроизведённом раскладе установки),
    # `ensure --no-vectors` доходит до индексатора, а команды `ragraf-mcp`
    # и `ragraf-admin` объявлены и ведут в пакет, а не в `tools/`.
    "test_install_layout.py",
    "test_install_from_scratch.py",
    # Этап 15, находка из вопроса этапа 14. Набор герметичен (песочный корпус
    # и свой каталог состояния в %TEMP%) и закрепляет то, что нельзя увидеть
    # чтением: ревизия без живого слоя баз НАЗЫВАЕТ причину в своей записи.
    # Замер 01.11.2026: rev-1193, rev-1195, rev-1204, rev-1221 показывали
    # `databases items=0` без объяснения, и разбирать их пришлось вручную.
    "test_db_layer_visibility.py",
    # Этап 1 плана «Агентские интеграции» (01.11.2026): фундамент LLM-слоя.
    # Набор герметичен (песочный корпус и свой каталог состояния в %TEMP%,
    # поддельный транспорт вместо сети) и закрепляет пять утверждений, которые
    # нельзя проверить чтением: отказ вместо пустого ответа, устойчивый
    # отпечаток, отсутствие второго платного вызова, факт без упоминания
    # отвергается, ключ не попадает ни в ответ, ни в файл настроек.
    "test_docgraph_foundation.py",
    # Этап 2 (01.11.2026): извлечение фактов из документов. Набор герметичен
    # (песочный корпус и свой каталог состояния в %TEMP%, поддельный транспорт
    # вместо сети) и закрепляет то, что нельзя увидеть чтением: повторный прогон
    # не делает ни одного платного вызова и оставляет слой побайтово тем же,
    # цитата вне чанка отвергается кодом, смена версии промпта инвалидирует кэш,
    # отказ провайдера на середине не оставляет полузаписанного слоя.
    "test_docgraph_extract.py",
    # Этап 3 (01.11.2026): две операции чтения документного графа и гейт изданий.
    # Набор закрепляет то, чего не видно чтением: у каждого факта в ответе есть
    # адрес (у пары документов — с обеих сторон), факт без адреса считается в
    # rejected_no_citation, а не теряется молча; под Community обе операции
    # отвечают EditionRefusal с названием издания, под разрешённой возможностью —
    # исполняются; depth/limit вне диапазона дают код ошибки вызова; набор
    # инструментов MCP, их описания и обработчики совпадают; проход извлечения
    # закрыт той же возможностью, что и чтение.
    "test_docgraph_ops.py",
    # Этап 3.1 (01.11.2026): числа навыка сверяются с кодом. Набор появился из
    # измеренного дефекта: навык заявлял «39 операций / 41 инструмент» при 64 и 71,
    # а правило чисел в check_skill_variants.py применялось только к копиям файлов
    # с режимом declared — файлы identical сравнивались лишь побайтово, и копии
    # сходились в неверном числе. Плюс шаблон не видел формы «проверок».
    # Набор работает на песочнице (--root) и подсадками доказывает оба случая.
    "test_skill_numbers.py",
    # Этап 4 (01.11.2026): dual-level поиск — `mode` у `search`, уровень «тема» из
    # документного графа. Набор закрепляет то, чего не видно чтением: `auto`
    # остаётся прежним поведением (те же хиты, те же оценки), уровень «тема» только
    # ДОБАВЛЯЕТ хиты с адресами и не переставляет лексические, ключи запроса
    # кэшируются (второй запрос не платит), а деградация названа полем `degraded` с
    # причиной: нет модели, нет слоя, нет издания, темы не собраны.
    "test_docgraph_search.py",
    # Этап 5 (02.11.2026): темы и карта документного графа. Набор герметичен
    # (песочный корпус, свой каталог состояния, поддельные провайдеры) и закрепляет
    # то, чего не видно чтением: theme_id вычислим и стабилен, у каждого участника
    # адрес, сводка без цитат не выдаётся и не пишется, «тем нет» отличается от
    # «слой не собран», а обе операции закрыты возможностью doc-graph.
    "test_docgraph_themes.py",
    # HARNESS 44.7.0 (02.11.2026): поддержка моста хуков.
    # Набор закрепляет три измеренных дефекта: команда хука начиналась с кавычки
    # (PowerShell: ParserError, код 1), вывод падал UnicodeEncodeError на cp1252
    # (и fail-open сообщал «вызов разрешён»), а DEFAULT_MODE расходился с ядром.
    # Плюс то, что нельзя увидеть чтением: моста и протокола нет в сборке, установка
    # идёт командой клиента и требует хранилища pnpm профиля.
    "test_harness_bridge.py",
    # Интеграция Claude Code (02.11.2026): установка, идемпотентность, откат, хуки.
    # Набор герметичен (свой каталог настроек в %TEMP%, `--home`) и закрепляет то,
    # чего не видно чтением: второй запуск не меняет файл продукта и не плодит
    # копии, чужие хуки и правила остаются целыми, откат снимает только наше,
    # `SessionStart` отвечает по протоколу клиента, метка задачи ставится первым
    # запросом и не меняется вторым. Подсадки (пропало событие, снята
    # дедупликация, раздел дописывается всегда) обязаны давать отклонение.
    "test_claude_code_integration.py",
]

#: Нужны другие корпуса на этой машине (путь и наличие — не наша зона).
EXTERNAL = [
    "test_css_layer.py", "test_class_usage.py", "test_report_facts.py",
    "test_dedupe_effect.py", "test_rdpmon_ops.py", "test_sagaai_catalog.py",
    "test_sagaai_function.py", "test_sagaai_instruction.py",
    "test_sagaai_page_loads.py", "test_sagaai_skill.py", "test_task_stats_sagaai.py",
    "test_watch_live.py", "test_agents_prompt.py", "test_excludes_prompt.py",
    "test_dangerous_fix_final.py", "test_new_langs_indexed.py",
    "test_slash_ragraf.py", "test_fixes_package.py",
]

#: Нужен запущенный сервис, внешний процесс или устройство.
SERVICE = [
    "check_admin_agents.py", "check_admin_dedupe.py", "test_gpu_backend.py",
    "test_gpu_settings.py", "test_gpu_threshold.py", "test_joern_names.py",
    "test_joern_server.py", "test_joern_speed.py", "test_server_reuse.py",
    "test_server_settings.py", "test_server_speed.py", "test_agent_stats_live.py",
    "test_cpg_carry.py", "test_sagaai_child_output_encoding.py",
]


def discover() -> list[str]:
    return sorted(p.name for p in (ROOT / "tests").glob("*.py"))


def group_of(name: str) -> str:
    if name in CORE:
        return "core"
    if name in EXTERNAL:
        return "external"
    if name in SERVICE:
        return "service"
    return "other"


#: Файлы живого состояния, за которыми следит раннер: прогон проверок не
#: должен менять состояние, на котором сам же и проверяет (этап 7).
LIVE_STATE = ("settings.json", "catalog.json", "registry_history.jsonl",
              "sources.json", "access_tokens.json", "db_access.json")


#: Что в файле живого состояния СОДЕРЖАТЕЛЬНО, без подвижных отметок времени.
#: `Catalog.save()` двигает `updated_at` при каждом сохранении — в том числе
#: когда сохраняет ДРУГОЙ процесс (индексируется другой корпус, отвечает
#: долгоживущий MCP-сервер на том же реестре). Замер 31.10.2026: два прогона
#: подряд назвали разных виновников (`test_mcp_compat`, `test_doc_layer_archive`),
#: а поодиночке ни один из них живой реестр не трогал. Обвинять набор за чужую
#: запись — ложная улика: сторож должен называть смену СОСТАВА (появилась
#: тестовая разработка, изменился признак индексации), а сдвиг даты называть
#: отдельно и не валить прогон.
VOLATILE_KEYS = ("updated_at",)


def live_state_semantics(path: Path) -> str | None:
    """Содержательная часть файла состояния: без подвижных отметок времени.

    У реестра разработок сравнивается СОСТАВ (имена записей), а не подробности
    каждой: чужой процесс, переиндексировавший свой корпус, законно меняет
    `rev_id`, `indexed_at` и `runs` — и это не дело набора. Что набором
    действительно считается нарушением — появление или исчезновение
    разработки (например, тестовой пробы) в живом реестре.
    """
    import hashlib
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        for key in VOLATILE_KEYS:
            data.pop(key, None)
        entries = data.get("entries")
        if isinstance(entries, dict):
            # Только имена: подробности записи двигает тот, кто её индексирует.
            data["entries"] = sorted(entries)
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


def live_state_digest() -> dict[str, str]:
    """Контрольные суммы живого `catalog/`: снимок до и после каждого набора."""
    import hashlib

    base = ROOT / "catalog"
    out: dict[str, str] = {}
    for name in LIVE_STATE:
        path = base / name
        if path.exists():
            out[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def live_state_semantic_digest() -> dict[str, str]:
    """Содержательные отпечатки живого `catalog/`: без подвижных отметок."""
    base = ROOT / "catalog"
    out: dict[str, str] = {}
    for name in LIVE_STATE:
        path = base / name
        if path.exists():
            value = live_state_semantics(path)
            if value is not None:
                out[name] = value
    return out


def suite_state_dir(name: str) -> Path:
    """Каталог состояния ОДНОГО набора: копия реестра, а не живой файл.

    Замер 30.10.2026: одиннадцать наборов из пятидесяти четырёх писали в живой
    `catalog/` — через CLI, MCP-сервер и админ-панель, которые наследуют
    окружение процесса. Поэтому раннер даёт каждому набору свой каталог
    состояния: содержимое живого реестра копируется туда (набор видит те же
    разработки), а записи уходят в копию и убираются вместе с ней.
    """
    import tempfile

    base = Path(tempfile.mkdtemp(prefix=f"ragraf-state-{name.replace('.py', '')}-"))
    live = ROOT / "catalog"
    for fname in LIVE_STATE:
        source = live / fname
        if source.exists():
            try:
                shutil.copy2(source, base / fname)
            except OSError:
                pass
    return base


#: Наборы, которым общего предела мало (см. `run_one`). Замер 01.11.2026:
#: test_cli_ops_dispatch — 27 проверок за ≈540 с при общем пределе 300 с, то есть
#: исправный набор получал вердикт «провалено».
SLOW_TIMEOUTS: dict[str, int] = {"test_cli_ops_dispatch.py": 900}


def run_one(name: str, timeout: int) -> dict:
    started = time.perf_counter()
    # Предел для МЕДЛЕННОГО набора: он запускает четыре десятка процессов CLI,
    # и каждый поднимает модель эмбеддингов. Предел задаётся ИМЕНИ, а не всему
    # раннеру: поднять общий — значит спрятать зависание остальных наборов.
    timeout = SLOW_TIMEOUTS.get(name, timeout)
    live_before = live_state_digest()
    state_dir = suite_state_dir(name)
    env = dict(os.environ, RAGRAF_CATALOG_DIR=str(state_dir))
    try:
        proc = subprocess.run([PYTHON, str(ROOT / "tests" / name)], cwd=str(ROOT),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env)
        out = (proc.stdout or "") + (proc.stderr or "")
        code = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        out = str(exc.stdout or "") + str(exc.stderr or "")
        code = -1
        timed_out = True
    seconds = time.perf_counter() - started
    # Пауза перед снимком: дочерний сервер набора может дописать состояние
    # через секунду после выхода процесса, и без паузы запись приписывалась
    # СЛЕДУЮЩЕМУ набору (замер 30.10.2026, второй прогон).
    time.sleep(2.0)
    live_after = live_state_digest()
    shutil.rmtree(state_dir, ignore_errors=True)
    state_left = state_dir.exists()
    live_changed_all = sorted(key for key in set(live_before) | set(live_after)
                              if live_before.get(key) != live_after.get(key))
    # Разделение: смена состава — провал прогона; сдвиг только отметки времени —
    # запись ДРУГОГО процесса, и набор за неё не отвечает.
    semantic_before = live_state_semantic_digest()
    live_changed = [key for key in live_changed_all
                    if semantic_before.get(key) != live_state_semantic_digest().get(key)]
    live_touched = [key for key in live_changed_all if key not in live_changed]
    match = re.search(r"пройдено[:\s]+(\d+)[^\n]*?провалено[:\s]+(\d+)", out)
    passed = int(match.group(1)) if match else 0
    failed = int(match.group(2)) if match else 0
    tail = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    return {
        "name": name, "exit": code, "seconds": round(seconds, 2),
        "timed_out": timed_out, "passed": passed, "failed": failed,
        "summary": tail[-1][:100] if tail else "",
        "live_changed": live_changed,
        # Файлы, у которых сдвинулась только отметка времени: набор их не менял.
        "live_touched": live_touched,
        "state_left": state_left,
        # Хвост вывода нужен только при провале: без него причина остаётся
        # внутри захваченного вывода, и разбор начинается с догадок
        # (замер 30.10.2026: провал test_ensure 3/17 при одиночном 17/0).
        "tail_output": "\n".join((out or "").splitlines()[-40:]),
    }


def take_sandbox_snapshot() -> set[str]:
    """Имена каталогов `ragraf*` во временном каталоге — до прогона."""
    temp = os.environ.get("TEMP") or os.environ.get("TMP") or ""
    root = Path(temp)
    if not temp or not root.is_dir():
        return set()
    try:
        return {item.name for item in root.iterdir()
                if item.is_dir() and item.name.startswith("ragraf")}
    except OSError:
        return set()


def drop_new_sandboxes(before: set[str], *, keep: bool) -> tuple[int, int]:
    """Убрать каталоги, появившиеся за прогон. Возвращает (удалено, осталось неубранных).

    Удаляется только НОВОЕ и только с префиксом `ragraf`: каталог, который был до прогона,
    не трогается ни при каких условиях — иначе раннер убирал бы чужие песочницы, снятые для
    разбора вручную.
    """
    temp = os.environ.get("TEMP") or os.environ.get("TMP") or ""
    root = Path(temp)
    if not temp or not root.is_dir():
        return 0, 0
    appeared = []
    try:
        for item in root.iterdir():
            if not item.is_dir() or not item.name.startswith("ragraf"):
                continue
            if item.name in before:
                continue
            appeared.append(item)
    except OSError:
        return 0, 0
    if keep:
        return 0, len(appeared)
    removed = 0
    for item in appeared:
        shutil.rmtree(item, ignore_errors=True)
        if not item.exists():
            removed += 1
    return removed, len(appeared) - removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Раннер проверок RAGRAF")
    parser.add_argument("--group", default="core",
                        choices=["core", "external", "service", "all"],
                        help="какие проверки запускать (по умолчанию ядро)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="предел на одну проверку, секунд")
    parser.add_argument("--keep-sandboxes", action="store_true",
                        help="не убирать временные каталоги, созданные прогоном")
    parser.add_argument("--list", action="store_true",
                        help="показать состав групп и не запускать")
    args = parser.parse_args()

    found = discover()
    if args.list:
        for group in ("core", "external", "service", "other"):
            names = [n for n in found if group_of(n) == group]
            print(f"{group}: {len(names)}")
            for name in names:
                print(f"  {name}")
        unknown = [n for n in found if group_of(n) == "other"]
        if unknown:
            print("\nВНЕ ГРУПП (укажите группу в раннере):")
            for name in unknown:
                print(f"  {name}")
        return 0

    if args.group == "all":
        selected = found
    else:
        selected = [n for n in found if group_of(n) == args.group]
    if not selected:
        print(f"проверок в группе {args.group} не найдено")
        return 1

    print(f"группа: {args.group} | проверок: {len(selected)} | предел на "
          f"проверку: {args.timeout} с")
    # Снимок песочниц ДО прогона: наборы создают каталоги во временном хранилище, и не все
    # убирают их сами (замер 29.10.2026: 3838 каталогов на 17,7 ГБ, из них `ragraf-arch-*`
    # — по 16 за три часа). Убирается только появившееся за прогон.
    sandboxes_before = take_sandbox_snapshot()
    results = []
    started = time.perf_counter()
    for name in selected:
        result = run_one(name, args.timeout)
        results.append(result)
        if result["timed_out"]:
            flag = "ПРЕДЕЛ"
        elif result["exit"] == 0:
            flag = "ok"
        else:
            flag = f"ПРОВАЛ({result['exit']})"
        print(f"  {flag:12} {name:42} {result['seconds']:7.1f} с  "
              f"{result['passed']} пройдено, {result['failed']} провалено",
              flush=True)
        if result.get("live_changed"):
            # Живое состояние изменилось за набор: это находка, а не мелочь —
            # следующий прогон начнётся с другого состояния (этап 7).
            print("      | живое состояние изменено: "
                  + ", ".join(result["live_changed"]))
        if result.get("live_touched"):
            # Сдвинулась ТОЛЬКО отметка времени. `Catalog.save()` двигает её при
            # любом сохранении, в том числе от чужого процесса на том же реестре
            # (долгоживущий MCP-сервер). Набору это не приписывается.
            print("      | отметка времени обновлена другим процессом: "
                  + ", ".join(result["live_touched"]))
        if result["exit"] != 0:
            # Хвост вывода провалившегося набора: без него причина не видна.
            for line in (result.get("tail_output") or "").splitlines():
                print(f"      | {line[:160]}")

    total_seconds = time.perf_counter() - started
    dropped, left = drop_new_sandboxes(sandboxes_before,
                                       keep=bool(getattr(args, "keep_sandboxes", False)))
    if dropped or left:
        print(f"песочниц убрано: {dropped}" + (f", осталось: {left}" if left else ""))
    ok = [r for r in results if r["exit"] == 0]
    bad = [r for r in results if r["exit"] != 0]
    checks_passed = sum(r["passed"] for r in results)
    checks_failed = sum(r["failed"] for r in results)
    print()
    print("=" * 78)
    print(f"ИТОГ: файлов {len(results)}, прошло {len(ok)}, провалено {len(bad)} | "
          f"проверок {checks_passed} пройдено, {checks_failed} провалено | "
          f"{total_seconds:.1f} с")
    for result in bad:
        note = result["summary"] or ("предел времени" if result["timed_out"] else "")
        print(f"  ПРОВАЛ: {result['name']}: {note}")
    stale_states = [r["name"] for r in results if r.get("state_left")]
    if stale_states:
        print(f"каталоги состояния не убраны ({len(stale_states)}): "
              + ", ".join(stale_states))
    writers = [r for r in results if r.get("live_changed")]
    if writers:
        print(f"ЖИВОЕ СОСТОЯНИЕ ИЗМЕНЕНО наборами ({len(writers)}): "
              + "; ".join(f"{r['name']} [{', '.join(r['live_changed'])}]"
                          for r in writers))
    else:
        touched = [r["name"] for r in results if r.get("live_touched")]
        if touched:
            print("живое состояние: состав не изменён; отметку времени двигал "
                  f"другой процесс ({len(touched)} набор(ов) в окне: "
                  + ", ".join(touched) + ")")
        else:
            print("живое состояние: ни один набор не изменил catalog/*")
    print("Строка для отчёта: "
          f"проверок {len(results)}: пройдено {len(ok)}, провалено {len(bad)}")
    print("Полный вердикт = этот раннер + "
          "tools/Invoke-ProjectDoctrine.ps1 (конвенции исходников)")
    print("=" * 78)
    return 0 if not bad and not writers else 1


if __name__ == "__main__":
    raise SystemExit(main())
