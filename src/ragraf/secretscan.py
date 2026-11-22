"""Сверка текста со значениями хранилища секретов: одна реализация на два места.

Зачем модуль, а не две копии. Значения секретов нужны двум потребителям:
проверке утечек (`tools/check_secret_leaks.py`) и памяти фактов
(`facts.py`), которая обязана отказать записи, если в тексте лежит значение
секрета. Вторая копия расшифровки означала бы второй путь к значению, а такого
пути в проекте быть не должно: `tools/check_secret_leaks.py` прямо пишет, что
берёт ключ и расшифровку у самого хранилища.

Почему модуль в `src/`, а не в `tools/`. Проверка утечек — инструмент, а память
фактов — код продукта; продукт не может импортировать из `tools/` (это не пакет
и в поставку входит иначе). Сверка секретов — базовая безопасность, поэтому она
живёт в Community рядом с `vault.py`.

Границы доверия:
  * ищутся ТОЛЬКО значения, которые есть в хранилище; секрет, которого там нет,
    не будет найден — это названное ограничение, а не обещание;
  * значения короче порога не ищутся: «yes», «1234», «md» встречаются в любом
    тексте, и совпадение по ним было бы ложной находкой;
  * значения не печатаются и не логируются: наружу уходит имя записи и место.
"""

from __future__ import annotations

import re

#: Минимальная длина значения, при которой поиск осмыслен. Короче — ложные
#: находки на обычных словах.
DEFAULT_MIN_LENGTH = 8


def collect_values(vault, min_length: int = DEFAULT_MIN_LENGTH
                   ) -> tuple[dict[str, str], list[str], dict]:
    """Расшифровать значения всех секретов (без записи в аудит) — для перебора.

    Ключ и расшифровка берутся у самого хранилища (`vault.master` и
    `vault._decrypt` — те же функции, которыми пользуется выдача): второго пути к
    значению в проекте нет и заводить его нельзя. Аудит здесь не пишется
    осознанно: журнал выдачи существует для того, чтобы владелец видел РЕАЛЬНЫЕ
    выдачи агентов, и замер, засоривший его, мешал бы читать его по назначению.

    Счётчик записей и строк аудита берётся из `Vault.check()`: он читает всё
    хранилище и ничего не пишет — то есть безопасен для замера.
    """
    from . import vault as vault_mod

    health = vault.check()
    master = vault.master()
    conn = vault._connect()  # noqa: SLF001
    try:
        rows = conn.execute(
            "SELECT id, name, ver, nonce, ciphertext FROM secret"
        ).fetchall()
    finally:
        conn.close()
    values: dict[str, str] = {}
    skipped: list[str] = []
    for row in rows:
        name = str(row["name"])
        try:
            value = vault_mod._decrypt(master, str(row["id"]), name,  # noqa: SLF001
                                       int(row["ver"]), str(row["nonce"]),
                                       str(row["ciphertext"]))
        except Exception:  # noqa: BLE001 — нечитаемая запись не отменяет проверку
            skipped.append(name)
            continue
        if value and len(value) >= min_length:
            values[name] = value
    return values, skipped, health


def leak_pattern(values: dict[str, str]) -> re.Pattern[str]:
    """Одно регулярное выражение из всех значений: один проход по тексту."""
    parts = sorted(values.values(), key=len, reverse=True)
    return (re.compile("|".join(re.escape(p) for p in parts)) if parts
            else re.compile(r"(?!x)x"))


def scan_text(pattern: re.Pattern[str], text: str) -> bool:
    """Есть ли в тексте хоть одно значение из выражения."""
    return bool(text) and pattern.search(text) is not None


def find_in_text(vault, text: str, min_length: int = DEFAULT_MIN_LENGTH
                 ) -> tuple[str, str]:
    """Имя секрета, значение которого найдено в тексте, иначе ("", "").

    Возвращает ИМЯ и причину, а не значение: вызывающий не должен иметь
    возможности напечатать секрет даже случайно. Хранилище недоступно — пустая
    пара и причина `unavailable`, чтобы вызывающий сам решил, блокировать ли
    запись.
    """
    if not text:
        return "", "empty"
    try:
        values, _skipped, _health = collect_values(vault, min_length)
    except Exception as exc:  # noqa: BLE001 — недоступное хранилище не должно ронять вызов
        return "", f"unavailable: {type(exc).__name__}"
    if not values:
        return "", "no_values"
    name = next((n for n, v in values.items() if v and v in text), "")
    return name, ("found" if name else "clean")
