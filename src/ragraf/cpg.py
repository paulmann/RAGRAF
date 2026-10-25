"""Слой CPG: вызов Joern для получения межпроцедурных связей и потоков данных.

Зачем отдельный слой, если уже есть tree-sitter:
  * tree-sitter даёт синтаксис и локальный контекст — этого достаточно для навигации;
  * срезы по PDG и межпроцедурные потоки данных требуют полноценного CPG
    (AST+CFG+DFG+PDG), который строит Joern. Именно на срезах основан эффект
    сжатия контекста, ради которого заказчик разрешил JVM.

Режим degradation (архитектура §6.4): если Joern/JDK недоступны или сборка упала,
ревизия помечается `capability=degraded`, операции `slice` и `flow` отказывают с
причиной. Молчаливой деградации не бывает: причина попадает в отчёт и в coverage.

Внешняя зависимость изолирована в этом модуле: остальной код не знает про Joern.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import util
from .config import ProjectConfig
from .joern_export import is_global_owner, normalize_joern_name, normalize_joern_path

# --- расположение инструментов ------------------------------------------------


def tools_root(cfg: ProjectConfig) -> Path:
    """Каталог .tools: рядом с проектом RAGRAF (не внутри индексируемого корпуса)."""
    return Path(__file__).resolve().parents[2] / ".tools"


def _needs_shell(script: Path) -> bool:
    """Нужен ли shell для запуска скрипта.

    Проверено прогоном: `.bat` в Windows требует cmd, иначе аргументы (в т.ч. -J-Xmx)
    передаются неправильно — JVM жалуется «Too small maximum heap» или игнорирует
    лимит. На Linux такого требования нет.
    """
    return script.suffix.lower() in (".bat", ".cmd")


def _shell_cmd(cmd: list[str], script: Path) -> str | list[str]:
    """Подготовить команду к запуску.

    `.bat` и `.cmd` в Windows обязан исполнять `cmd.exe`: без него аргументы
    (в том числе `-J-Xmx`) доходят искажённо, и JVM сообщает «Too small maximum
    heap» либо игнорирует лимит. Остальные скрипты запускаются списком, без
    оболочки вовсе — там нечего экранировать.

    Может поднять `util.CmdArgumentError`: это не ошибка программы, а измеренная
    невозможность передать такой аргумент через cmd (см. `util.CMD_UNSAFE`).
    Формулировка «экранировать» здесь не работает: `%` в командной строке cmd
    не отключается ни кавычками, ни косой чертой.
    """
    if script.suffix.lower() not in (".bat", ".cmd"):
        return cmd
    return util.cmd_command(cmd)


def _stderr_digest(text: str | None, head: int = 900, tail: int = 700) -> str | None:
    """Начало и конец вывода Joern — причина отказа и след.

    Почему обе части. Причина у Joern почти всегда в НАЧАЛЕ вывода
    («ExceptionInInitializerError: Cannot unpack libzstd-jni…: Access is
    denied», замер 16.10.2026), а конец — стек вызовов. Прежняя редакция брала
    только хвост, и заметка индексации показывала трассировку без причины.
    """
    if not text:
        return None
    # JVM печатает «Picked up JAVA_TOOL_OPTIONS: …» первой строкой. Это не
    # причина отказа, но она попадала в заметку индексации и сдвигала начало
    # вывода, ради которого заметка и собирается.
    text = "\n".join(
        line for line in str(text).splitlines()
        if not line.strip().startswith("Picked up JAVA_TOOL_OPTIONS")
    ).strip()
    if len(text) <= head + tail:
        return text
    return f"{text[:head]}\n…\n{text[-tail:]}"


def _heap_arg(cfg: ProjectConfig) -> str | None:
    """Аргумент лимита памяти JVM в форме, которую понимает обёртка Joern.

    Форма без знака '=' — проверено: `-J-Xmx=4g` даёт «Invalid maximum heap size»,
    а `-J-Xmx4g` принимается.

    Источник значения один — настройка корпуса `index.joern_heap_gb`, переменная
    окружения RAGRAF_JOERN_HEAP_GB только перекрывает её. Прежняя версия держала
    здесь зашитое значение 18 ГБ, тогда как серверный режим брал 8 ГБ из конфига:
    одна и та же JVM получала разные лимиты в зависимости от пути запуска. Это
    расхождение устранено — решение владельца: 8 ГБ (см. `config.py`).

    Про 18 ГБ и почему его здесь больше нет. Такое значение требовалось экспорту
    GraphSON, который собирает один огромный JSON и падал с OutOfMemoryError. Этот
    формат в проекте не используется: экспорт идёт потоковым `neo4jcsv`
    (`joern_export.py`), которому большого лимита не нужно. Значение по-прежнему
    можно поднять через RAGRAF_JOERN_HEAP_GB, не меняя код.
    """
    raw = os.environ.get("RAGRAF_JOERN_HEAP_GB")
    if raw is None or not str(raw).strip():
        raw = str(getattr(cfg.index, "joern_heap_gb", 0) or 8)
    try:
        value = int(str(raw).strip())
    except ValueError:
        value = 8
    return f"-J-Xmx{value}g" if value > 0 else None


def find_java(cfg: ProjectConfig) -> Path | None:
    """Java: сначала переменная окружения, затем .tools/jdk, затем PATH."""
    candidates: list[Path] = []
    env = os.environ.get("RAGRAF_JAVA_HOME")
    if env:
        candidates.append(Path(env) / "bin" / "java.exe")
    candidates.append(tools_root(cfg) / "jdk" / "bin" / "java.exe")
    for p in candidates:
        if p.exists():
            return p
    found = shutil.which("java")
    return Path(found) if found else None


def find_php() -> Path | None:
    """PHP CLI. Нужен обязательно: php2cpg не имеет своего парсера PHP.

    Проверено по исходникам фронтенда: PhpParser.scala запускает
    `php --php-ini <tmp> <php-parser.phar> --with-recovery --resolve-names --json-dump`,
    a Php2Cpg.createCpg вызывает `php --version` и требует версию >= 7.1.
    Без php в PATH фронтенд не стартует вообще (это не деградация, а отказ).
    """
    found = shutil.which("php")
    if found:
        return Path(found)
    env = os.environ.get("RAGRAF_PHP_BIN")
    if env and Path(env).exists():
        return Path(env)
    for candidate in (Path("C:/tools/php85/php.exe"), Path("C:/tools/php/php.exe")):
        if candidate.exists():
            return candidate
    return None


def php_env(cfg: ProjectConfig) -> dict[str, str]:
    """Окружение для запуска php2cpg: php обязательно ДОЛЖЕН быть как `php` в PATH.

    Фронтенд вызывает буквально `Seq("php", ...)`, поэтому полный путь не подходит:
    подкладываем каталог с php в начало PATH.
    """
    env = java_env(cfg)
    php = find_php()
    if php is not None:
        env["PATH"] = str(php.parent) + os.pathsep + env.get("PATH", "")
    return env


def find_joern_scripts(cfg: ProjectConfig) -> dict[str, Path]:
    """Исполняемые скрипты Joern: joern, joern-parse, joern-export и т.п.

    Ищутся в .tools/joern-cli (с подкаталогом или без) и по переменной окружения.
    """
    bases: list[Path] = []
    env = os.environ.get("RAGRAF_JOERN_HOME")
    if env:
        bases.append(Path(env))
    root = tools_root(cfg) / "joern-cli"
    bases.append(root)
    bases.append(root / "joern-cli")

    out: dict[str, Path] = {}
    names = ("joern-parse", "joern", "joern-export", "joern-scan", "php2cpg",
             # Парсер Python: у Joern 4.0.99 он отдельный и даже умеет
             # --exclude. Без него граф вызовов по Python не строится,
             # и индексация заканчивается статусом degraded.
             "pysrc2cpg")
    for base in bases:
        if not base.exists():
            continue
        for name in names:
            if name in out:
                continue
            for suffix in (".bat", ".cmd", ""):
                candidate = base / f"{name}{suffix}"
                if candidate.exists() and candidate.is_file():
                    out[name] = candidate
                    break
    return out


def _extract_script_body(script_text: str, cpg_file: str = "",
                         out_file: str = "") -> str | None:
    """Подготовить скрипт CPGQL к выполнению через сервер.

    ПОЧЕМУ ПОДСТАНОВКА, А НЕ ПЕРЕПИСЫВАНИЕ ЛОГИКИ.
    Первая версия пыталась убрать запись в файл и вернуть значение прямо
    из запроса. Это оказалось неверно: `ujson.write(x)` — функция записи
    в поток, а не получения строки, и вместо данных вернулся огрызок
    (680 символов вместо тысяч).

    Теперь логика скрипта почти не меняется: значения параметров подставляются
    в текст, а результат скрипт кладёт в тот же файл, что и при запуске
    отдельным процессом. Значит вызывающий код читает файл как обычно,
    и поведение полностью совпадает.

    ЕДИНСТВЕННОЕ ИЗМЕНЕНИЕ: строка `importCpg(...)` УБИРАЕТСЯ.
    Граф загружается один раз до запроса, и повторная загрузка внутри скрипта
    его ЛОМАЕТ: в Joern 4.0.99 повторный вызов importCpg падает с
    NullPointerException в XTypeRecoveryPassGenerator, после чего граф в сессии
    остаётся пустым и данные не выдаются. Это воспроизведено вызовом:
    тот же скрипт с importCpg файла не создавал, без него — создавал.

    Возвращается None, если скрипт не разобран: тогда работает прежний путь
    через отдельный процесс.
    """
    import re as _re

    match = _re.search(
        r"@main\s+def\s+\w+\s*\([^)]*\)\s*=\s*\{(.*)\}\s*$",
        script_text, _re.DOTALL,
    )
    if not match:
        return None
    body = match.group(1).strip()
    if not body:
        return None

    # Убирается повторная загрузка графа: она ломает сессию сервера.
    body = _re.sub(r"^\s*importCpg\([^)]*\)\s*$", "", body, flags=_re.MULTILINE)

    # Пути подставляются в прямых слешах: обратные — escape-символы в Scala.
    cpg_posix = str(cpg_file).replace("\\", "/")
    out_posix = str(out_file).replace("\\", "/")

    # Подстановка вместо переменных скрипта. Обрабатываются три формы записи:
    # в двойных кавычках, в одинарных и без кавычек.
    for name, value in (("cpgFile", cpg_posix), ("outFile", out_posix)):
        body = _re.sub(rf'"\$?{name}"', f'"{value}"', body)
        body = _re.sub(rf"'\$?{name}'", f'"{value}"', body)
        body = _re.sub(rf"\b{name}\b", f'"{value}"', body)

    return body.strip() or None


def joern_tmp(cfg: ProjectConfig) -> Path:
    """Личный временный каталог JVM для Joern.

    Зачем не общий `%TEMP%`. Зависимость Joern `zstd-jni` распаковывает нативную
    библиотеку в `java.io.tmpdir` при КАЖДОМ запуске JVM и не убирает её за
    собой. Замер 23.10.2026 на этой машине: в `AppData/Local/Temp` накопилось
    **1532** файла `libzstd-jni-*.dll` общим объёмом **1,73 ГБ**, и с 21.10
    распаковка перестала работать — `Cannot unpack libzstd-jni-1.5.6-3: Access
    is denied`. Следствие было не «нет CPG», а тихое «рёбра перенесены из
    прошлой ревизии»: capability остаётся `full`, связи нового кода теряются.

    Каталог живёт внутри `.ragraf` (он исключён из индексации базовым списком),
    поэтому мусор не попадает ни в корпус, ни в общий temp, а права и
    содержимое предсказуемы.
    """
    directory = cfg.ragraf_dir / "tmp" / "joern"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def prune_joern_tmp(cfg: ProjectConfig) -> int:
    """Убрать распакованные библиотеки прошлых запусков. Возвращает число файлов.

    Удаляются только файлы `libzstd-jni-*` в НАШЕМ каталоге: они появляются за
    один запуск JVM и не нужны после него. Занятый другим процессом файл не
    удалится — это штатно и не мешает.
    """
    removed = 0
    for item in joern_tmp(cfg).glob("libzstd-jni-*"):
        try:
            item.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def java_env(cfg: ProjectConfig) -> dict[str, str]:
    """Переменные окружения для запуска Joern с нашим JDK."""
    env = dict(os.environ)
    java = find_java(cfg)
    if java is not None:
        java_home = str(java.parent.parent)
        env["JAVA_HOME"] = java_home
        env["PATH"] = str(java.parent) + os.pathsep + env.get("PATH", "")
    # `JAVA_TOOL_OPTIONS` действует на КАЖДУЮ JVM, включая те, которые поднимают
    # bat-скрипты Joern (joern-parse, joern-export): их командную строку мы не
    # контролируем, а переменную они наследуют. Прежнее значение не затирается.
    flag = f"-Djava.io.tmpdir={joern_tmp(cfg)}"
    existing = env.get("JAVA_TOOL_OPTIONS", "").strip()
    env["JAVA_TOOL_OPTIONS"] = f"{existing} {flag}".strip()
    return env


# --- состояние слоя ------------------------------------------------------------


@dataclass
class CpgStatus:
    available: bool
    reason: str | None
    joern_parse: str | None = None
    joern: str | None = None
    java: str | None = None
    php: str | None = None

    def describe(self) -> str:
        if self.available:
            return f"CPG доступна (joern-parse: {self.joern_parse}, php: {self.php})"
        return f"CPG недоступна: {self.reason}"


@dataclass
class CpgExport:
    ok: bool
    path: Path | None
    seconds: float
    note: str | None = None
    nodes_csv: Path | None = None
    rels_csv: Path | None = None


@dataclass
class CpgResult:
    ok: bool
    cpg_path: Path | None
    seconds: float
    nodes: int = 0
    edges: int = 0
    cfg_edges: int = 0
    dfg_edges: int = 0
    call_edges: int = 0
    note: str | None = None
    stderr_tail: str | None = None
    stages: list[dict] = field(default_factory=list)


# --- подготовка скрипта запросов ----------------------------------------------

# Скрипты запросов. Формат обязателен: скриптовый режим Joern требует точку входа
# @main def exec(...), а параметры приходят через --param (документация docs/interpreter).
# Результат пишется оператором `#>` в файл, который читает наш код.
#
# ВАЖНО: эти скрипты выполняются ТОЛЬКО нашим конвейером. Агенту язык запросов
# недоступен (ФТ-15) — наружу выставляются фиксированные операции фасада.

QUERY_CALLS = '''
@main def exec(cpgFile: String, outFile: String) = {
  importCpg(cpgFile)
  val rows = cpg.call.map { c =>
    ujson.Obj(
      "srcMethod" -> c.method.fullName,
      "srcFile" -> c.file.name.headOption.getOrElse(""),
      "line" -> c.lineNumber.getOrElse(-1),
      "callee" -> c.methodFullName,
      "calleeName" -> c.name,
      "dispatchType" -> c.dispatchType,
      "code" -> c.code.take(200)
    )
  }.l
  os.write.over(os.Path(outFile), ujson.write(ujson.Arr(rows.toSeq*)))
}
'''

QUERY_METHODS = '''
@main def exec(cpgFile: String, outFile: String) = {
  importCpg(cpgFile)
  val rows = cpg.method.map { m =>
    ujson.Obj(
      "name" -> m.name,
      "fullName" -> m.fullName,
      "filename" -> m.filename,
      "lineNumber" -> m.lineNumber.getOrElse(-1),
      "lineNumberEnd" -> m.lineNumberEnd.getOrElse(-1),
      "isExternal" -> m.isExternal,
      "params" -> m.parameter.size
    )
  }.l
  os.write.over(os.Path(outFile), ujson.write(ujson.Arr(rows.toSeq*)))
}
'''

QUERY_TYPES = '''
@main def exec(cpgFile: String, outFile: String) = {
  importCpg(cpgFile)
  val rows = cpg.typeDecl.map { t =>
    ujson.Obj(
      "name" -> t.name,
      "fullName" -> t.fullName,
      "filename" -> t.filename,
      "lineNumber" -> t.lineNumber.getOrElse(-1)
    )
  }.l
  os.write.over(os.Path(outFile), ujson.write(ujson.Arr(rows.toSeq*)))
}
'''


class JoernRunner:
    """Обёртка над Joern: построение CPG и экспорт связей."""

    def __init__(self, cfg: ProjectConfig) -> None:
        self.cfg = cfg
        self.scripts = find_joern_scripts(cfg)
        self.java = find_java(cfg)
        self.php = find_php()
        self.workdir = cfg.ragraf_dir / "cpg"
        # Лимит памяти JVM передаётся аргументом -J-Xmx: JAVA_OPTS до JVM не доходит.
        # Источник значения тот же, что у серверного режима (см. _heap_arg):
        # настройка корпуса, которую перекрывает переменная окружения.
        raw_heap = os.environ.get("RAGRAF_JOERN_HEAP_GB")
        if raw_heap is None or not str(raw_heap).strip():
            raw_heap = str(getattr(cfg.index, "joern_heap_gb", 0) or 8)
        try:
            self.heap = int(str(raw_heap).strip())
        except ValueError:
            self.heap = 8
        self._stderr_tail: list[str] = []

    # --- доступность ---

    def status(self) -> CpgStatus:
        if not self.cfg.index.joern_enabled:
            return CpgStatus(False, "CPG отключена в конфигурации (index.joern_enabled=false)")
        if self.java is None:
            return CpgStatus(False, "JDK не найден: ожидается .tools/jdk или RAGRAF_JAVA_HOME")
        if self.php is None:
            return CpgStatus(
                False,
                "PHP CLI не найден: php2cpg использует внешний php-parse и без php не стартует",
                java=str(self.java),
            )
        if "joern-parse" not in self.scripts and "joern" not in self.scripts:
            return CpgStatus(
                False,
                f"скрипты Joern не найдены в {tools_root(self.cfg) / 'joern-cli'}",
                java=str(self.java),
                php=str(self.php),
            )
        return CpgStatus(
            available=True,
            reason=None,
            joern_parse=str(self.scripts.get("joern-parse")),
            joern=str(self.scripts.get("joern")),
            java=str(self.java),
            php=str(self.php),
        )

    # --- построение CPG ---

    def build(self, rev_id: str, timeout: int = 7200) -> CpgResult:
        """Построить CPG по исходникам корпуса.

        Вызывается как отдельный процесс: падение Joern (частый случай на
        нестандартном PHP) не должно ронять конвейер индексации.
        """
        started = time.perf_counter()
        status = self.status()
        if not status.available:
            return CpgResult(False, None, 0.0, note=status.reason)

        self.workdir.mkdir(parents=True, exist_ok=True)
        # Уборка распакованных библиотек прошлых запусков: они занимают место и
        # в общем временном каталоге уже привели к отказу распаковки.
        prune_joern_tmp(self.cfg)
        cpg_path = self.workdir / f"{rev_id}.cpg.bin"
        if cpg_path.exists():
            cpg_path.unlink()

        script = self.scripts.get("joern-parse") or self.scripts.get("joern")
        if script is None:  # pragma: no cover
            return CpgResult(False, None, 0.0, note="нет исполняемого скрипта Joern")

        # Язык выбирается по СОСТАВУ КОРПУСА, а не жёстко.
        #
        # Почему это важно. Раньше здесь всегда стояло `--language PHP`,
        # и на Python-проекте Joern строил пустой граф: индексация
        # заканчивалась со статусом `degraded`, вызовы не извлекались,
        # а операция `slice` была недоступна. Замер на проекте sagaai:
        # 20,1 с работы Joern и 0 извлечённых вызовов.
        #
        # У Joern 4.0.99 для Python есть отдельный парсер `pysrc2cpg`
        # (проверено вызовом: `pysrc2cpg.bat --help` отвечает). Он даже умеет
        # `--exclude`, чего лишён php2cpg.
        lang = self._joern_language()
        if lang is None:
            return CpgResult(
                False, None, round(time.perf_counter() - started, 3),
                note=("в корпусе нет файлов на языках, которые понимает Joern "
                      "(PHP и Python): граф вызовов не строится"),
            )

        # Для Python используется свой скрипт-парсер: joern-parse
        # с `--language PYTHON` не разбирает Python так же полно.
        if lang == "PYTHON":
            py_script = self.scripts.get("pysrc2cpg")
            if py_script is not None:
                script = py_script

        cmd = [
            str(script),
            *([_heap_arg(self.cfg)] if _heap_arg(self.cfg) else []),
            str(self.cfg.root),
            "-o",
            str(cpg_path),
        ]
        # `--language` принимает только общий joern-parse: у специализированных
        # парсеров (php2cpg, pysrc2cpg) язык зашит в самом скрипте.
        if script.name.lower().startswith("joern-parse"):
            cmd.insert(-2, "--language")
            cmd.insert(-2, lang)
        # Исключения каталогов: pysrc2cpg их принимает, php2cpg — НЕТ
        # (проверено прогоном: «Unknown option --exclude»). Поэтому
        # отсев идёт только там, где парсер это умеет.
        if lang == "PYTHON" and script.name.lower().startswith("pysrc2cpg"):
            dirs = self._exclude_dirs()
            if dirs:
                # Один аргумент со запятыми: `--ignore-dir-names a,b,c`.
                #
                # Здесь была ошибка: список передавался без кавычек, и при
                # запуске через cmd (а он нужен для .bat) строка разбиралась
                # по запятым — Joern получал «Unknown argument '.idea'»
                # на каждый каталог и завершался с ошибкой.
                #
                # Кавычки теперь ставит `util.cmd_command` для КАЖДОГО аргумента.
                # Ручное квотирование здесь оставлять нельзя: кавычка попала бы
                # внутрь аргумента, и Joern получил бы каталог с кавычками в имени.
                # Проверено прогоном: с ним сборка CPG отказывала с причиной
                # «аргумент содержит «"»».
                cmd.append(f'--ignore-dir-names={",".join(dirs)}')
        note = None
        try:
            launch = _shell_cmd(cmd, script)
        except util.CmdArgumentError as exc:
            # Отказ cmd, а не отказ Joern: индексация продолжается без графа
            # свойств кода, причина названа. Подавлять это исключение нельзя —
            # иначе индекс молча теряет CPG и выглядит полным.
            return CpgResult(False, None, round(time.perf_counter() - started, 3),
                             note=str(exc))
        try:
            proc = subprocess.run(
                launch,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=php_env(self.cfg),
                cwd=str(self.workdir),
            )
        except subprocess.TimeoutExpired:
            return CpgResult(
                False, None, round(time.perf_counter() - started, 3),
                note=f"Joern не завершился за {timeout} с",
            )
        except OSError as exc:
            return CpgResult(
                False, None, round(time.perf_counter() - started, 3),
                note=f"не удалось запустить Joern: {type(exc).__name__}: {exc}",
            )

        tail = _stderr_digest(proc.stderr)
        if proc.returncode != 0 or not cpg_path.exists():
            return CpgResult(
                False, None, round(time.perf_counter() - started, 3),
                note=f"Joern завершился с кодом {proc.returncode}",
                stderr_tail=tail,
            )
        size_mb = round(cpg_path.stat().st_size / 1_048_576, 1)
        return CpgResult(
            True,
            cpg_path,
            round(time.perf_counter() - started, 3),
            note=f"CPG построена: {size_mb} МБ",
            stderr_tail=tail,
        )

    def _joern_language(self) -> str | None:
        """Какой язык подать в Joern: выбирается по составу корпуса.

        Возвращается `PHP`, `PYTHON` или None, если файлов на этих языках
        в корпусе нет — тогда граф строить не имеет смысла.

        Выбор по БОЛЬШИНСТВУ файлов: смешанные корпуса редки, а Joern
        разбирает один язык за прогон. Если в корпусе есть и PHP, и Python,
        берётся тот, файлов которого больше: второй останется на разборе
        деревом (рёбра `calls` и `calls_heuristic` всё равно строятся).
        """
        try:
            from . import corpus as corpus_mod

            result = corpus_mod.scan(self.cfg)
        except Exception:
            return "PHP"   # не удалось осмотреть — сохраняется прежнее поведение
        counts: dict[str, int] = {}
        for f in result.files:
            if f.lang in ("php", "python"):
                counts[f.lang] = counts.get(f.lang, 0) + 1
        if not counts:
            return None
        top = max(counts, key=lambda k: counts[k])
        return "PHP" if top == "php" else "PYTHON"

    def _exclude_dirs(self) -> list[str]:
        """Каталоги, которые НЕ надо разбирать в Python-режиме.

        ВАЖНО про смысл. Раньше этот метод возвращал список каталогов
        ИСХОДНИКОВ (`src`, `app`, `lib` и прочие) — он использовался
        в build() для PHP: вместо корня подавались только эти каталоги, потому
        что php2cpg не умеет исключать пути.

        Для Python такое поведение вредно: `pysrc2cpg` умеет
        `--ignore-dir-names`, и передача ему списка исходников ничего
        не исключает, зато заставляет разбирать служебные копии. Именно так
        в графе проекта sagaai появились файлы из `.dev_agent` — каталога
        с резервными копиями агента: 72 566 вызовов из них вместо исходников.

        Теперь метод возвращает настоящие СЛУЖЕБНЫЕ каталоги: те, что
        исключены в конфиге корпуса и являются простыми именами
        (без шаблонов и путей).
        """
        from .config import DEFAULT_EXCLUDES

        names: list[str] = []
        for pattern in list(self.cfg.exclude) + list(DEFAULT_EXCLUDES):
            # Берутся только простые шаблоны вида `**/имя/**`:
            # сложные (например `**/*backup*/**`) pysrc2cpg не поймёт.
            text = str(pattern).strip()
            if not (text.startswith("**/") and text.endswith("/**")):
                continue
            name = text[3:-3]
            if not name or "*" in name or "/" in name:
                continue
            if name not in names:
                names.append(name)
        return names

    # --- экспорт ---

    # --- уборка старых артефактов ---

    def prune_exports(self, keep_rev: str, keep_extra: int = 2) -> int:
        """Удалить экспорты старых ревизий, кроме текущей и нескольких последних.

        Зачем: каждая авто-реиндексация оставляет экспорт CPG в десятки мегабайт
        (на этом корпусе 40 МБ). Без уборки каталог растёт неограниченно.
        Дефект найден ПОСЛЕ включения авто-реиндексации: сторож переиндексировал
        часто, и накопилось 37 экспортов (1,5 ГБ) — при том, что пользы от
        старых экспортов нет, разбор идёт только по текущей ревизии.

        Что сохраняется: экспорт текущей ревизии и нескольких последних по имени.
        Файлы CPG (.bin) не трогаются: они нужны для повторного экспорта.

        Возвращает число удалённых каталогов.
        """
        try:
            exps = sorted(
                (p for p in self.workdir.iterdir()
                 if p.is_dir() and p.name.startswith("export_")),
                key=lambda p: p.name,
                reverse=True,
            )
        except OSError:
            return 0

        keep_names = {f"export_{keep_rev}_neo4jcsv"}
        for p in exps[: max(1, keep_extra)]:
            keep_names.add(p.name)

        removed = 0
        for p in exps:
            if p.name in keep_names:
                continue
            shutil.rmtree(p, ignore_errors=True)
            removed += 1
        return removed

    def export(self, rev_id: str, fmt: str = "neo4jcsv", timeout: int = 3600) -> CpgExport:
        """Экспорт CPG в машиночитаемый вид.

        Формат neo4jcsv — вынужденный и проверенный выбор:
          * graphson на полном корпусе падает с OutOfMemoryError (попытка собрать
            один JSON: "Required array length 2147483640 + 8 is too large"),
            памяти не хватает даже при -Xmx18g;
          * --repr=cpg даёт файл на каждый метод (731 файл, 739 МБ) и падает на записи;
          * neo4jcsv отдаёт два обычных CSV (узлы и рёбра), читаемых построчно -
            это ровно то, что нужно для потокового разбора (223 МБ, 27 с).

        Память задаётся аргументом -J-Xmx (документированный способ): переменная
        JAVA_OPTS до JVM не доходит и на больших проектах приводит к OOM. Лимит
        берётся из настройки корпуса `index.joern_heap_gb` (по умолчанию 8 ГБ) и
        перекрывается переменной RAGRAF_JOERN_HEAP_GB. Замеренное требование
        18 ГБ относится только к graphson, который здесь не применяется.
        """
        started = time.perf_counter()
        cpg_path = self.workdir / f"{rev_id}.cpg.bin"
        if not cpg_path.exists():
            return CpgExport(False, None, 0.0, "CPG не найдена")
        script = self.scripts.get("joern-export")
        if script is None:
            return CpgExport(False, None, 0.0, "joern-export не найден")

        # Уборка старых экспортов ДО нового: иначе место кончится.
        self.prune_exports(keep_rev=rev_id)

        out_dir = self.workdir / f"export_{rev_id}_{fmt}"
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        cmd = [
            str(script),
            *([_heap_arg(self.cfg)] if _heap_arg(self.cfg) else []),
            str(cpg_path),
            "--repr=all",
            f"--format={fmt}",
            "-o",
            str(out_dir),
        ]
        try:
            proc = subprocess.run(
                _shell_cmd(cmd, script), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout, env=java_env(self.cfg),
                cwd=str(self.workdir),
            )
        except Exception as exc:
            return CpgExport(False, None, round(time.perf_counter() - started, 3),
                             f"{type(exc).__name__}: {exc}")

        files = sorted(p for p in out_dir.rglob("*") if p.is_file()) if out_dir.exists() else []
        if proc.returncode != 0 or not files:
            tail = (proc.stderr or proc.stdout or "")[-400:]
            return CpgExport(False, None, round(time.perf_counter() - started, 3),
                             f"joern-export код {proc.returncode}, файлов {len(files)}: {tail}")

        nodes_csv = self._pick(files, ("nodes", "node"))
        rels_csv = self._pick(files, ("edges", "rels", "relationships", "relationship"))
        total_mb = round(sum(f.stat().st_size for f in files) / 1_048_576, 1)
        return CpgExport(
            True, out_dir, round(time.perf_counter() - started, 3),
            f"{fmt}: {len(files)} файлов, {total_mb} МБ",
            nodes_csv=nodes_csv, rels_csv=rels_csv,
        )

    def _pick(self, files: list[Path], needles: tuple[str, ...]) -> Path | None:
        """Выбрать файл по имени (узлы/рёбра) в каталоге экспорта."""
        for f in files:
            name = f.name.lower()
            if any(n in name for n in needles):
                return f
        return None

    # --- запросы ---

    def query(self, rev_id: str, script_text: str, timeout: int = 3600) -> tuple[bool, str | None, object]:
        """Выполнить CPGQL-скрипт над готовой CPG. Возвращает (ok, ошибка, результат).

        Два пути выполнения:

        1. ЧЕРЕЗ СЕРВЕР (быстрый) — если он поднят и разрешён настройкой.
           Замерено: запрос к поднятому серверу занимает 22–38 мс, тогда как
           отдельный запуск платит 5,53 с за загрузку классов Joern. Скрипт
           с @main при этом разбирается на две части: загрузка графа выполняется
           отдельно, а тело — как запрос.

        2. ОТДЕЛЬНЫМ ПРОЦЕССОМ (прежний путь) — если сервер недоступен или
           выключен настройкой. Он работает всегда, поэтому остаётся запасным.

        ВАЖНО: этот метод вызывает только наш код. Агенту язык запросов недоступен
        (ФТ-15) — наружу выставляются фиксированные операции фасада.
        """
        cpg_path = self.workdir / f"{rev_id}.cpg.bin"
        if not cpg_path.exists():
            return False, f"CPG для {rev_id} не найдена", None

        # --- путь 1: через сервер ---
        if self.cfg.index.joern_server:
            ok, err, result = self._query_via_server(rev_id, cpg_path, script_text, timeout)
            # Отказ сервера (не ошибка запроса) означает, что надо повторить
            # обычным способом: тогда работа не встанет из-за сервера.
            if not (err and str(err).startswith("СЕРВЕР НЕДОСТУПЕН")):
                return ok, err, result
            self._server_note = str(err)

        return self._query_via_process(rev_id, cpg_path, script_text, timeout)

    def _query_via_server(
        self, rev_id: str, cpg_path: Path, script_text: str, timeout: int
    ) -> tuple[bool, str | None, object]:
        """Выполнить скрипт через поднятый сервер Joern.

        Логика скрипта не меняется: подставляются только пути (к графу и к файлу
        результата), а скрипт сам кладёт данные в файл — так же, как при запуске
        отдельным процессом. Поэтому вызывающий код читает результат обычным
        способом, и разницы в поведении нет.

        Экономия — в отсутствии загрузки классов Joern: 22–38 мс вместо 5,53 с.
        """
        out_file = self.workdir / f"out_{util.short_hash(script_text)}.json"
        if out_file.exists():
            out_file.unlink()

        body = _extract_script_body(script_text, str(cpg_path), str(out_file))
        if body is None:
            # Скрипт не разобран: это не ошибка, а повод работать по-старому.
            return False, "СЕРВЕР НЕДОСТУПЕН: скрипт не разобран для серверного режима", None

        try:
            server = self._ensure_server()
        except Exception as exc:
            return False, f"СЕРВЕР НЕДОСТУПЕН: {type(exc).__name__}: {exc}", None

        try:
            # Граф загружается один раз: загрузка крупного графа занимает время.
            if self._loaded_rev != rev_id:
                server.query(
                    f'importCpg("{cpg_path.as_posix()}")',
                    timeout=min(timeout, 900),
                )
                self._loaded_rev = rev_id
            out = server.query(body, timeout=timeout)
        except Exception as exc:
            return False, f"СЕРВЕР НЕДОСТУПЕН: {type(exc).__name__}: {exc}", None

        if out.startswith("ОШИБКА ЗАПРОСА"):
            # Ошибка самого запроса, а не отказ сервера: повторять бессмысленно.
            return False, out[:500], None

        # Результат читается из файла — тем же способом, что и обычно.
        if not out_file.exists():
            return False, "сервер выполнил скрипт, но результат не создан", None
        try:
            payload = json.loads(out_file.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"результат не разобран: {type(exc).__name__}", None
        return True, None, payload

    def _ensure_server(self):
        """Получить поднятый сервер, подняв его при необходимости."""
        from .joern_server import JoernServer

        if getattr(self, "_server", None) is not None and self._server.status.running:
            return self._server
        script = self.scripts.get("joern")
        if script is None:
            raise RuntimeError("скрипт joern не найден")

        # Рабочий каталог создаётся ЗАРАНЕЕ. Сервер запускается с ним как
        # рабочим, и если каталога нет, запуск падает с NotADirectoryError
        # («The directory name is invalid»). У нового корпуса каталог .ragraf/cpg
        # ещё не создан: индексатор создаёт его позже, при загрузке стадий.
        try:
            self.workdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"не удалось создать рабочий каталог {self.workdir}: {exc}"
            ) from exc

        # JAVA_HOME нужен обязательно: joern.bat без него не находит Java,
        # даже когда java.exe есть в PATH (проверено запуском).
        java_path = find_java(self.cfg)
        java_home = java_path.parent.parent if java_path else None
        server = JoernServer(
            joern_script=script,
            workdir=self.workdir,
            java_home=java_home,
            heap=str(self.cfg.index.joern_heap_gb or 8) + "g",
            preferred_port=int(self.cfg.index.joern_server_port or 9500),
        )
        server.start()
        self._server = server
        self._loaded_rev = ""
        self._loaded_marker = ""
        return server

    def stop_server(self) -> None:
        """Остановить сервер, если он поднят. Вызывается при закрытии."""
        server = getattr(self, "_server", None)
        if server is not None:
            try:
                server.stop()
            except Exception:
                pass
            self._server = None

    def server_status(self) -> dict:
        """Состояние сервера для интерфейса администратора."""
        server = getattr(self, "_server", None)
        if server is None:
            return {
                "enabled": bool(self.cfg.index.joern_server),
                "running": False,
                "port": 0,
                "reason": ("сервер ещё не запускался"
                           if self.cfg.index.joern_server
                           else "сервер выключен в настройках"),
            }
        data = server.status.to_dict()
        data["enabled"] = bool(self.cfg.index.joern_server)
        return data

    def build_via_server(self, rev_id: str, timeout: int = 7200) -> CpgResult:
        """Построить граф через сервер: заменить build и export одним шагом.

        Зачем. Индексация делает ДВА отдельных запуска Joern, и каждый платит
        5,53 с за загрузку классов:
          * build() — joern-parse, строит файл .cpg.bin;
          * export() — joern-export, выгружает граф в CSV.
        Итого около 11 с накладных.

        Сервер строит граф в ПАМЯТИ и отвечает на запросы за 22–38 мс.
        Проверено на полном корпусе z.batteryservice.ru:
          * importCode — 7,42 с (это работа, а не накладные);
          * запрос вызовов — 4,13 с, получено 32 619 записей с теми же полями,
            что читает joern_export: srcMethod, callee, line и другие.

        Важное ограничение: файл .cpg.bin через сервер СОЗДАТЬ НЕ УДАЁТСЯ:
        команда save падает из-за блокировки файла. Поэтому при серверном пути
        файл графа не создаётся, и операции, которым он нужен, работают
        по-прежнему через отдельный процесс.

        Возвращается CpgResult с тем же смыслом, что у build: ok, путь, секунды.
        Путь указывается None: файла нет, граф живёт в памяти сервера.
        """
        started = time.perf_counter()
        try:
            server = self._ensure_server()
        except Exception as exc:
            return CpgResult(False, None, round(time.perf_counter() - started, 3),
                             note=f"сервер недоступен: {type(exc).__name__}: {exc}")

        root = Path(str(self.cfg.root))
        # Язык выбирается по составу корпуса — как и в build().
        #
        # Дефект, который это исправляет: здесь всегда стояло
        # `language = "PHP"`. На Python-проекте (sagaai) сервер получал
        # команду разобрать Python как PHP, возвращал пустой список вызовов,
        # и индексация заканчивалась статусом degraded с пометкой
        # «сервер вернул пустой список вызовов». Хотя обычный файловый
        # запрос через query() отдавал 72 566 вызовов — то есть граф
        # строился, но не тем парсером.
        lang = self._joern_language() or "PHP"
        try:
            out = server.query(
                f'val project = importCode(inputPath = "{root.as_posix()}", '
                f'language = "{lang}")',
                timeout=timeout,
            )
        except Exception as exc:
            return CpgResult(False, None, round(time.perf_counter() - started, 3),
                             note=f"построение не удалось: {type(exc).__name__}")

        if out.startswith("ОШИБКА ЗАПРОСА"):
            return CpgResult(False, None, round(time.perf_counter() - started, 3),
                             note=out[:300])

        self._built_via_server = True
        self._server_rev = rev_id
        seconds = round(time.perf_counter() - started, 3)
        return CpgResult(
            True, None, seconds,
            note=(f"граф построен в памяти сервера за {seconds:.2f} с; "
                  f"файл .cpg.bin не создаётся (save в серверном режиме "
                  f"не работает)"),
        )

    def calls_via_server(self, rev_id: str, timeout: int = 3600) -> tuple[bool, list[dict], str]:
        """Получить вызовы через сервер вместо чтения CSV-экспорта.

        Возвращает (успех, список вызовов, примечание). Формат записей тот же,
        что у joern_export: srcMethod, srcFile, line, callee, calleeName,
        dispatchType, code. Проверено на полном корпусе: 32 619 записей.

        Почему через сервер: экспорт в CSV требует отдельного запуска Joern
        (5,53 с старта + 0,50 с работы), а запрос — 4,13 с без старта.
        """
        out_file = self.workdir / f"server_calls_{rev_id}.json"
        if out_file.exists():
            out_file.unlink()

        body = _extract_script_body(QUERY_CALLS, "unused", str(out_file))
        if body is None:
            return False, [], "скрипт запроса не разобран"

        try:
            server = self._ensure_server()
            out = server.query(body, timeout=timeout)
        except Exception as exc:
            return False, [], f"сервер недоступен: {type(exc).__name__}: {exc}"

        if out.startswith("ОШИБКА ЗАПРОСА"):
            return False, [], out[:300]
        if not out_file.exists():
            return False, [], "сервер не создал файл результата"
        try:
            rows = json.loads(out_file.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, [], f"результат не разобран: {type(exc).__name__}"
        if not isinstance(rows, list):
            return False, [], "неожидаемый формат результата"
        return True, rows, f"получено записей: {len(rows)}"

    def methods_via_server(self, rev_id: str, timeout: int = 3600) -> tuple[bool, list[dict], str]:
        """Получить список методов через сервер. Формат совпадает с экспортом."""
        out_file = self.workdir / f"server_methods_{rev_id}.json"
        if out_file.exists():
            out_file.unlink()
        body = _extract_script_body(QUERY_METHODS, "unused", str(out_file))
        if body is None:
            return False, [], "скрипт запроса не разобран"
        try:
            server = self._ensure_server()
            out = server.query(body, timeout=timeout)
        except Exception as exc:
            return False, [], f"сервер недоступен: {type(exc).__name__}: {exc}"
        if out.startswith("ОШИБКА ЗАПРОСА"):
            return False, [], out[:300]
        if not out_file.exists():
            return False, [], "сервер не создал файл результата"
        try:
            rows = json.loads(out_file.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, [], f"результат не разобран: {type(exc).__name__}"
        if not isinstance(rows, list):
            return False, [], "неожидаемый формат результата"
        return True, rows, f"получено записей: {len(rows)}"

    def _query_via_process(
        self, rev_id: str, cpg_path: Path, script_text: str, timeout: int
    ) -> tuple[bool, str | None, object]:
        """Прежний путь: отдельный процесс Joern на каждый запрос."""
        script = self.scripts.get("joern")
        if script is None:
            return False, "скрипт joern не найден", None

        script_file = self.workdir / f"query_{util.short_hash(script_text)}.sc"
        util.write_text(script_file, script_text)
        out_file = self.workdir / f"out_{util.short_hash(script_text)}.json"
        if out_file.exists():
            out_file.unlink()

        # Синтаксис скриптового режима Joern (docs/interpreter): в файле должен быть
        # @main def, а параметры передаются через --param. Формат "--script файл"
        # без @main не исполняется — это проверено на документации.
        cmd = [
            str(script),
            "--script",
            str(script_file),
            "--param",
            f"cpgFile={cpg_path}",
            "--param",
            f"outFile={out_file}",
        ]
        try:
            # ВАЖНО: вызов идёт через _shell_cmd. Без него .bat-скрипт Joern
            # не запускается корректно: cmd.exe получает аргументы не так, и
            # Joern отвечает «Option --param failed when given 'cpgFile'.
            # unable to parse param input cpgFile». Дефект был в этом вызове:
            # остальные места (build, export) уже использовали _shell_cmd.
            proc = subprocess.run(
                _shell_cmd(cmd, script),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=php_env(self.cfg),
                cwd=str(self.workdir),
            )
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}", None

        if not out_file.exists():
            tail = (proc.stderr or proc.stdout or "")[-800:]
            return False, f"Joern не создал результат (код {proc.returncode}): {tail}", None
        try:
            data = util.loads(out_file.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"не удалось разобрать результат: {exc}", None
        return True, None, data


# --- разбор результата ---------------------------------------------------------


# Форма приёмника в поле `code` (исходный текст строки).
#
# Разбор охватывает ФОРМЫ РАЗНЫХ ЯЗЫКОВ, и образцы здесь УЗКИЕ намеренно.
#
#   self.имя(      Python, Ruby (через self), JS/TS-подобное
#   cls.имя(       Python (classmethod)
#   $this->имя(    PHP — обращение к своему объекту
#   $self->имя(    Perl — обращение к своему объекту
#   self::имя(     PHP — свой класс
#   static::имя(   PHP — позднее связывание: тот же класс или наследник
#   parent::имя(   PHP — ЯВНО базовый класс
#
# ЧЕГО ЗДЕСЬ НЕЛЬЗЯ ДЕЛАТЬ И ПОЧЕМУ. Первая редакция принимала как «свой объект»
# любой `$переменная->имя(`, и это была ошибка, создающая ВЫДУМАННЫЕ связи:
# замер на revoap показал, что под неё попадают `$file->isFile()`,
# `$iter_tmp2->current()` — то есть вызовы ЛЮБОГО локального объекта, а не своего.
# Такая запись, привязанная к классу владельца, в `impact`-анализе читается как
# «правка этого класса затронет это место», что является ложью.
# Поэтому переменная называется ЯВНО: только `$this` и `$self`.
_SHAPE_RE = re.compile(
    r"^(?:"
    r"(?P<own1>(?:self|cls)\.)"
    r"|(?P<dollar>\$(?:this|self)->)"
    r"|(?P<scoped>(?:self|static|parent)::)"
    r")"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)

# Служебные имена чтения атрибутов: их нельзя принять за вызов метода.
FIELD_ACCESS_NAMES = ("<operator>.fieldAccess", "<operator>.indexAccess")


def call_shape(code: str | None) -> tuple[str, str]:
    """Разобрать форму вызова из поля `code` (исходный текст строки).

    Возвращает (форма, имя), где форма:
      * `own_method`   — вызов метода СВОЕГО объекта или класса
        (`self.м(`, `cls.м(`, `$this->м(`, `$self->м(`, `self::м(`, `static::м(`);
        вызов через ЛЮБУЮ ДРУГУЮ переменную (`$file->м(`) сюда НЕ попадает;
      * `parent_method` — вызов СВОЕГО БАЗОВОГО класса (`parent::м(`).
        Особый вид: направление в базу заявлено самим кодом, поэтому поиск
        идёт сразу по базам, без обхода собственного класса;
      * `field_access` — чтение атрибута: это НЕ вызов, связью быть не может;
      * `other`        — всё остальное: решает не форма, а разрешение имени.

    Зачем это нужно. Поле `calleeName` НЕ различает две разные вещи, а `code`
    различает их прямо по тексту:

        self.send_header("X", "y")   -> вызов СВОЕГО метода
        self.send_header              -> ЧТЕНИЕ АТРИБУТА (не вызов)

    Замерено на собственном корпусе (132 396 записей): из 2126 записей с началом
    `self.` настоящих вызовов 350, остальные 1776 — чтение атрибутов. Без этого
    различия число «вызовов своего класса» завышается примерно в шесть раз.

    Функция чистая (без обращения к хранилищу): проверяется тестом на строках.
    """
    text = str(code or "").strip()
    if not text:
        return "other", ""
    m = _SHAPE_RE.match(text)
    if not m:
        return "other", ""
    name = m.group("name")
    tail = text[m.end():].lstrip()
    # Скобка сразу после имени означает ВЫЗОВ своего метода.
    if tail.startswith("("):
        if m.group("scoped") == "parent::":
            return "parent_method", name
        return "own_method", name
    # Точка после имени означает, что приёмник — ЧУЖОЙ объект:
    # `self.conn.execute(…)` — вызов метода соединения, а не чтение поля.
    # Такую запись НЕЛЬЗЯ отбрасывать как чтение: цель разрешается по имени.
    if tail.startswith(".") or tail.startswith("->") or tail.startswith("::"):
        return "other", name
    # Остальное — обращение к полю без вызова: `self.wfile`, `$this->x = 1`.
    return "field_access", name


def parse_calls_export(calls: list[dict]) -> list[dict]:
    """Оставить только настоящие межпроцедурные вызовы.

    Отбрасываются: операторы языка, встроенные функции, вызовы без владельца
    (в глобальной области — их не к чему привязать в графе).

    ВАЖНО про имена владельцев. Обозначение глобальной области РАЗНОЕ
    у разных языков:
      * PHP: `_diag.php:<global>` и `_diag.php:<global>-><lambda>0`;
      * Python: `core\\api_errors.py:<module>`.

    Раньше проверялось только `<global>`, поэтому на Python-корпусе
    ни одна запись не отбрасывалась как глобальная И ни одна не
    привязывалась — модуль верхнего уровня не соответствует ни одному
    символу в индексе. Замер: 40 806 вызовов, подтверждено 0.
    """
    out: list[dict] = []
    for call in calls:
        owner = call.get("owner_method")
        callee = call.get("callee")
        if not owner or not callee:
            continue
        if _is_global_owner(owner):
            continue
        if _is_noise_call(string_of(callee), call.get("callee_name")):
            continue
        out.append(call)
    return out


def _is_global_owner(owner: str) -> bool:
    """Вызов из глобальной области (модуля), а не из функции.

    Такие вызовы отбрасываются: в индексе им не соответствует символ,
    поэтому ребро не к чему привязать.

    Проверяются оба обозначения, встречающиеся на практике:
      * `<global>` — PHP;
      * `<module>` — Python (проверено на проекте sagaai).
    """
    text = string_of(owner)
    # Учитывается и хвост с лямбдой: `file.php:<global>-><lambda>0`.
    for marker in ("<global>", "<module>"):
        if marker in text:
            return True
    return False


def string_of(value: object) -> str:
    return "" if value is None else str(value)


def _is_noise_call(callee: str, callee_name: str | None = None) -> bool:
    """Служебные «вызовы» Joern, не являющиеся вызовами кода.

    Проверено на реальном экспорте: подавляющее большинство рёбер CALL — операторы
    языка (`<operator>.assignment` — 50 тыс. из 183 тыс., `indexAccess`, `array`).
    Для CPG/PDG они полезны, но как межпроцедурные связи бесполезны.
    """
    name = f"{callee or ''}|{callee_name or ''}"
    if "<operator>" in name or "<meta" in name:
        return True
    low = (callee or "").strip().lower()
    return low in {
        "array", "list", "new", "init", "print", "echo", "exit", "isset", "unset",
        "declare", "die", "var_dump", "print_r",
    }


def is_noise_file(path: str | None) -> bool:
    """Служебные пути: артефакты агента, бэкапы, вендорные копии, сгенерированное.

    Проверено прогоном: без этой фильтрации в CPG попадают `.zwork/artifacts/...`,
    `_bak`, `vendor` — их связи засоряют граф и искажают impact-анализ.

    Маркеры указаны без ведущего слэша: путь может начинаться сразу с каталога
    (`.zwork/artifacts/...`), и тогда проверка по `/.zwork/` не срабатывает.
    """
    if not path:
        return False
    low = path.replace("\\", "/").lower().lstrip("/")
    # Вариант с ведущей точкой у относительного пути (артефакты): "./var/..."
    low = low[2:] if low.startswith("./") else low
    markers = (
        ".zwork/", ".agents/", ".dev_agent/", ".kilo/",
        "node_modules/", "vendor/", "var/", "_bak/",
        ".git/", "dist/", "build/", "cover/",
    )
    if any(low.startswith(m) for m in markers):
        return True
    if any(f"/{m}" in low for m in markers):
        return True
    return "_bak" in low or ".min." in low


def parse_calls_export(calls: list[dict]) -> list[dict]:
    """Оставить только настоящие межпроцедурные вызовы внутри корпуса.

    Отбрасываются: операторы языка; вызовы из глобальной области (их не к чему
    привязать в нашем графе); вызовы из служебных каталогов; вызовы без цели;
    и ЧТЕНИЕ АТРИБУТОВ вида `self.wfile` — это не вызовы (`call_shape`).

    Про последнее подробнее. Замер на полном корпусе: из 2126 записей с началом
    `self.` только 350 оказались вызовами, а 1776 — обращениями к полям (у них
    `calleeName` = `<operator>.fieldAccess`). Оставить их значило бы утверждать,
    что модуль вызывает собственные поля.
    """
    out: list[dict] = []
    for call in calls:
        owner = call.get("owner_method")
        callee = call.get("callee")
        if not owner or not callee:
            continue
        # Владелец-заглушка <global>: привязать не к чему — у файла нет символа-метода.
        if is_global_owner(owner):
            continue
        if is_noise_file(call.get("owner_file")):
            continue
        if _is_noise_call(str(callee), call.get("callee_name")):
            continue
        # Чтение атрибута — не вызов. Отбрасывается по форме кода, а если формы
        # нет — по служебному имени цели из поля callee_name.
        shape, _shape_name = call_shape(call.get("code"))
        if shape == "field_access":
            continue
        target_name = str(call.get("callee_name") or call.get("calleeName") or "")
        if target_name in FIELD_ACCESS_NAMES:
            continue
        out.append(call)
    return out


def parse_call_export(payload: object) -> list[dict]:
    """Преобразовать выгрузку вызовов Joern в наши записи рёбер.

    Joern отдаёт список JSON-строк; формат может отличаться между версиями,
    поэтому разбор устойчив к обоим вариантам: список строк и список объектов.
    """
    rows = _as_rows(payload)
    out: list[dict] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        src = _first(item, ("srcMethod", "src_method", "method"))
        callee = _first(item, ("callee", "methodFullName", "method_full_name"))
        line = _as_int(_first(item, ("line", "lineNumber", "line_number")))
        path = _first(item, ("srcFile", "src_file", "file"))
        name = _first(item, ("calleeName", "callee_name", "name"))
        if not callee:
            continue
        out.append(
            {
                "src_method": src,
                "callee": callee,
                "callee_name": name,
                "line": line,
                "path": path,
            }
        )
    return out


def _as_rows(payload: object) -> list:
    """Joern может вернуть список или строку с вложенным JSON."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, str):
        text = payload.strip()
        try:
            return util.loads(text)
        except Exception:
            # Иногда приходит поток строк, по одной JSON-строке на элемент.
            rows = []
            for line in text.splitlines():
                line = line.strip().rstrip(",")
                if line.startswith("{") and line.endswith("}"):
                    try:
                        rows.append(util.loads(line))
                    except Exception:
                        continue
            return rows
    if isinstance(payload, dict):
        for key in ("result", "data", "rows", "items"):
            if key in payload:
                return _as_rows(payload[key])
    return []


