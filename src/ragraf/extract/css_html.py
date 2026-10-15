"""Извлечение объектов из CSS и разметки (HTML/HTM).

Зачем отдельный модуль
    Значительная часть интерфейса этих проектов живёт вне PHP: правила оформления
    в CSS-файлах и готовая разметка в шаблонах. Пока эти файлы не разбирались,
    из индекса выпадал целый слой: нельзя было спросить «где определён класс и
    где он используется», и нельзя было заметить расхождение между ними.

    Именно расхождение и оказалось самым ценным. На полигоне:
      * 9 классов страницы входа использовались в разметке и не были описаны
        в стилях (найдено раньше вручную, теперь находится автоматически);
      * 32 класса подсистемы agreements использовались в разметке, и ни одного
        правила для них не существовало.

    Обе страницы при этом работали: браузер применяет оформление по умолчанию,
    и отсутствие правил не даёт ошибки. Дефект виден только сопоставлением.

Как устроено сопоставление «определение ↔ использование»
    Имя класса — общий ключ. Узел класса создаётся один раз и адресуется парой
    (вид, имя, файл), где для использования файл пустой. Тогда:
      * `defines_class` — ребро от CSS-файла к узлу класса;
      * `uses_class` — ребро от файла разметки к узлу класса с тем же именем.
    Пустой файл в ключе выбран намеренно: иначе использование в разных файлах
    создавало бы разные узлы, и вопрос «где определён и где использован» пришлось
    бы решать сопоставлением имён вручную. Обратная сторона: узел класса в графе
    один на все файлы использования — поэтому операции обязаны смотреть на рёбра,
    а не на поле `path` узла.

Что НЕ делается
    * не строится каскад и не вычисляется применённое оформление: для этого нужен
      браузерный движок, а не разбор текста;
    * не отслеживаются классы, добавляемые из JS строками: в этих корпусах такие
      места есть (в пилоте разметка собирается в JS-шаблонах), и они помечаются
      как непокрытые, а не додумываются;
    * не разбираются препроцессоры (SASS/LESS): их в этих корпусах нет.
"""

from __future__ import annotations

import re

from .php_ast import (
    FILE_PREFIX,
    INCLUDE_ATTR_RE,
    SSI_INCLUDE_RE,
    ChunkRec,
    EdgeRec,
    FileExtraction,
    SymbolRec,
    TextRec,
    file_ref_edges,
    placeholder_facts,
    route_edges,
    routes_from_text,
)
from .. import textdec, util

# Виды узлов.
CLASS_KIND = "css_class"
VAR_KIND = "css_var"
ID_KIND = "html_id"
RULE_KIND = "css_rule"

# Признак «файл определения неизвестен» в ключе узла класса. Используется
# и в CSS-извлекателе, и в разметке, чтобы ключ совпадал.
NO_FILE = ""

CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
RULE_HEAD_RE = re.compile(r"([^{}]*?)\{", re.DOTALL)
CLASS_TOKEN_RE = re.compile(r"\.(-?[A-Za-z_][A-Za-z0-9_-]*)")
VAR_DECL_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")
VAR_USE_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)")

CLASS_ATTR_RE = re.compile(r"""class\s*=\s*["']([^"']*)["']""")
ID_ATTR_RE = re.compile(r"""\bid\s*=\s*["']([^"']+)["']""")
AT_RULE_RE = re.compile(r"@(?:media|supports|container|layer)\b[^{]*", re.IGNORECASE)

# Имя, допустимое как класс оформления.
CLASS_NAME_RE = re.compile(r"-?[A-Za-z_][A-Za-z0-9_-]*")


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def class_node_id(name: str) -> str:
    """Идентификатор узла класса. Один на все файлы — по имени.

    Почему без файла: использование класса в разметке не знает, в каком файле
    он определён. Если бы файл входил в ключ, определение и использование
    давали бы разные узлы и никогда не сопоставлялись.
    """
    return util.stable_id(CLASS_KIND, name, NO_FILE)


