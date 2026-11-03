"""Читатель почты: `.eml` и `.msg`.

Почему письмо — не «текст с заголовками».

У письма есть части, и они разной природы: тема и адреса (метаданные), тело
(содержимое), цитата предыдущего письма (чужой текст) и вложения (другие
документы). Если всё это свалить в один чанк, поиск будет находить письмо по
цитате из чужого письма и выдавать её за содержание — самый дорогой вид ложного
факта в переписке.

Поэтому:
  * тема и адреса — узел `path#message`;
  * тело — узел `path#part=N`;
  * цитата — отдельный узел `path#part=N#quote=M` (текст чужой, и это видно);
  * вложение — узел `path#part=N` со структурой `attachment`, `content_type`,
    `size`, `parent_document`.

Про вложение честно: файл вложения НЕ пишется на диск, поэтому отдельным
документом с собственным адресом он стать не может. Адрес вложения — это часть
письма плюс имя файла; так «где лежит» отвечается без выдуманного пути.

Про `.msg`: пакета `extract_msg` в окружении нет (замер 29.10.2026), работает
собственный разбор OLE (`officebinary.msg_text`), который достаёт тему,
отправителя, получателя и тело. Частей и вложений он не даёт — значит статус
`degraded` с причиной, а не «ok» с видом полного разбора.
"""

from __future__ import annotations

import email
import email.policy
from pathlib import Path

from .. import officebinary
from ..docnode import DocumentNode, ReadResult

#: Сколько символов текста вложения показывать в узле. Вложение — не текст письма:
#: его содержимое читается отдельным путём, а здесь нужен адрес и опознание.
MAX_QUOTE_LINES = 400


def _clean(value: str) -> str:
    return str(value or "").replace("\r\n", "\n").strip()


def _html_to_text(raw: str) -> str:
    try:
        from lxml import html as lhtml
    except Exception:  # noqa: BLE001
        return _clean(raw)
    try:
        tree = lhtml.fromstring(raw.encode("utf-8", "replace"))
        for tag in ("script", "style", "head"):
            for element in list(tree.iter(tag)):
                parent = element.getparent()
                if parent is not None:
                    parent.remove(element)
        return _clean(" ".join(tree.itertext()))
    except Exception:  # noqa: BLE001
        return _clean(raw)


def _split_quotes(text: str) -> tuple[str, str]:
    """Разделить тело на «свой текст» и «цитату».

    Признак цитаты — строка, начинающаяся с `>` (обычная практика ответа) или
    блок после «-----Original Message-----» / «Пишет …:». Эвристика названа здесь,
    а не спрятана: если письмо оформлено иначе, цитата останется в теле, и это
    лучше, чем выбросить текст.
    """
    body: list[str] = []
    quote: list[str] = []
    in_quote = False
    for line in text.splitlines():
        stripped = line.strip()
        if not in_quote and (stripped.startswith("-----Original Message")
                             or stripped.startswith("От:")
                             and body and body[-1].strip() == ""):
            in_quote = True
        if stripped.startswith(">"):
            in_quote = True
        if in_quote:
            quote.append(line)
        else:
            body.append(line)
    return ("\n".join(body).strip(), "\n".join(quote[:MAX_QUOTE_LINES]).strip())


