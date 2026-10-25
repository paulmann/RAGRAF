"""Служебные функции: пути, хэши, glob-сопоставление, JSON.

Все функции детерминированы (НФТ-5): результат зависит только от входных данных,
не от времени, порядка обхода и переменных окружения.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

# --------------------------------------------------------------------------
# Пути и хэши
# --------------------------------------------------------------------------


def to_posix(path: str | Path) -> str:
    """Путь в POSIX-форме (слеши). Нормализация регистра не выполняется."""
    return str(path).replace("\\", "/")


def rel_posix(root: str | Path, path: str | Path) -> str:
    """Путь относительно корня в POSIX-форме. Всегда с прямыми слешами."""
    return to_posix(Path(path).resolve().relative_to(Path(root).resolve()))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# Адрес строки внутри текста чанка
# --------------------------------------------------------------------------

#: Служебный заголовок чанка: `function indexed_hashes (src/ragraf/watch.py:151)`.
#: Его пишет чанкер, и он НЕ является частью файла.
_CHUNK_HEADER_RE = re.compile(r"\((?:[^()\n]{1,240}):\d+\)\s*$")


def chunk_header_lines(text: str) -> int:
    """Сколько строк текста занимает служебный заголовок чанка (0 или 1).

    Зачем это нужно считать, а не предполагать. У чанков-СИМВОЛОВ первая строка
    — заголовок, и код начинается со строки 1, соответствующей
    `chunk_map.start_line`.У чанков-файлов (разметка, документация, текстовые
    языки) заголовка нет, и первая строка текста и есть `start_line`. Одна
    формула на оба случая давала сдвиг: замер 14.10.2026 — строка
    «бюджет исчерпан на стадии» лежит в `src/ragraf/indexer.py:99`, а индекс
    отдавал `100`. Ссылка `path:line` — контракт доверия, поэтому сдвиг на
    строку означал бы, что каждая ссылка внутри функции показывает не ту строку.
    """
    if not text:
        return 0
    first = text.split("\n", 1)[0]
    return 1 if _CHUNK_HEADER_RE.search(first) else 0


def text_line(text: str, pos: int, start_line: int) -> int:
    """Номер строки ФАЙЛА по позиции в тексте чанка.

    Единственное место этого пересчёта: и лексический поиск (`lexvec`), и
    структурный анализ (`analysis`) обязаны считать одинаково, иначе два ответа
    об одном и том же коде назовут разные строки.
    """
    if pos < 0:
        return max(1, int(start_line))
    return max(1, int(start_line) + text.count("\n", 0, pos) - chunk_header_lines(text))


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    """sha256 файла. Чтение блоками: файлы бывают крупными."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def stable_id(*parts: Any) -> str:
    """Детерминированный идентификатор из частей.

    Разделитель '|' и отсутствие времени/UUID — требование НФТ-5.
    None и пустые строки приводятся к пустому представлению.
    """
    payload = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def short_hash(text: str, length: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


# --------------------------------------------------------------------------
# Glob-сопоставление
# --------------------------------------------------------------------------

_GLOB_CACHE: dict[str, re.Pattern[str]] = {}


def _translate(pattern: str) -> str:
    """Перевод glob в регулярное выражение.

    Поддержано: '**' (любые каталоги), '*' (в пределах сегмента), '?' (один символ),
    '[...]' (набор). Сопоставление — с путём в POSIX-форме относительно корня.
    """
    pat = to_posix(pattern).lstrip("/")
    out: list[str] = []
    i = 0
    n = len(pat)
    while i < n:
        ch = pat[i]
        if ch == "*":
            if pat.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
                continue
            if pat.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if ch == "?":
            out.append("[^/]")
            i += 1
            continue
        if ch == "[":
            j = i + 1
            if j < n and pat[j] in "!^":
                j += 1
            if j < n and pat[j] == "]":
                j += 1
            while j < n and pat[j] != "]":
                j += 1
            if j >= n:
                out.append(re.escape("["))
                i += 1
                continue
            inner = pat[i + 1 : j]
            if inner.startswith("!"):
                inner = "^" + inner[1:]
            out.append("[" + inner.replace("\\", "\\\\") + "]")
            i = j + 1
            continue
        out.append(re.escape(ch))
        i += 1
    return "".join(out)


def compile_glob(pattern: str) -> re.Pattern[str]:
    """Скомпилировать glob в регулярное выражение (с кэшем)."""
    cached = _GLOB_CACHE.get(pattern)
    if cached is None:
        cached = re.compile("^" + _translate(pattern) + "$")
        _GLOB_CACHE[pattern] = cached
    return cached


def glob_match(path: str, pattern: str) -> bool:
    """Сопоставить путь (POSIX, относительно корня) с glob-шаблоном."""
    return compile_glob(pattern).match(to_posix(path).lstrip("/")) is not None


def matches_any(path: str, patterns: Sequence[str]) -> str | None:
    """Первый совпавший шаблон или None."""
    for pat in patterns:
        if glob_match(path, pat):
            return pat
    return None


def prune_dir(dir_rel: str, patterns: Sequence[str]) -> str | None:
    """Стоит ли не заходить в каталог.

    Если шаблон вида '**/vendor/**' — сравниваем его префикс ('**/vendor') с путём
    каталога: совпал — обход каталога прекращается (производительность).
    """
    d = to_posix(dir_rel).strip("/")
    for pat in patterns:
        p = to_posix(pat)
        if p.endswith("/**"):
            prefix = p[: -len("/**")]
            if prefix and glob_match(d, prefix):
                return pat
    return None


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------


def dumps(obj: Any, indent: int | None = None) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=indent, sort_keys=False)