class CssExtractor:
    """Разбор CSS-файла: классы, переменные оформления, контекст правил."""

    def extract(self, path: str, data: bytes) -> FileExtraction:
        text, enc = textdec.decode(data, path)
        res = FileExtraction(
            path=path,
            lines=textdec.line_count(data, text, enc),
            sha256=util.sha256_bytes(data),
            lang="css",
            encoding=enc,
            parse_status="ok",
        )
        file_id = util.stable_id("file", path, path)
        res.symbols.append(
            SymbolRec(
                node_id=file_id, kind="file", name=path, fqn=path,
                start_line=1, end_line=res.lines, start_byte=0, end_byte=len(data),
                signature=None, parent_fqn=None, props={"lang": "css"},
            )
        )

        clean = CSS_COMMENT_RE.sub(" ", text)
        at_rules = [(m.start(), m.group(0).strip()) for m in AT_RULE_RE.finditer(clean)]

        def context_of(pos: int) -> str | None:
            """Ближайшее предшествующее ат-правило: видно, внутри какого условия
            объявлен класс (`@media (max-width: 720px)`)."""
            current = None
            for start, raw in at_rules:
                if start > pos:
                    break
                current = raw
            return current

        seen_classes: set[str] = set()
        for m in RULE_HEAD_RE.finditer(clean):
            head = m.group(1).strip()
            if not head or head.startswith("@"):
                continue
            line = _line_of(text, m.start())
            context = context_of(m.start())
            for cm in CLASS_TOKEN_RE.finditer(head):
                name = cm.group(1)
                node_id = class_node_id(name)
                if name in seen_classes:
                    continue
                seen_classes.add(name)
                res.symbols.append(
                    SymbolRec(
                        node_id=node_id, kind=CLASS_KIND, name=name,
                        fqn=f"css:{name}", start_line=line,
                        end_line=line, start_byte=0, end_byte=0,
                        signature=head[:200], parent_fqn=None,
                        props={"selector": head[:200], "context": context},
                    )
                )
                res.edges.append(
                    EdgeRec(
                        src=file_id, dst=node_id, kind="defines_class",
                        provenance="stated", evidence_line=line, confidence=1.0,
                        props={"selector": head[:120]},
                    )
                )

        seen_vars: set[str] = set()
        for m in VAR_DECL_RE.finditer(text):
            name = m.group(1)
            if name in seen_vars:
                continue
            seen_vars.add(name)
            res.symbols.append(
                SymbolRec(
                    node_id=util.stable_id(VAR_KIND, name, path), kind=VAR_KIND,
                    name=name, fqn=f"{path}:{name}",
                    start_line=_line_of(text, m.start()),
                    end_line=_line_of(text, m.start()), start_byte=0, end_byte=0,
                    signature=name, parent_fqn=None, props={"declared": True},
                )
            )
        # Использование переменных: если переменная используется, но не объявлена,
        # узел создаётся с признаком declared=False — это и есть расхождение.
        for m in VAR_USE_RE.finditer(text):
            name = m.group(1)
            node_id = util.stable_id(VAR_KIND, name, path)
            if name not in seen_vars:
                seen_vars.add(name)
                res.symbols.append(
                    SymbolRec(
                        node_id=node_id, kind=VAR_KIND, name=name,
                        fqn=f"{path}:{name}",
                        start_line=_line_of(text, m.start()),
                        end_line=_line_of(text, m.start()), start_byte=0, end_byte=0,
                        signature=name, parent_fqn=None, props={"declared": False},
                    )
                )
            res.edges.append(
                EdgeRec(
                    src=file_id, dst=node_id, kind="uses_var", provenance="stated",
                    evidence_line=_line_of(text, m.start()), confidence=1.0,
                )
            )
        return res

    def chunks(self, res: FileExtraction, data: bytes, **kwargs) -> list[ChunkRec]:
        """Чанки CSS: одно правило — один чанк.

        Почему не окнами по строкам: правило оформления осмысленно целиком.
        Разрезанное пополам, оно даёт обрывок селектора и обрывок значений —
        такой чанк не совпадает ни с одним осмысленным запросом.
        """
        text = textdec.decode(data, res.path)[0]
        lines = text.splitlines()
        clean = CSS_COMMENT_RE.sub(" ", text)
        out: list[ChunkRec] = []
        for m in RULE_HEAD_RE.finditer(clean):
            head = m.group(1).strip()
            if not head or head.startswith("@"):
                continue
            start = _line_of(text, m.start())
            close = clean.find("}", m.end())
            end = _line_of(text, close if close > 0 else m.end())
            body = "\n".join(lines[max(0, start - 1): max(start, end)])
            payload = f"{head} {{ {body} }}"
            out.append(
                ChunkRec(
                    chunk_id=util.stable_id(RULE_KIND, head, res.path, start),
                    source_kind=RULE_KIND, path=res.path, node_id=None,
                    start_line=start, end_line=max(start, end), lang="css",
                    text=payload[:6000], text_hash=util.sha256_text(payload),
                )
            )
        if not out:
            body = "\n".join(lines[:120])
            out.append(
                ChunkRec(
                    chunk_id=util.stable_id("file", res.path, res.path, 1),
                    source_kind="file", path=res.path, node_id=None, start_line=1,
                    end_line=min(len(lines) or 1, 120), lang="css", text=body,
                    text_hash=util.sha256_text(body),
                )
            )
        return out


