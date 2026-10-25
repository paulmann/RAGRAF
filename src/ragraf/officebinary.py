"""Разбор старых двоичных документов Office: .doc, .xls, .ppt, .msg, .wps.

Зачем свой разбор, а не библиотека
    На этой машине нет ни LibreOffice, ни antiword, ни olefile, а форматы
    Word 6/95/97-2003, Excel BIFF и PowerPoint 97 — это то, что десятилетиями
    лежит в рабочих каталогах. Считать их «неподдерживаемыми» значило бы
    выкинуть из индекса именно те документы, которые чаще всего и нужны.

    Поэтому здесь собственный разборщик контейнера OLE/CFB и три читателя:
    Word (таблица фрагментов), Excel (таблица строк книги) и PowerPoint
    (записи с текстом). Внешних зависимостей нет — только стандартная
    библиотека, поэтому путь работает всегда.

Честность вместо догадки
    * битый контейнер даёт `OfficeError` с причиной, а не пустой текст;
    * неизвестный вариант формата (например, Word 6 без таблицы фрагментов)
      читается по запасному пути и это отмечается в примечании;
    * если текст не найден, вызывающий получает пустую строку и причину —
      «документ без текста» отличается от «разбор не удался».

Проверено на реальных файлах этой машины (tests/test_office_binary.py):
    1/BCT-M 600_600.doc  — Word 97, 3 фрагмента, текст извлекается;
    a.batteryservice.ru/backup/mail/import/Letters.xls — BIFF8,
    словарь строк 214 записей.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# --- пределы: документ не должен превращаться в минуты работы ---------------
MAX_STREAM_BYTES = 64 * 1024 * 1024
MAX_SHEETS = 64
MAX_ROWS = 20000
MAX_SLIDES = 400
MAX_TEXT_CHARS = 8 * 1024 * 1024

FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE


class OfficeError(Exception):
    """Контейнер или формат не разобраны. Причина — в тексте исключения."""


# --------------------------------------------------------------------------
# Контейнер OLE / Compound File Binary
# --------------------------------------------------------------------------
class CompoundFile:
    """Чтение потоков из OLE-контейнера (CFB).

    Реализованы ровно те части спецификации, без которых не достать текст:
    заголовок, таблица размещения (FAT/DIFAT), каталог и мини-поток. Ничего
    не пишется и не изменяется — контейнер открывается только на чтение.
    """

    SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

    def __init__(self, data: bytes) -> None:
        if len(data) < 512 or data[:8] != self.SIGNATURE:
            raise OfficeError("это не OLE-контейнер (нет подписи CFB)")
        self.data = data
        self.sector_shift = struct.unpack_from("<H", data, 0x1E)[0]
        self.mini_shift = struct.unpack_from("<H", data, 0x20)[0]
        if not 7 <= self.sector_shift <= 16 or not 4 <= self.mini_shift <= 12:
            raise OfficeError(
                f"недопустимый размер сектора: {self.sector_shift}/{self.mini_shift}"
            )
        self.sector_size = 1 << self.sector_shift
        self.mini_size = 1 << self.mini_shift
        self.n_fat = struct.unpack_from("<I", data, 0x2C)[0]
        self.dir_start = struct.unpack_from("<I", data, 0x30)[0]
        self.mini_cutoff = struct.unpack_from("<I", data, 0x38)[0]
        self.minifat_start = struct.unpack_from("<I", data, 0x3C)[0]
        self.n_minifat = struct.unpack_from("<I", data, 0x40)[0]
        self.difat_start = struct.unpack_from("<I", data, 0x44)[0]
        self.n_difat = struct.unpack_from("<I", data, 0x48)[0]
        self._load_difat()
        self._load_fat()
        self._load_dir()
        self._load_minifat()

    def sector(self, index: int) -> bytes:
        offset = 512 + index * self.sector_size
        return self.data[offset:offset + self.sector_size]

    def _load_difat(self) -> None:
        per = self.sector_size // 4 - 1
        self.fat_sectors: list[int] = list(
            struct.unpack_from("<109I", self.data, 0x4C)
        )
        seen: set[int] = set()
        sector = self.difat_start
        while sector not in (ENDOFCHAIN, FREESECT) and sector not in seen:
            if sector * self.sector_size + 512 > len(self.data):
                break
            seen.add(sector)
            block = self.sector(sector)
            if len(block) < self.sector_size:
                break
            values = struct.unpack_from(f"<{per + 1}I", block, 0)
            self.fat_sectors.extend(values[:per])
            sector = values[per]

    def _load_fat(self) -> None:
        count = self.sector_size // 4
        self.fat: list[int] = []
        limit = min(len(self.fat_sectors), max(self.n_fat, 1) + 2)
        for sector in self.fat_sectors[:limit]:
            if sector in (ENDOFCHAIN, FREESECT):
                continue
            block = self.sector(sector)
            if len(block) < self.sector_size:
                continue
            self.fat.extend(struct.unpack_from(f"<{count}I", block, 0))
        if not self.fat:
            raise OfficeError("таблица размещения пуста: контейнер повреждён")

    def chain(self, start: int) -> list[int]:
        out: list[int] = []
        sector = start
        seen: set[int] = set()
        while sector not in (ENDOFCHAIN, FREESECT):
            if sector in seen or len(out) > len(self.fat) + 8:
                raise OfficeError("петля в цепочке секторов: контейнер повреждён")
            seen.add(sector)
            out.append(sector)
            if sector >= len(self.fat):
                break
            sector = self.fat[sector]
        return out

    def _read_chain(self, start: int, size: int | None = None) -> bytes:
        chunks = [self.sector(s) for s in self.chain(start)]
        blob = b"".join(chunks)
        return blob if size is None else blob[:size]

    def _load_dir(self) -> None:
        blob = self._read_chain(self.dir_start)
        self.entries: list[dict] = []
        for offset in range(0, max(0, len(blob) - 127), 128):
            raw = blob[offset:offset + 128]
            name_len = struct.unpack_from("<H", raw, 0x40)[0]
            keep = max(0, min(name_len, 64) - 2)
            name = raw[:keep].decode("utf-16-le", "replace")
            self.entries.append(
                {
                    "name": name,
                    "type": raw[0x42],
                    "start": struct.unpack_from("<I", raw, 0x74)[0],
                    "size": struct.unpack_from("<Q", raw, 0x78)[0],
                }
            )
        root = next((e for e in self.entries if e["type"] == 5), None)
        self.mini_stream = b""
        if root and root["size"]:
            self.mini_stream = self._read_chain(root["start"], min(
                root["size"], MAX_STREAM_BYTES
            ))

    def _load_minifat(self) -> None:
        self.minifat: list[int] = []
        if self.minifat_start in (ENDOFCHAIN, FREESECT):
            return
        blob = self._read_chain(self.minifat_start)
        if blob:
            self.minifat = list(struct.unpack_from(f"<{len(blob) // 4}I", blob, 0))

    def names(self) -> list[str]:
        return [e["name"] for e in self.entries if e["type"] == 2]

    def stream(self, name: str) -> bytes | None:
        """Поток по точному имени. None — потока нет."""
        for entry in self.entries:
            if entry["type"] != 2 or entry["name"] != name:
                continue
            size = min(entry["size"], MAX_STREAM_BYTES)
            if size < self.mini_cutoff:
                out = bytearray()
                sector = entry["start"]
                seen: set[int] = set()
                while sector not in (ENDOFCHAIN, FREESECT):
                    if sector in seen:
                        raise OfficeError("петля в мини-потоке")
                    seen.add(sector)
                    offset = sector * self.mini_size
                    out += self.mini_stream[offset:offset + self.mini_size]
                    if sector >= len(self.minifat):
                        break
                    sector = self.minifat[sector]
                return bytes(out[:size])
            return self._read_chain(entry["start"], size)
        return None


# --------------------------------------------------------------------------
# Кодировка однобайтовых фрагментов
# --------------------------------------------------------------------------
def _decode_8bit(raw: bytes) -> str:
    """Однобайтовый текст: выбрать кодировку по содержимому.

    Зачем выбор, а не одна кодировка. Старые .doc не хранят кодировку в самом
    тексте: она лежит в сводке документа, которую читать отдельно дорого.
    Русские документы обычно в cp1251, западные — в cp1252. Обе однобайтовые,
    поэтому ошибки декодирования не возникает, а мусор возникает: cp1252
    поверх кириллицы даёт «Ðåçóëüòàò».

    Критерий: доля символов, которые в языке текста почти не встречаются, —
    управляющие коды и знаки замены. Берётся вариант с меньшей долей.
    """
    if not raw:
        return ""
    best_text = ""
    best_score = -1.0
    for codec in ("cp1251", "cp1252", "cp866"):
        try:
            text = raw.decode(codec)
        except UnicodeDecodeError:
            continue
        bad = sum(
            1 for ch in text
            if (ord(ch) < 32 and ch not in "\r\n\t") or ch == "\ufffd"
        )
        good = sum(
            1 for ch in text
            if ch.isalpha() or ch.isdigit() or ch in " \r\n\t.,;:!?()-—«»\"'/\\"
        )
        score = (good - bad * 4) / max(len(text), 1)
        if score > best_score:
            best_score = score
            best_text = text
    return best_text


def _clean_text(text: str) -> str:
    """Убрать служебные коды Word, сохранив границы строк и ячеек."""
    out = []
    for ch in text:
        code = ord(ch)
        if ch in "\r\n\t":
            out.append("\n" if ch == "\r" else ch)
        elif code == 7:            # конец ячейки таблицы
            out.append("\t")
        elif code < 32:            # поля, сноски и прочая служебная разметка
            continue
        else:
            out.append(ch)
    text = "".join(out)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


# --------------------------------------------------------------------------
# Word (.doc, .wps) — Word 6/95 и Word 97-2003
# --------------------------------------------------------------------------
@dataclass
class WordResult:
    text: str
    note: str = ""


def word_text(data: bytes) -> WordResult:
    """Текст документа Word из OLE-контейнера."""
    cfb = CompoundFile(data)
    doc = cfb.stream("WordDocument")
    if doc is None:
        raise OfficeError("в контейнере нет потока WordDocument")
    if len(doc) < 0x200:
        raise OfficeError("поток WordDocument короче заголовка")

    ident = struct.unpack_from("<H", doc, 0)[0]
    n_fib = struct.unpack_from("<H", doc, 2)[0]
    flags = struct.unpack_from("<H", doc, 0x0A)[0]
    fc_min, fc_mac = struct.unpack_from("<II", doc, 0x18)
    table_name = "1Table" if (flags >> 9) & 1 else "0Table"
    table = cfb.stream(table_name) or b""
    note = f"Word: nFib={n_fib}, поток таблицы {table_name}"
    if ident != 0xA5EC:
        note += f", неожиданный идентификатор {ident:#06x}"

    # Word 97 и новее: текст разложен по фрагментам (piece table) в CLX.
    if n_fib >= 0x00C1 and table:
        clx_off = 0x01A2
        fc_clx, lcb_clx = struct.unpack_from("<II", doc, clx_off)
        if 0 < lcb_clx and fc_clx + lcb_clx <= len(table):
            text = _word97_text(doc, table[fc_clx:fc_clx + lcb_clx])
            if text:
                return WordResult(_clean_text(text), note + ", таблица фрагментов")

    # Запасной путь: непрерывный кусок от fcMin до fcMac (Word 6/95 и
    # документы, у которых таблица фрагментов не читается).
    raw = doc[fc_min:min(fc_mac, len(doc))]
    if not raw:
        return WordResult("", note + ", текстового фрагмента нет")
    if raw.count(b"\x00") > len(raw) // 3:
        text = raw.decode("utf-16-le", "replace")
    else:
        text = _decode_8bit(raw)
    return WordResult(_clean_text(text), note + ", непрерывный фрагмент")


def _word97_text(doc: bytes, clx: bytes) -> str:
    """Разобрать CLX: найти PlcPcd и собрать текст по фрагментам."""
    pos = 0
    plc = b""
    while pos < len(clx):
        kind = clx[pos]
        if kind == 0x01:
            if pos + 3 > len(clx):
                break
            size = struct.unpack_from("<H", clx, pos + 1)[0]
            pos += 3 + size
            continue
        if kind == 0x02:
            if pos + 5 > len(clx):
                break
            size = struct.unpack_from("<I", clx, pos + 1)[0]
            plc = clx[pos + 5:pos + 5 + size]
            break
        break
    if len(plc) < 16:
        return ""
    pieces = (len(plc) - 4) // 12
    if pieces <= 0 or pieces > 100000:
        return ""
    cps = struct.unpack_from(f"<{pieces + 1}I", plc, 0)
    parts: list[str] = []
    base = 4 * (pieces + 1)
    for index in range(pieces):
        pcd = plc[base + index * 8:base + index * 8 + 8]
        if len(pcd) < 8:
            break
        fc_raw = struct.unpack_from("<I", pcd, 2)[0]
        compressed = bool(fc_raw & 0x40000000)
        fc = fc_raw & 0x3FFFFFFF
        chars = cps[index + 1] - cps[index]
        if chars <= 0:
            continue
        if compressed:
            fc //= 2
            chunk = doc[fc:fc + chars]
            parts.append(_decode_8bit(chunk))
        else:
            chunk = doc[fc:fc + chars * 2]
            parts.append(chunk.decode("utf-16-le", "replace"))
        if sum(len(p) for p in parts) > MAX_TEXT_CHARS:
            break
    return "".join(parts)


# --------------------------------------------------------------------------
# Excel (.xls, .et) — BIFF8
# --------------------------------------------------------------------------
@dataclass
class SheetResult:
    text: str
    note: str = ""


def excel_text(data: bytes) -> SheetResult:
    """Текст книги Excel из OLE-контейнера (BIFF8).

    Разбор идёт по всем листам. Это не мелочь: книга BIFF8 устроена как
    глобальная часть (имена листов, таблица строк, стили), за которой идут
    блоки листов, и КАЖДЫЙ блок заканчивается записью EOF (0x000A). Первая
    версия останавливалась на первом EOF — то есть на конце глобальной части.
    Замер на реальном файле (Letters.xls): словарь строк читался (152 записи),
    а ячеек было ноль, и отчёт честно говорил «только формулы», хотя книга
    полна данных. Поэтому EOF больше не прерывает разбор.
    """
    cfb = CompoundFile(data)
    book = cfb.stream("Workbook") or cfb.stream("Book")
    if book is None:
        raise OfficeError("в контейнере нет потока Workbook/Book")

    records = _biff_records(book)
    if not records:
        raise OfficeError("записи BIFF не читаются: книга повреждена")

    sheet_names: list[str] = []
    for rec_id, payload in records:
        if rec_id == 0x0085 and len(payload) >= 8:      # BOUNDSHEET
            sheet_names.append(_biff_short_string(payload, 6))

    sst: list[str] = []
    for index, (rec_id, payload) in enumerate(records):
        if rec_id != 0x00FC:
            continue
        blob = bytearray(payload)
        # SST продолжается записями CONTINUE: без них длинные строки рвутся.
        for rec2, payload2 in records[index + 1:]:
            if rec2 != 0x003C:
                break
            blob += payload2
        sst = _biff_sst(bytes(blob))
        break

    # Ячейки раскладываются по листам. Счёт листов идёт по BOF (0x0809):
    # первая такая запись — глобальная часть, дальше по одной на лист.
    sheets: dict[int, dict[tuple[int, int], str]] = {}
    bof_seen = 0
    current = -1
    for rec_id, payload in records:
        if rec_id == 0x0809:                            # BOF
            bof_seen += 1
            current = bof_seen - 2                      # -1 — глобальная часть
            if current >= 0:
                sheets.setdefault(current, {})
            continue
        if current < 0:
            continue
        store = sheets[current]
        if rec_id == 0x00FD and len(payload) >= 10:      # LABELSST
            row, col = struct.unpack_from("<HH", payload, 0)
            isst = struct.unpack_from("<I", payload, 6)[0]
            if 0 <= isst < len(sst):
                store[(row, col)] = sst[isst]
        elif rec_id == 0x0204 and len(payload) >= 9:     # LABEL (BIFF5/8)
            row, col = struct.unpack_from("<HH", payload, 0)
            chars = struct.unpack_from("<H", payload, 6)[0]
            flags = payload[8]
            body = payload[9:]
            if flags & 0x01:
                store[(row, col)] = body[: chars * 2].decode("utf-16-le", "replace")
            else:
                store[(row, col)] = _decode_8bit(body[:chars])
        elif rec_id == 0x0203 and len(payload) >= 14:    # NUMBER
            row, col = struct.unpack_from("<HH", payload, 0)
            store[(row, col)] = _number(struct.unpack_from("<d", payload, 6)[0])
        elif rec_id == 0x027E and len(payload) >= 10:    # RK
            row, col = struct.unpack_from("<HH", payload, 0)
            store[(row, col)] = _number(_rk(payload, 6))
        elif rec_id == 0x00BD and len(payload) >= 6:     # MULRK
            row, first_col = struct.unpack_from("<HH", payload, 0)
            count = (len(payload) - 6) // 6
            for step in range(max(0, count)):
                base = 4 + step * 6
                if base + 6 > len(payload):
                    break
                store[(row, first_col + step)] = _number(_rk(payload, base + 2))

    parts: list[str] = []
    produced = 0
    for sheet_no in sorted(sheets):
        cells = sheets[sheet_no]
        if not cells:
            continue
        name = (sheet_names[sheet_no] if sheet_no < len(sheet_names)
                else f"лист {sheet_no + 1}")
        parts.append(f"--- sheet {sheet_no + 1}: {name} ---")
        by_row: dict[int, list[tuple[int, str]]] = {}
        for (row, col), value in cells.items():
            if value:
                by_row.setdefault(row, []).append((col, value))
        for row in sorted(by_row):
            if produced >= MAX_ROWS:
                parts.append(f"[остановлено: больше {MAX_ROWS} строк]")
                break
            cells_row = [v for _c, v in sorted(by_row[row])]
            parts.append(f"строка {row + 1}: " + " | ".join(cells_row))
            produced += 1
        if produced >= MAX_ROWS:
            break

    note = (f"Excel BIFF8: листов {len(sheet_names)}, строк словаря {len(sst)}, "
            f"непустых строк {produced}")
    if not produced:
        note += ("; значений не найдено — либо книга без данных, либо в ней "
                 "только формулы без кэша")
    return SheetResult("\n".join(parts).strip(), note)


def _biff_records(blob: bytes) -> list[tuple[int, bytes]]:
    """Последовательность записей BIFF: (тип, содержимое)."""
    out: list[tuple[int, bytes]] = []
    offset = 0
    limit = len(blob)
    while offset + 4 <= limit:
        rec_id, size = struct.unpack_from("<HH", blob, offset)
        if offset + 4 + size > limit:
            # Обрыв записи — не повод терять уже прочитанное.
            break
        out.append((rec_id, blob[offset + 4:offset + 4 + size]))
        offset += 4 + size
        # EOF (0x000A) НЕ прерывает разбор: он завершает каждый блок листа,
        # а после него начинается следующий лист. Остановка на первом EOF
        # давала «книгу без данных» на книге с данными (см. excel_text).
    return out


def _biff_short_string(payload: bytes, offset: int) -> str:
    """Строка BIFF с однобайтовым или двухбайтовым флагом."""
    if offset >= len(payload):
        return ""
    chars = payload[offset]
    flags = payload[offset + 1] if offset + 1 < len(payload) else 0
    body = payload[offset + 2:]
    if flags & 0x01:
        return body[: chars * 2].decode("utf-16-le", "replace")
    return body[:chars].decode("cp1251", "replace")


def _biff_sst(blob: bytes) -> list[str]:
    """Таблица строк книги (Shared String Table).

    Разбор намеренно «мягкий»: при несоответствии длины строка пропускается,
    а не роняет книгу. Причина — CONTINUE: строка может быть разрезана между
    записями, и тогда её хвост начинается с нового байта флагов. Терять из-за
    этого все остальные строки было бы хуже, чем потерять одну.
    """
    if len(blob) < 8:
        return []
    _total, unique = struct.unpack_from("<II", blob, 0)
    unique = min(unique, 200000)
    pos = 8
    out: list[str] = []
    for _ in range(unique):
        if pos + 3 > len(blob):
            break
        chars = struct.unpack_from("<H", blob, pos)[0]
        flags = blob[pos + 2]
        pos += 3
        runs = 0
        ext = 0
        if flags & 0x08:                      # rich text: число прогонов
            if pos + 2 > len(blob):
                break
            runs = struct.unpack_from("<H", blob, pos)[0]
            pos += 2
        if flags & 0x04:                      # расширение: длина блока
            if pos + 4 > len(blob):
                break
            ext = struct.unpack_from("<I", blob, pos)[0]
            pos += 4
        if flags & 0x01:
            size = chars * 2
            if pos + size > len(blob):
                break
            text = blob[pos:pos + size].decode("utf-16-le", "replace")
        else:
            size = chars
            if pos + size > len(blob):
                break
            text = _decode_8bit(blob[pos:pos + size])
        pos += size + runs * 4 + ext
        if text and chars:
            out.append(text)
    return out


def _rk(value: bytes, offset: int) -> float:
    raw = struct.unpack_from("<I", value, offset)[0]
    number = raw & 0xFFFFFFFC
    if raw & 0x02:
        result = float(struct.unpack("<i", struct.pack("<I", number))[0] >> 2)
    else:
        result = struct.unpack("<d", struct.pack("<Q", number << 32))[0]
    if raw & 0x01:
        result /= 100.0
    return result


def _number(value: float) -> str:
    if value == int(value) and abs(value) < 10 ** 15:
        return str(int(value))
    return f"{value:g}"


# --------------------------------------------------------------------------
# PowerPoint (.ppt, .dps) — записи документа
# --------------------------------------------------------------------------
@dataclass
class SlideResult:
    text: str
    note: str = ""


#: Контейнеры PowerPoint: их содержимое — такие же записи, вложенные.
PPT_CONTAINERS = frozenset({0x03E8, 0x03EE, 0x03F0, 0x0FF0, 0x0FF1, 0x0FF2, 0x0FF3})
#: Слайд: только этот тип даёт маркер «--- slide N ---». Раньше маркер ставился
#: и на SlideListWithText, и нумерация шла дважды: 1,2,3 затем 1..17 на одном
#: файле. Адрес `path:line` должен быть один, поэтому маркер один.
PPT_SLIDE = 0x03EE
PPT_TEXT_UTF16 = 0x0FA0        # TextCharsAtom
PPT_TEXT_8BIT = 0x0FA8         # TextBytesAtom

#: Заглушки шаблона PowerPoint. В любой презентации есть образец слайда, и его
#: служебный текст лежит в файле наравне с содержимым: «Click to edit Master
#: title style» и русский вариант того же («Щелкните, чтобы изменить стили…»),
#: а также уровни списка. Замер на `Консалтинг.ppt`: 11 из 56 найденных атомов
#: были именно заглушками. В индексе они занимают место и попадают в выдачу
#: поиска как текст документа, хотя документом не являются.
PPT_PLACEHOLDERS = frozenset(
    value.casefold() for value in (
        "Click to edit Master title style",
        "Click to edit Master text styles",
        "Click to edit Master subtitle style",
        "Second level", "Third level", "Fourth level", "Fifth level",
        "Щелкните, чтобы изменить стили текста образца слайда",
        "Щелкните, чтобы изменить стили текста",
        "Второй уровень", "Третий уровень", "Четвертый уровень",
        "Пятый уровень", "*",
    )
)


def _looks_like_text(value: str) -> bool:
    """Похоже ли содержимое на текст, а не на двоичный мусор.

    Нужно потому, что ниже появляется ПОИСК АТОМОВ ПО ВСЕМУ ПОТОКУ, а не только
    обход дерева: заголовок записи может случайно совпасть с байтами картинки.
    Критерий простой и проверяемый: доля букв, цифр, пробелов и обычной
    пунктуации не меньше половины, а управляющих кодов почти нет.
    """
    if not value:
        return False
    good = 0
    for char in value:
        if char.isalnum() or char in " \t\r\n.,;:!?()\"'\u00ab\u00bb-\u2014/\\%+*#@&":
            good += 1
        elif ord(char) < 32 and char not in "\r\n\t":
            good -= 2
    return good / max(len(value), 1) >= 0.5


def _is_placeholder(value: str) -> bool:
    return value.strip().casefold() in PPT_PLACEHOLDERS


def _ppt_scan_atoms(blob: bytes) -> list[tuple[int, int, str]]:
    """Все строки текста потока со смещениями, независимо от вложенности.

    Зачем это в дополнение к обходу дерева. Замер на реальной презентации
    (`Консалтинг.ppt`, 2,98 МБ, 17 слайдов): обход нашёл все слайды, но НИ
    ОДНОГО атома текста. Причина — устройство формата: в PowerPoint 97+ текст
    фигур лежит не в контейнере слайда, а внутри структур OfficeArt
    (PPDrawing), у которых СВОЙ формат заголовка. Обход по заголовкам
    презентации туда не заходит, поэтому текст молча пропадал: слайды есть,
    текста нет, ошибки нет.

    Смещение возвращается вместе с текстом: по нему атом привязывается к
    слайду, внутри которого он лежит, и `path:line` перестаёт быть общим
    списком на всю презентацию.
    """
    out: list[tuple[int, int, str]] = []
    limit = len(blob)
    offset = 0
    while offset + 8 <= limit:
        _ver_inst, rec_type, length = struct.unpack_from("<HHI", blob, offset)
        if rec_type in (PPT_TEXT_UTF16, PPT_TEXT_8BIT) and 0 < length <= limit - offset - 8:
            body = blob[offset + 8: offset + 8 + length]
            if rec_type == PPT_TEXT_UTF16:
                value = body.decode("utf-16-le", "replace")
            else:
                value = _decode_8bit(body)
            # Один атом часто содержит несколько строк: заглушки шаблона и
            # текст слайда лежат ВМЕСТЕ («Click to edit Master text styles» +
            # «Second level» + «Щелкните, чтобы изменить…»). Замер: сравнение
            # целого атома со списком заглушек не отбросило НИ ОДНОЙ строки —
            # строки приходили склеенными. Поэтому фильтр построчный.
            for line in value.replace("\r", "\n").split("\n"):
                line = line.strip()
                if not line or _is_placeholder(line) or not _looks_like_text(line):
                    continue
                out.append((offset, len(out), line))
            offset += 8 + length
            continue
        offset += 1
    return out


def powerpoint_text(data: bytes) -> SlideResult:
    """Текст презентации PowerPoint 97-2003, разложенный по слайдам."""
    cfb = CompoundFile(data)
    stream = cfb.stream("PowerPoint Document")
    if stream is None:
        raise OfficeError("в контейнере нет потока PowerPoint Document")

    parts: list[str] = []
    walked, problems, slide_offsets = _ppt_walk(stream, 0, len(stream), parts, depth=0)

    atoms = _ppt_scan_atoms(stream)
    # Текст, найденный структурой, тоже участвует — но обычно его нет: атомы
    # лежат в OfficeArt. Оставляем, чтобы не потерять на других файлах.
    structural = [(0, index, p) for index, p in enumerate(parts)
                  if p.strip() and not p.lstrip().startswith("--- slide")
                  and not _is_placeholder(p)]
    pool = structural + atoms

    # Раскладка по слайдам: атом относится к последнему слайду, начавшемуся
    # раньше него. Смещения слайдов получены обходом, то есть измерены, а не
    # угаданы по порядку.
    buckets: dict[int, list[str]] = {index: [] for index in range(len(slide_offsets))}
    prelude: list[str] = []
    seen: set[str] = set()
    # Сортировка по (смещение, номер строки): смещение ставит слайды в порядок
    # файла, номер сохраняет порядок строк внутри одного атома.
    for offset, _sequence, value in sorted(pool, key=lambda item: (item[0], item[1])):
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        index = -1
        for number, slide_offset in enumerate(slide_offsets):
            if slide_offset <= offset:
                index = number
            else:
                break
        if index < 0:
            prelude.append(value)
        else:
            buckets[index].append(value)

    lines: list[str] = list(prelude)
    with_text = 0
    for index in range(len(slide_offsets)):
        lines.append(f"--- slide {index + 1} ---")
        if buckets[index]:
            with_text += 1
            lines.extend(buckets[index])
    body = "\n".join(line for line in lines if line.strip()).strip()

    note = (f"PowerPoint: записей {walked}, слайдов {len(slide_offsets)}, "
            f"обрывов {problems}, текстовых атомов {len(atoms)}, "
            f"слайдов с текстом {with_text}")
    if problems:
        note += "; структура неполная — часть записей пропущена"
    if not atoms and not structural:
        note += "; текстовых атомов не найдено"
    return SlideResult(_clean_text(body), note)


def _ppt_walk(blob: bytes, start: int, end: int, out: list[str],
              depth: int) -> tuple[int, int, list[int]]:
    """Обход записей. Возвращает (записи, обрывы, смещения слайдов)."""
    walked = 0
    problems = 0
    offsets: list[int] = []
    offset = start
    end = min(end, len(blob))
    while offset + 8 <= end:
        _ver_inst, rec_type, length = struct.unpack_from("<HHI", blob, offset)
        body_start = offset + 8
        body_end = body_start + length
        if length < 0 or body_end > end:
            # Длина выводит за границу контейнера: дальше идти нельзя, иначе
            # разбор «поедет» и выдаст текст из чужих записей.
            problems += 1
            break
        walked += 1
        if rec_type == PPT_SLIDE:
            offsets.append(offset)
        if rec_type in PPT_CONTAINERS and depth < 12:
            sub_walked, sub_problems, sub_offsets = _ppt_walk(
                blob, body_start, body_end, out, depth + 1
            )
            walked += sub_walked
            problems += sub_problems
            offsets.extend(sub_offsets)
        elif rec_type == PPT_TEXT_UTF16:
            out.append(blob[body_start:body_end].decode("utf-16-le", "replace"))
        elif rec_type == PPT_TEXT_8BIT:
            out.append(_decode_8bit(blob[body_start:body_end]))
        offset = body_end
    offsets.sort()
    return walked, problems, offsets


# --------------------------------------------------------------------------
# Outlook (.msg) — свойства в контейнере OLE
# --------------------------------------------------------------------------
#: Свойства письма, которые нужны для поиска: тема и тело.
MSG_PROPS = {
    "0037": "тело",
    "1000": "тема",
    "0C1A": "отправитель",
    "0C15": "кому",
}


@dataclass
class MsgResult:
    text: str
    note: str = ""


def msg_text(data: bytes) -> MsgResult:
    """Тема и тело письма Outlook из OLE-контейнера."""
    cfb = CompoundFile(data)
    found: dict[str, str] = {}
    for name in cfb.names():
        if not name.startswith("__substg1.0_"):
            continue
        prop = name[len("__substg1.0_"):]
        if len(prop) != 8:
            continue
        prop_id, kind = prop[:4], prop[4:]
        if prop_id not in MSG_PROPS:
            continue
        raw = cfb.stream(name)
        if not raw:
            continue
        if kind == "001F":
            value = raw.decode("utf-16-le", "replace")
        elif kind == "001E":
            value = _decode_8bit(raw)
        else:
            continue
        value = value.strip("\x00").strip()
        if value:
            found.setdefault(MSG_PROPS[prop_id], value)
    if not found:
        return MsgResult("", "письмо разобрано, но тема и тело пусты")
    parts = []
    for key in ("тема", "отправитель", "кому", "тело"):
        if key in found:
            parts.append(f"{key}: {found[key]}")
    return MsgResult(_clean_text("\n".join(parts)),
                     f"Outlook MSG: свойств найдено {len(found)}")