def loads(text: str) -> Any:
    return json.loads(text)


def write_json(path: str | Path, obj: Any, indent: int = 2) -> None:
    """Запись JSON в UTF-8 без BOM (NFR-6 / правила проекта)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps(obj, indent=indent) + "\n", encoding="utf-8", newline="\n")


def read_json(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    return loads(p.read_text(encoding="utf-8"))


def write_text(path: str | Path, text: str) -> None:
    """Запись текста в UTF-8 без BOM и без CRLF."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def append_text(path: str | Path, text: str) -> None:
    """Дописывание текста (журнал расхождений) в UTF-8 без CRLF."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# --------------------------------------------------------------------------
# Прочее
# --------------------------------------------------------------------------


def now_iso() -> str:
    """Метка времени для журналов. В идентификаторы НЕ попадает (НФТ-5)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def batched(items: Iterable[Any], size: int) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def count_lines(path: str | Path) -> int:
    """Число строк файла (без чтения в память целиком)."""
    total = 0
    with open(path, "rb") as fh:
        while True:
            block = fh.read(1 << 20)
            if not block:
                break
            total += block.count(b"\n")
    return total


# --------------------------------------------------------------------------
# Похожие имена
# --------------------------------------------------------------------------


def closest_names(needle: str, haystack: Sequence[str], limit: int = 3) -> list[str]:
    """Похожие имена из списка — для подсказок об опечатках и пропусках.

    Зачем: когда агент спрашивает колонку или таблицу, которой нет, бесполезно
    отвечать просто «нет». Нужно указать ближайшие существующие имена — именно
    так на полигоне было бы сразу видно, что вместо AuthorUserID нужен InitiatorID.

    Сравнение без внешних библиотек: сначала совпадение по подстроке (имя целиком
    входит в другое или наоборот), затем расстояние редактирования. Порядок —
    по возрастанию сложности преобразования.
    """
    if not needle:
        return []
    low = str(needle).lower().replace("_", "")
    scored: list[tuple[float, str]] = []
    for candidate in haystack:
        cand = str(candidate)
        cl = cand.lower().replace("_", "")
        if not cl or low == cl:
            continue   # точно такое же имя — не подсказка
        if low in cl or cl in low:
            scored.append((0.5, cand))
            continue
        d = _levenshtein(low, cl)
        ratio = d / max(len(low), len(cl))
        if ratio <= 0.45:
            scored.append((ratio + 1.0, cand))
    scored.sort(key=lambda kv: (kv[0], kv[1].lower()))
    return [name for _, name in scored[: max(1, limit)]]