class HtmlExtractor:
    """Разбор разметки: какой класс и где используется."""

    def extract(self, path: str, data: bytes) -> FileExtraction:
        text, enc = textdec.decode(data, path)
        res = FileExtraction(
            path=path,
            lines=textdec.line_count(data, text, enc),
            sha256=util.sha256_bytes(data),
            lang="html",
            encoding=enc,
            parse_status="ok",
        )
        file_id = util.stable_id("file", path, path)
        res.symbols.append(
            SymbolRec(
                node_id=file_id, kind="file", name=path, fqn=path,
                start_line=1, end_line=res.lines, start_byte=0, end_byte=len(data),
                signature=None, parent_fqn=None, props={"lang": "html"},
            )
        )

        seen: set[str] = set()
        dynamic: set[str] = set()
        for m in CLASS_ATTR_RE.finditer(text):
            line = _line_of(text, m.start())
            for name in m.group(1).split():
                if not CLASS_NAME_RE.fullmatch(name):
                    continue
                # Обрубок от конкатенации: `class="z-agr-status--<?= $s ?>"` даёт
                # имя, оканчивающееся на дефис. Классом оно не является, и считать
                # его «отсутствующим в CSS» было бы ложным срабатыванием.
                if name.endswith("-") or name.endswith("_"):
                    dynamic.add(name)
                    continue
                node_id = class_node_id(name)
                if name not in seen:
                    seen.add(name)
                    res.symbols.append(
                        SymbolRec(
                            node_id=node_id, kind=CLASS_KIND, name=name,
                            fqn=f"css:{name}", start_line=line,
                            end_line=line, start_byte=0, end_byte=0,
                            signature=None, parent_fqn=None,
                            props={"used_only": True},
                        )
                    )
                res.edges.append(
                    EdgeRec(
                        src=file_id, dst=node_id, kind="uses_class",
                        provenance="stated", evidence_line=line, confidence=1.0,
                        props={"in": path},
                    )
                )

        if dynamic:
            res.texts.append(
                TextRec(
                    chunk_id=util.stable_id("dyn", path, len(dynamic)),
                    owner_node_id=None, start_line=1, end_line=1,
                    text_kind="note",
                    raw="dynamic_classes:" + ",".join(sorted(dynamic)),
                )
            )

        # --- P1-3: точки входа, подключения и ключи переводов ---
        # Разметка — единственное место, где видно, КАКОЙ файл подключён:
        # `src="lib/x.js"`, `<script src>`, `action="upload.php"`,
        # `<!--#include virtual="..."-->`, `fetch('...')`. В графе рёбер для
        # этого нет — есть строки, и до этой правки они не разбирались.
        res.routes = routes_from_text(res.path, text)
        route_edges(res, file_id)
        placeholder_facts(res, text, file_id, res.path)
        file_ref_edges(res, file_id, text)
        for rx, kind in ((INCLUDE_ATTR_RE, "include"), (SSI_INCLUDE_RE, "include")):
            for m in rx.finditer(text):
                target = m.group(1).strip()
                if not target:
                    continue
                line = text.count("\n", 0, m.start()) + 1
                res.edges.append(
                    EdgeRec(src=file_id, dst=f"{FILE_PREFIX}{target}", kind="imports",
                            provenance="derived", evidence_line=line, confidence=0.9,
                            props={"kind": kind, "in": path})
                )
                res.includes.append({"path": target, "line": line, "kind": kind,
                                     "literal": True})
        return res

    def chunks(self, res: FileExtraction, data: bytes, **kwargs) -> list[ChunkRec]:
        """Чанки разметки: окна по строкам И по символам одновременно.

        Почему два условия. Замер на документации и сгенерированной разметке
        показал чанк в 44 771 токен при лимите 8 000: одна «строка» такой
        разметки может содержать целую таблицу. Лимит только по строкам не
        ограничивает объём, поэтому второе условие — по символам.
        """
        text = textdec.decode(data, res.path)[0]
        lines = text.splitlines()
        max_lines = int(kwargs.get("max_lines") or 120)
        # 8000 токенов ≈ 33 600 символов; берётся запас 24 000.
        max_chars = 24000
        out: list[ChunkRec] = []

        block: list[str] = []
        block_start = 0
        used = 0
        for index, line in enumerate(lines):
            over_chars = used + len(line) > max_chars and block
            over_lines = len(block) >= max_lines
            if over_chars or over_lines:
                body = "\n".join(block)
                out.append(
                    ChunkRec(
                        chunk_id=util.stable_id("html_window", res.path, block_start,
                                                len(block)),
                        source_kind="file", path=res.path, node_id=None,
                        start_line=block_start + 1, end_line=block_start + len(block),
                        lang="html", text=body, text_hash=util.sha256_text(body),
                    )
                )
                block = []
                block_start = index
                used = 0
            block.append(line)
            used += len(line) + 1

        if block:
            body = "\n".join(block)
            out.append(
                ChunkRec(
                    chunk_id=util.stable_id("html_window", res.path, block_start,
                                            len(block)),
                    source_kind="file", path=res.path, node_id=None,
                    start_line=block_start + 1, end_line=block_start + len(block),
                    lang="html", text=body, text_hash=util.sha256_text(body),
                )
            )
        return out or [
            ChunkRec(
                chunk_id=util.stable_id("file", res.path, res.path, 1),
                source_kind="file", path=res.path, node_id=None, start_line=1,
                end_line=1, lang="html", text="", text_hash=util.sha256_text(""),
            )
        ]


