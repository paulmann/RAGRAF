# -*- coding: utf-8 -*-
"""Astro: символы и вызовы из frontmatter и `<script>` — деревом TypeScript.

Зачем отдельный извлекатель. `.astro` — не язык с собственной грамматикой в
наборе RAGRAF, но его содержимое на 100 % состоит из TypeScript-островков:
frontmatter между `---` и каждый блок `<script>`. До 22.11.2026 таких файлов в
индексе не было вовсе (замечание клиента: 58 страниц и компонентов, «нужный
парсер уже установлен»).

Как. Из файла берутся ТОЛЬКО области кода, а остальное заменяется пробелами с
сохранением переводов строк — поэтому разбор видит корректный TypeScript, а
номера строк совпадают с исходным файлом: `path:line` остаётся проверяемым.
Разметка в символы не превращается: она не код, и выдумывать по ней функции
значит выдавать одно за другое.
"""
from __future__ import annotations

import re

from .generic import PROFILES, GenericExtractor
from .php_ast import FileExtraction
from .. import textdec, util

_FRONTMATTER = re.compile(r"\A\ufeff?(?:<!--.*?-->\s*)?---\s*\n(.*?)\n---\s*\n",
                          re.S)
_SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.S | re.I)


def _blank(text: str) -> str:
    """Замаскировать область: переводы строк остаются, остальное — пробелы."""
    return "".join("\n" if ch == "\n" else " " for ch in text)


def code_regions(text: str) -> list[tuple[int, str]]:
    """Области кода файла: `[(номер первой строки области, текст)]`."""
    regions: list[tuple[int, str]] = []
    match = _FRONTMATTER.match(text)
    if match:
        start = text.count("\n", 0, match.start(1)) + 1
        regions.append((start, match.group(1)))
    for script in _SCRIPT.finditer(text):
        start = text.count("\n", 0, script.start(1)) + 1
        regions.append((start, script.group(1)))
    return regions


def masked_source(text: str) -> str:
    """Текст той же длины и с теми же переводами строк, где код — только код.

    TypeScript-разбор склеивает области в один файл: между ними остаются пустые
    строки, поэтому позиции узлов совпадают с исходником, и `path:line` не врёт.
    """
    chars = list(_blank(text))
    for start, body in code_regions(text):
        offset = 0
        for index, line in enumerate(text.split("\n"), 1):
            if index == start:
                for position, char in enumerate(body.replace("\r", "")):
                    target = offset + position
                    if target < len(chars):
                        chars[target] = char
                break
            offset += len(line) + 1
    return "".join(chars)


class AstroExtractor:
    """Символы и вызовы Astro-файла через профиль TypeScript."""

    def __init__(self) -> None:
        self._ts = GenericExtractor(PROFILES["typescript"])

    def extract(self, path: str, data: bytes) -> FileExtraction:
        text, enc = textdec.decode(data, path)
        source = masked_source(text)
        result = self._ts.extract(util.to_posix(path), source.encode("utf-8"))
        result.lang = "astro"
        result.lines = textdec.line_count(data, text, enc)
        result.encoding = enc
        # Разбор выполнен деревом — но не языком Astro, а его кодом: это
        # называется прямо, чтобы «astro разобран» не читалось как «разметка
        # страницы стала графом».
        result.parse_status = "ok" if result.parse_status == "ok" else result.parse_status
        note = "astro: разобраны frontmatter и <script> как TypeScript"
        result.parse_note = f"{result.parse_note}; {note}" if result.parse_note else note
        return result