def _levenshtein(a: str, b: str) -> int:
    """Расстояние редактирования (две строки буфера, память O(min(n,m)))."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def decode_text(data: bytes, path: str | None = None) -> str:
    """Текст файла с определением кодировки (UTF-8/CP1251/UTF-16).

    Единая точка чтения текста: до неё в четырёх местах стояло
    `data.decode("utf-8", errors="replace")`, и файл в CP1251 терял кириллицу
    целиком — поиск по русской строке отвечал «не найдено» на существующее.
    Правила определения и обоснование порогов — в `ragraf/textdec.py`.
    """
    from . import textdec

    return textdec.decode(data, path)[0]


def is_binary(path: str | Path, sniff: int = 8192) -> bool:
    """Признак двоичного файла по NUL-байту в начале."""
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(sniff)
    except OSError:
        return True


# --------------------------------------------------------------------------
# Запуск внешних программ через cmd.exe
# --------------------------------------------------------------------------
#
# Зачем это здесь, а не рядом с Joern. `.bat`/`.cmd` в Windows исполняет только
# cmd.exe, и cmd РАЗБИРАЕТ командную строку по своим правилам: `&`, `|`, `<`,
# `>`, `^`, `%`, `!` — его метасимволы. Замерено 24.10.2026 на живой .bat-обёртке:
# аргумент `a&b` без пробела разрывал команду на две (код 1, `b` исполнялся как
# отдельная команда), `a^b` доходил как `ab`, `%TEMP%\x` раскрывался в реальный
# путь. Ошибка не видна до запуска и меняет смысл команды, а не падает.
#
# Форма ниже передаёт `&`, `^`, `!` и пробелы дословно. `%` не передаётся ни в
# какой форме: в командной строке cmd его отключить нельзя, поэтому такой путь —
# отказ с причиной, а не тихая подмена значения.

# Символы, с которыми cmd.exe не справиться кавычками.
CMD_UNSAFE = ("%", '"')


class CmdArgumentError(ValueError):
    """Аргумент нельзя передать через cmd.exe без искажения.

    Отдельный тип нужен, чтобы вызывающий отличал «cmd не может передать путь»
    от «программа упала»: первое — деградация с причиной, второе — отказ
    внешней программы.
    """


def cmd_command(cmd: Sequence[str], comspec: str = "cmd.exe") -> str:
    """Собрать командную строку для cmd.exe так, чтобы аргументы дошли целыми.

    Почему не список. Python превращает список в командную строку по правилам
    MSVCRT. Там кавычка внутри аргумента экранируется обратной косой, а cmd
    обратную косую внутри кавычек литералом не считает — аргумент доходил
    искажённым (замерено 24.10.2026).

    Почему внешние кавычки и `/s`. С `/s` cmd снимает первую и последнюю кавычку
    строки и разбирает остаток по кавычкам вокруг аргументов — тогда метасимволы
    внутри кавычек литеральны. `/d` отключает AutoRun из реестра, `/v:off` —
    отложенное раскрытие `!` (иначе `!` в пути съедался бы).
    """
    for part in cmd:
        text = str(part)
        for bad in CMD_UNSAFE:
            if bad in text:
                raise CmdArgumentError(
                    f"путь или аргумент содержит «{bad}», который cmd.exe "
                    f"разбирает неисправимо: {text} — перенесите каталог в путь "
                    f"без «{bad}»; граф свойств кода в таком пути недоступен"
                )
    body = " ".join(f'"{str(part)}"' for part in cmd)
    return f'{comspec} /d /s /v:off /c "{body}"'