class MultiExtractor:
    """Выбор извлекателя по расширению файла.

    Нужен, чтобы индексатор не знал о нескольких языках: он вызывает один метод,
    а тот направляет файл нужному разбору. Дешевле, чем менять сигнатуру
    индексатора и всех его вызовов.

    Раньше здесь были только три разбора: PHP, CSS, разметка. Всё остальное
    уходило в PHP-разбор по умолчанию — то есть .js, .py, .cs разбирались
    грамматикой PHP и давали мусор. Теперь для каждого языка с грамматикой
    берётся свой профиль (extract/generic.py), а неизвестные расширения
    попадают туда же по расширению, а не «в PHP».

    Ленивость важна: парсеры создаются при первом обращении к языку. На корпусе
    из PHP-файлов ни один лишний парсер не будет создан, а на смешанном
    корпусе создадутся только нужные.
    """

    def __init__(self) -> None:
        from .php_ast import PhpExtractor

        self.php = PhpExtractor()
        self.css = CssExtractor()
        self.html = HtmlExtractor()
        self._generic: dict[str, object] = {}

    # --- выбор языка ---

    @staticmethod
    def lang_for(path: str) -> str:
        """Определить язык файла по расширению.

        Имена языков берутся из карты расширений config.py — единственной точки
        правды. Если расширение неизвестно, возвращается пустая строка, и файл
        обрабатывается текстовым путём.
        """
        from ..config import LANG_BY_EXT

        low = path.lower()
        for ext, lang in LANG_BY_EXT.items():
            if low.endswith(ext):
                return lang
        return ""

    def generic_for(self, lang: str):
        """Извлекатель для языка с разбором дерева (или None, если его нет)."""
        from .generic import PROFILES, GenericExtractor

        if lang not in PROFILES:
            return None
        if lang not in self._generic:
            self._generic[lang] = GenericExtractor(PROFILES[lang])
        return self._generic[lang]

    # --- разбор ---

    def extract(self, path: str, data: bytes) -> FileExtraction:
        low = path.lower()
        if low.endswith(".css"):
            return self.css.extract(path, data)
        if low.endswith((".htm", ".html")):
            return self.html.extract(path, data)
        if low.endswith((".php", ".phtml", ".inc")):
            return self.php.extract(path, data)
        # Остальные языки — по профилю грамматики.
        lang = self.lang_for(path)
        extractor = self.generic_for(lang)
        if extractor is not None:
            return extractor.extract(path, data)
        # Язык без профиля: файл не разбирается деревом. Возвращается пустое
        # извлечение — индексатор построит по нему текстовые чанки.
        text, enc = textdec.decode(data, path)
        return FileExtraction(
            path=util.to_posix(path),
            sha256=util.sha256_bytes(data),
            lines=textdec.line_count(data, text, enc),
            lang=lang or "text",
            encoding=enc,
            parse_status="text_only",
            parse_note=f"разбор деревом для языка «{lang or 'неизвестно'}» не предусмотрен",
        )

    # --- чанки ---

    def chunks(self, res: FileExtraction, data: bytes, **kwargs) -> list[ChunkRec]:
        low = res.path.lower()
        # Байтовые смещения в извлечении относятся к UTF-8-буферу (см.
        # `extract`: перекодирование делается до разбора). Поэтому нарезка
        # чанков получает тот же буфер, а не исходные байты: иначе у файла
        # в CP1251 границы символов сдвинулись бы и чанк взял чужой код.
        data = textdec.transcode(data, res.path)[0]
        if low.endswith(".css"):
            return self.css.chunks(res, data, **kwargs)
        if low.endswith((".htm", ".html")):
            return self.html.chunks(res, data, **kwargs)
        if low.endswith((".php", ".phtml", ".inc")):
            from .php_ast import build_chunks

            return build_chunks(res, data, **kwargs)
        extractor = self.generic_for(res.lang)
        if extractor is not None and res.parse_status != "text_only":
            from .generic import chunks_for_language

            return chunks_for_language(res, data, **kwargs)
        # Текстовый путь: чанки строит индексатор (_text_chunks). Сюда
        # возвращается пустой список, чтобы не дублировать логику дробления
        # по строкам и символам.
        return []