def _first(item: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            value = item[key]
            if isinstance(value, list):
                value = value[0] if value else None
            if isinstance(value, dict):
                value = value.get("l") or value.get("value")
            if value not in (None, ""):
                return str(value)
    return None


def _as_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


# --- слияние с нашим графом ---------------------------------------------------

CPG_SOURCE = "joern-php2cpg"


def _is_builtin_target(callee: str) -> bool:
    """Цель — встроенная функция или класс PHP, а не символ проекта.

    Зачем отдельная классификация: из 17 822 отфильтрованных вызовов 13 008 ведут
    к встроенным (`array_push`, `is_array`, `trim`, `Iterator.next`). Они НЕ должны
    попадать в «не разрешено» — это не потеря данных, а норма: таких символов нет
    в проекте по определению. Честная метрика должна их различать.
    """
    from .extract.php_ast import PHP_BUILTIN_CLASSES, PHP_BUILTINS

    text = callee.strip().lstrip("\\")
    if not text:
        return False
    head, _, tail = text.partition("::")
    if tail:
        # Iterator::current, PDO::prepare и т.п. — методы встроенных интерфейсов/классов.
        return head.split("\\")[-1] in PHP_BUILTIN_CLASSES
    base = text.split("\\")[-1]
    if base.lower() in PHP_BUILTINS:
        return True
    return base in PHP_BUILTIN_CLASSES


def owner_class(owner_fqn: str | None, owner_file: str | None) -> str | None:
    """Имя класса-владельца из имени метода, если он ВНУТРИ класса.

    Замеренная особенность индекса: наш fqn метода записывается как
    `<путь>:<имя>` БЕЗ имени класса (`src/ragraf/admin_server.py:do_GET`), хотя
    узел класса есть и ребро `contains` «класс → метод» построено. Поэтому
    класс берётся из имени владельца, которое отдаёт Joern:

        src\\ragraf\\admin_server.py:<module>.Handler.do_GET  ->  Handler
        src\\ragraf\\cli.py:<module>.main                     ->  None

    Если метода в имени нет (не вложен в класс), возвращается None: привязывать
    такой вызов к какому-либо классу нечем и незачем.
    """
    if not owner_fqn:
        return None
    text = str(owner_fqn)
    tail = text.split(":", 1)[1] if ":" in text else text
    tail = tail.split("<module>.")[-1]
    parts = [p for p in tail.strip().split(".") if p]
    # Нужны хотя бы два сегмента: «Класс.метод». Один сегмент — метод модуля.
    if len(parts) < 2:
        return None
    candidate = parts[-2]
    # Заглушки и служебные маркеры классом не являются.
    if candidate.startswith("<") or candidate in ("<lambda>",):
        return None
    return candidate


def resolve_in_class(
    conn,
    rev_id: str,
    owner_fqn: str | None,
    owner_file: str | None,
    name: str,
) -> str | None:
    """Найти метод `name` в СВОЁМ классе владельца, через ребро `contains`.

    Почему именно через граф, а не подбором имени. Утверждение «этот вызов идёт
    в свой класс» — сильное, и оно должно опираться на факт: класс, объявленный
    в этом же файле, содержит метод с таким именем. Связь `class -contains->
    method` в графе есть (замерено: 301 ребро), и её достаточно.

    Условия отказа (каждое — отказ, а не догадка):
      * класса в имени владельца нет — метод объявлен в модуле;
      * класса с таким именем нет в этом файле;
      * метод в классе не один по имени (переопределения, слияние узлов) —
        выбрать одну из нескольких целей значило бы выдумать ребро.

    Возвращается идентификатор узла метода или None.
    """
    cls = owner_class(owner_fqn, owner_file)
    if not cls or not name:
        return None
    path = str(owner_file or "").replace("\\", "/")
    row = None
    if path:
        row = conn.execute(
            "SELECT node_id FROM node WHERE rev_id = ? AND kind = 'class' AND fqn = ?",
            (rev_id, f"{path}:{cls}"),
        ).fetchone()
    if row is None:
        # Запасной вариант: класс мог быть записан и без пути (PHP-корпуса).
        row = conn.execute(
            "SELECT node_id FROM node WHERE rev_id = ? AND kind = 'class' AND fqn LIKE ?",
            (rev_id, f"%:{cls}"),
        ).fetchone()
    if row is None:
        return None
    class_id = str(row[0] if not hasattr(row, "keys") else row["node_id"])
    got = conn.execute(
        """SELECT n.node_id FROM edge e
             JOIN node n ON n.node_id = e.dst AND n.rev_id = e.rev_id
            WHERE e.rev_id = ? AND e.src = ? AND e.kind = 'contains' AND n.name = ?""",
        (rev_id, class_id, name),
    ).fetchall()
    if len(got) != 1:
        return None
    first = got[0]
    return str(first[0] if not hasattr(first, "keys") else first["node_id"])


def class_bases(conn, rev_id: str, class_id: str) -> list[str]:
    """Прямые базовые классы (только ВНУТРИ корпуса).

    Возвращает идентификаторы узлов-классов. Ребро `extends` на заглушку
    `@ext:…` отбрасывается: снаружи корпуса продолжать цепочку нечем, а
    притворяться, что мы знаем базовый класс библиотеки, нельзя.

    Читается по ПОЗИЦИИ, а не по имени колонки: у обычного соединения sqlite3
    нет row_factory. Эта ошибка уже встречалась в проекте четыре раза.
    """
    rows = conn.execute(
        """SELECT n.node_id FROM edge e
             JOIN node n ON n.node_id = e.dst AND n.rev_id = e.rev_id
            WHERE e.rev_id = ? AND e.src = ? AND e.kind = 'extends'
              AND n.kind IN ('class','interface','struct','enum')""",
        (rev_id, class_id),
    ).fetchall()
    out: list[str] = []
    for r in rows:
        nid = str(r[0])
        if nid not in out:
            out.append(nid)
    return out


def resolve_own_method_via_mro(
    conn,
    rev_id: str,
    owner_fqn: str | None,
    owner_file: str | None,
    name: str,
    max_depth: int = 6,
    skip_self: bool = False,
) -> tuple[str | None, str]:
    """Найти `self.имя()` в СВОЁМ классе, затем в его базах (обход в ширину).

    ПОЧЕМУ ОБХОД, А НЕ ЧЕСТНЫЙ MRO. Настоящий порядок разрешения методов
    (C3-линеаризация) требует двух вещей, которых в графе нет: порядка баз в
    объявлении и признака `super()` в месте вызова. Обход в ширину даёт то же
    множество кандидатов, но может выбрать НЕ тот класс: если метод есть и в
    первой базе, и в её предке, а прямой вызов идёт во второй базе, ответ будет
    неверным. Поэтому результат помечается как производная связь, и это
    записано в паспорте как известное ограничение.

    ЧТО ЗДЕСЬ ГЛАВНОЕ — не найти больше, а НЕ ВЫДУМАТЬ. Связь идёт в
    `impact`-анализ, то есть по ней агент судит о последствиях правки, поэтому
    принимаются только однозначные случаи (см. ниже). Любая неоднозначность —
    ОТКАЗ, а не выбор наугад.

    Условия отказа (каждое возвращает None с причиной):
      1. у владельца нет класса в имени (`<module>.main`) — привязывать не к чему;
      2. класса нет в файле владельца;
      3. обход достиг `max_depth` без результата — цепочка слишком длинная;
      4. метод встречается БОЛЬШЕ ОДНАЖДО в одном классе (переопределение,
         слияние узлов) или найден в НЕСКОЛЬКИХ классах на одном уровне
         наследования — выбрать один из них значило бы выдумать связь;
      5. цепочка наследования содержит класс, от которого наследуются двое
         (ромб): одна и та же база доступна по двум путям, и приоритет пути
         неопределён.

    Возвращается (идентификатор узла метода или None, причина/способ).
    """
    cls_name = owner_class(owner_fqn, owner_file)
    if not cls_name:
        return None, "no_class_in_owner"
    path = str(owner_file or "").replace("\\", "/")
    row = None
    if path:
        row = conn.execute(
            "SELECT node_id FROM node WHERE rev_id = ? AND kind = 'class' AND fqn = ?",
            (rev_id, f"{path}:{cls_name}"),
        ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT node_id FROM node WHERE rev_id = ? AND kind = 'class' AND fqn LIKE ?",
            (rev_id, f"%:{cls_name}"),
        ).fetchone()
    if row is None:
        return None, "class_not_found"
    start = str(row[0])

    # Обход в ширину по уровням наследования.
    level = [start]
    visited = {start}
    # Для `parent::имя(` собственный класс не проверяется: код прямо указал базу.
    if skip_self:
        level = class_bases(conn, rev_id, start)
        visited.update(level)
        depth_shift = 1
        if not level:
            return None, "chain_exhausted"
    for depth in range(max_depth + 1):
        if not level:
            return None, "chain_exhausted"
        hits: list[str] = []
        for cid in level:
            got = conn.execute(
                """SELECT n.node_id FROM edge e
                     JOIN node n ON n.node_id = e.dst AND n.rev_id = e.rev_id
                    WHERE e.rev_id = ? AND e.src = ? AND e.kind = 'contains'
                      AND n.name = ?""",
                (rev_id, cid, name),
            ).fetchall()
            ids = []
            for g in got:
                gid = str(g[0])
                if gid not in ids:
                    ids.append(gid)
            # Условие 4: два одноимённых метода в одном классе — отказ.
            if len(ids) > 1:
                return None, "ambiguous_same_class"
            if ids:
                hits.append(ids[0])
        if len(hits) == 1:
            lvl = depth + (1 if skip_self else 0)
            how = "own_class" if lvl == 0 else f"mro_depth{lvl}"
            return hits[0], how
        if len(hits) > 1:
            # Условие 4: нашли в разных классах одного уровня — отказ.
            return None, "ambiguous_same_level"
        # Следующий уровень: прямые базы всех классов текущего уровня.
        next_level: list[str] = []
        for cid in level:
            for base in class_bases(conn, rev_id, cid):
                if base in visited:
                    # Условие 5: один и тот же предок достижим по двум путям —
                    # приоритет пути неопределён, отказываемся.
                    return None, "diamond_inheritance"
                visited.add(base)
                next_level.append(base)
        level = next_level
    return None, "depth_limit"


def merge_calls(
    cfg: ProjectConfig,
    rev_id: str,
    calls: list[dict],
    resolve_fqn,
    graph_db: str | Path | None = None,
) -> dict:
    """Добавить в граф межпроцедурные вызовы, подтверждённые CPG.

    Правило разрешения цели (архитектура §6.1 — приоритет доказательств):
      1. точное совпадение полного имени метода с нашим fqn → ребро `calls`, stated, 1.0;
      2. совпадение по имени метода и файлу → `calls`, stated, 0.9;
      3. цель пришла заглушкой, но есть имя вызова → `calls_heuristic`, derived,
         не выше 0.6: имя допускает несколько кандидатов, поэтому это догадка,
         а не доказательство (та же защита от «галлюцинации связями»);
      4. иначе — не добавляется.

    Параметр `graph_db` — путь к хранилищу вместо взятого из конфигурации. Нужен
    проверкам: они работают на одноразовом хранилище и не должны трогать индекс
    проекта. По умолчанию поведение прежнее.

    Возвращает статистику: сколько рёбер добавлено и почему остальные отклонены.
    """
    from . import store

    conn = store.connect(graph_db if graph_db is not None else cfg.graph_db)
    stats = {
        "added": 0, "by_fullname": 0, "by_name_file": 0,
        "unresolved": 0, "builtin_skipped": 0, "duplicate": 0,
    }
    rows: list[tuple] = []
    try:
        existing = {
            str(r["edge_uid"])
            for r in conn.execute(
                "SELECT edge_uid FROM edge WHERE rev_id = ? AND kind = 'calls'", (rev_id,)
            ).fetchall()
        }
        for call in calls:
            src_fqn = call.get("owner_method")
            callee = call.get("callee")
            owner_file = call.get("owner_file")
            if not src_fqn or not callee:
                stats["unresolved"] += 1
                continue
            # Нормализация имён Joern к нашему формату: App\Core\Config->get
            # превращается в App\Core\Config::get. Без этого не сопоставляется ничего:
            # проверено прогоном — из 992 вызовов привязалось 0.
            src_fqn = normalize_joern_name(src_fqn)
            callee = normalize_joern_name(callee)
            owner_file = normalize_joern_path(owner_file)
            # Резолвер всегда возвращает тройку (id, достоверность, способ) — если взять
            # только первое значение, в SQL попадёт кортеж. Проверено прогоном.
            src_id, _src_conf, _src_how = resolve_fqn(src_fqn, owner_file)

            # --- ЦЕЛЬ — ЗАГЛУШКА: берём имя вызова, а не полное имя метода ---
            #
            # Измеренный дефект. Для Python Joern у большинства вызовов не может
            # вывести полное имя метода и пишет заглушку `<unknownFullName>`:
            # на собственном корпусе так 10 156 записей из 10 486, прошедших
            # фильтр. При этом настоящее имя вызова в выдаче ЕСТЬ — поле
            # `callee_name` (`_cached`, `summary`, `get`). Прежняя редакция
            # пыталась разрешить заглушку и отбрасывала запись целиком, поэтому
            # из 131 874 запросов не добавлялось НИ ОДНОГО ребра.
            #
            # Почему такие рёбра слабее остальных (и это отличают поля ребра).
            # Полное имя метода — доказательство (`stated`, 0.9): Joern связал
            # вызов с конкретным методом. Имя вызова — только догадка о цели: по
            # `get` в одном файле может оказаться несколько кандидатов, а цель
            # могла быть и в другом файле. Принцип проекта прямо запрещает
            # выдавать догадку за доказательство, поэтому такое ребро пишется
            # КАК ЭВРИСТИКА: вид `calls_heuristic`, достоверность не выше 0.6,
            # происхождение `derived`. Выдавать его за `stated` нельзя.
            # Имя вызова читается под ОБОИМИ ключами, и это не перестраховка.
            # Производитель записи (обходной путь в indexer) пишет `calleeName`,
            # разбор CSV-экспорта — `callee_name`. Прежняя редакция знала только
            # второй ключ, поэтому на записях обходного пути имя вызова
            # оказывалось пустым и КАЖДАЯ цель уходила в «не разрешено» —
            # ровно это и давало ноль добавленных рёбер при живом резолвере.
            # Один и тот же дефект (рассогласование ключа) уже дважды мешал
            # этому пути: он же был найден в фильтре вызовов.
            guess_name = str(call.get("callee_name") or call.get("calleeName") or "").strip()
            placeholder = callee.startswith("<unknown") or callee.startswith("<unresolved")
            edge_kind = "calls"
            provenance = "stated"
            how = ""
            dst_id = None

            # --- ПРИЁМНИК — САМ ОБЪЕКТ ИЛИ КЛАСС: сначала ищем в СВОЁМ классе ---
            #
            # Только для формы `self.имя(…)` и `cls.имя(…)`: приёмник здесь — сам
            # объект (или класс), поэтому метод обязан быть в том же классе либо
            # унаследован от базы. Такая привязка точнее поиска по одному имени:
            # она идёт по ребру `contains` от класса, объявленного в этом же файле,
            # а не по совпадению имени в корпусе.
            #
            # ДОКАЗАТЕЛЬСТВО ОСТАЁТСЯ ДОКАЗАТЕЛЬСТВОМ: если класс объявляет метод
            # ровно один раз, это `calls`/`stated` без ограничения достоверности —
            # Joern показал имя цели, а граф подтвердил принадлежность классу.
            #
            # Почему нельзя так же поступать с `self.атрибут.метод(…)`: приёмник
            # там ЧУЖОЙ объект, и привязка к своему классу была бы вымыслом.
            # Такие записи идут обычным путём — по разрешению имени.
            shape, shape_name = call_shape(call.get("code"))
            if shape in ("own_method", "parent_method") and shape_name:
                # `parent::имя(` — код сам говорит, что цель в БАЗОВОМ классе:
                # собственный класс не проверяется вообще, иначе нашли бы
                # переопределение и выдали бы его за цель вызова.
                in_class = None
                if shape == "own_method":
                    in_class = resolve_in_class(
                        conn, rev_id, src_fqn, owner_file, shape_name
                    )
                if in_class:
                    dst_id = in_class
                    confidence = 0.9
                    how = "own_class"
                    # Полное имя в выдаче могло быть заглушкой — принадлежность
                    # классу всё равно доказана, поэтому вид остаётся `calls`.
                    callee = shape_name
                else:
                    # Своего метода в классе нет — ищем в БАЗОВЫХ классах,
                    # но только там, где привязка однозначна (см. докстроку
                    # resolve_own_method_via_mro).
                    #
                    # ВАЖНО про вид связи. Найденное в базовом классе — это
                    # ПРОИЗВОДНАЯ связь, а не доказательство: порядок разрешения
                    # методов (C3-линеаризация) в графе не восстанавливается,
                    # потому что нет ни порядка баз в объявлении, ни признака
                    # `super()`. Замер показал, чего стоит ошибка в этом месте:
                    # на собственном корпусе через базы нашлось бы 6 вызовов из
                    # 417, то есть выигрыш маленький, а риск выдуманной связи в
                    # `impact`-анализе — реальный. Поэтому вид ВСЕГДА
                    # `calls_heuristic` и достоверность не выше 0.5: агент
                    # обязан видеть, что это вывод, а не факт.
                    via_base, how_base = resolve_own_method_via_mro(
                        conn, rev_id, src_fqn, owner_file, shape_name,
                        skip_self=(shape == "parent_method"),
                    )
                    if via_base:
                        dst_id = via_base
                        confidence = 0.5
                        how = how_base
                        callee = shape_name
                        edge_kind = "calls_heuristic"
                        provenance = "derived"

            if dst_id is None:
                if placeholder:
                    if not guess_name or guess_name.startswith("<"):
                        stats["unresolved"] += 1
                        stats.setdefault("unresolved_samples", [])
                        if len(stats["unresolved_samples"]) < 20:
                            stats["unresolved_samples"].append(callee)
                        continue
                    callee = guess_name
                    edge_kind = "calls_heuristic"
                    provenance = "derived"
                dst_id, confidence, how = resolve_fqn(callee, owner_file, mode="callee")
            if not src_id:
                stats["unresolved"] += 1
                continue
            if not dst_id:
                # Встроенные функции и классы PHP — не потеря, а норма: их нет в проекте.
                if _is_builtin_target(callee):
                    stats["builtin_skipped"] = stats.get("builtin_skipped", 0) + 1
                else:
                    stats["unresolved"] += 1
                    stats.setdefault("unresolved_samples", [])
                    if len(stats["unresolved_samples"]) < 20:
                        stats["unresolved_samples"].append(callee)
                continue
            if edge_kind == "calls_heuristic":
                # Достоверность эвристики ограничена сверху: имя вызова не даёт
                # того же доказательства, что полное имя метода.
                confidence = min(float(confidence or 0.0), 0.6)
            line = call.get("line") if isinstance(call.get("line"), int) else None
            uid = util.stable_id(src_id, dst_id, edge_kind, owner_file, line)
            if uid in existing:
                stats["duplicate"] += 1
                continue
            rows.append(
                (
                    rev_id,
                    uid,
                    src_id,
                    dst_id,
                    edge_kind,
                    provenance,
                    owner_file,
                    line,
                    confidence,
                    util.dumps(["tree-sitter-php", CPG_SOURCE]),
                    # Свойства ребра: у вызовов из CPG их нет, но колонка обязана
                    # быть в кортеже — иначе число значений не совпадёт с запросом
                    # (найдено прогоном: «uses 11, and there are 10 supplied»).
                    util.dumps({"source": "cpg", "resolved_by": how}),
                )
            )
            existing.add(uid)
            stats["added"] += 1
            if edge_kind == "calls":
                stats["added_stated"] = stats.get("added_stated", 0) + 1
            else:
                stats["added_heuristic"] = stats.get("added_heuristic", 0) + 1
            stats[f"by_{how}"] = stats.get(f"by_{how}", 0) + 1

        if rows:
            store.executemany_chunked(
                conn,
                """INSERT OR REPLACE INTO edge(rev_id, edge_uid, src, dst, kind, provenance,
                                              evidence_path, evidence_line, confidence, sources, props)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
                batch=cfg.index.batch_size,
            )
            conn.commit()
    finally:
        conn.close()
    return stats