def read_eml(rel: str, raw: bytes) -> ReadResult:
    try:
        message = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception as exc:  # noqa: BLE001
        return ReadResult(status="failed",
                          reason=f"разбор EML: {type(exc).__name__}: {exc}",
                          reader="eml")
    nodes: list[DocumentNode] = []
    subject = _clean(str(message.get("Subject") or ""))
    sender = _clean(str(message.get("From") or ""))
    recipient = _clean(str(message.get("To") or ""))
    date = _clean(str(message.get("Date") or ""))
    header_lines = [f"Тема: {subject}"] if subject else []
    if sender:
        header_lines.append(f"От: {sender}")
    if recipient:
        header_lines.append(f"Кому: {recipient}")
    if date:
        header_lines.append(f"Дата: {date}")
    if header_lines:
        nodes.append(DocumentNode(
            kind="message",
            locator=f"{rel}#message",
            text="\n".join(header_lines),
            structure={"subject": subject, "from": sender, "to": recipient,
                       "date": date, "role": "headers"},
        ))

    attachments = 0
    part_number = 0
    for part in message.walk():
        if part.is_multipart():
            continue
        part_number += 1
        content_type = str(part.get_content_type() or "")
        filename = part.get_filename()
        if filename:
            attachments += 1
            payload = part.get_payload(decode=True) or b""
            nodes.append(DocumentNode(
                kind="record",
                locator=f"{rel}#part={part_number}",
                text=(f"вложение: {filename} ({content_type}, {len(payload)} Б); "
                      f"содержимое читается отдельно, в письме остаётся адрес"),
                structure={"attachment": str(filename),
                           "content_type": content_type,
                           "size": len(payload),
                           "parent_document": rel,
                           "role": "attachment"},
                metadata={"encoding": ""},
            ))
            continue
        try:
            payload_text = part.get_content()
        except Exception:  # noqa: BLE001 — часть может быть в неизвестной кодировке
            payload = part.get_payload(decode=True) or b""
            payload_text = payload.decode("utf-8", "replace")
        if not isinstance(payload_text, str):
            continue
        if content_type == "text/html":
            payload_text = _html_to_text(payload_text)
        text = _clean(payload_text)
        if not text:
            continue
        body, quote = _split_quotes(text)
        if body:
            nodes.append(DocumentNode(
                kind="paragraph",
                locator=f"{rel}#part={part_number}",
                text=body,
                structure={"parent_path": f"{rel}#message",
                           "content_type": content_type,
                           "no_heading_reason": "письмо: разделов нет"},
            ))
        if quote:
            nodes.append(DocumentNode(
                kind="paragraph",
                locator=f"{rel}#part={part_number}#quote=1",
                text=quote,
                structure={"parent_path": f"{rel}#part={part_number}",
                           "quote": True,
                           "no_heading_reason": "цитата письма: разделов нет"},
            ))
    if not nodes:
        return ReadResult(status="degraded",
                          reason="в письме нет текста и вложений",
                          reader="eml")
    return ReadResult(nodes=nodes, status="ok", reader="eml",
                      metadata={"subject": subject, "parts": part_number,
                                "attachments": attachments})


def read_msg(rel: str, raw: bytes) -> ReadResult:
    try:
        result = officebinary.msg_text(raw)
    except Exception as exc:  # noqa: BLE001
        return ReadResult(status="failed",
                          reason=f"разбор MSG: {type(exc).__name__}: {exc}",
                          reader="msg")
    text = _clean(result.text)
    if not text:
        return ReadResult(status="degraded",
                          reason=f"текста не извлечено: {result.note or 'пусто'}",
                          reader="msg")
    reason = ("собственный разбор OLE: тема, отправитель, получатель и тело; "
              "части и вложения не извлекаются")
    if result.note:
        reason = f"{reason}; {result.note}"
    return ReadResult(
        nodes=[DocumentNode(
            kind="message",
            locator=f"{rel}#message",
            text=text,
            structure={"role": "message", "no_heading_reason": "письмо MSG: без частей"},
            provenance={"reader": "msg", "converter": "builtin:officebinary:msg"},
        )],
        status="degraded", reason=reason, reader="msg",
        converter="builtin:officebinary:msg", warnings=[reason],
    )


def read(rel: str, raw: bytes) -> ReadResult:
    suffix = Path(rel).suffix.lower()
    if suffix == ".eml":
        return read_eml(rel, raw)
    if suffix == ".msg":
        return read_msg(rel, raw)
    return ReadResult(status="failed",
                      reason=f"читатель почты не знает формат: {rel}", reader="mail")
