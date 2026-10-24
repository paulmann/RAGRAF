"""Правка tools/check_admin.py: две ошибки в самой проверке.

1. Неверный токен содержит кириллицу; HTTP-заголовки требуют latin-1, и urllib
   падает до отправки запроса, возвращая код 0. Заменяется на ASCII-значение.
2. После сохранения настроек сервер отдаёт данные из кэша (5 секунд), поэтому
   проверка «значение действительно сохранено» читала прежнее значение.
   Перед повторным чтением кэш сбрасывается запросом /api/cache/clear.
"""

from __future__ import annotations

import pathlib

path = pathlib.Path(__file__).resolve().parents[1] / "tools" / "check_admin.py"
text = path.read_text(encoding="utf-8")

# 1. Неверный токен — только ASCII: заголовки HTTP не переносят кириллицу.
old1 = '{"values": {"reindex_after_changes": 7}}, token="неверный-токен")'
new1 = (
    '{"values": {"reindex_after_changes": 7}}, token="wrong-token-value")'
    # ragraf-release: placeholder-value — тот же неверный токен, перенесённый строкой патча
)
if old1 in text:
    text = text.replace(old1, new1)
    print("неверный токен заменён на ASCII")
else:
    print("строка с неверным токеном не найдена")

# 2. Сброс кэша перед проверкой сохранённого значения.
old2 = '''    code, sett2 = request("/api/settings")
    glob2 = ((sett2 or {}).get("settings") or {}).get("global_settings") or {}'''
new2 = '''    # Кэш интерфейса держит состояние 5 секунд: без сброса проверка прочитает
    # прежние значения и решит, что сохранение не сработало.
    request("/api/cache/clear", "POST", {"token": token}, token=token)
    code, sett2 = request("/api/settings")
    glob2 = ((sett2 or {}).get("settings") or {}).get("global_settings") or {}'''
if old2 in text:
    text = text.replace(old2, new2)
    print("добавлен сброс кэша перед проверкой")
else:
    print("блок чтения настроек не найден")

# 3. Сброс кэша перед проверкой восстановления исходного значения.
old3 = '''    request("/api/settings/global", "POST",
            {"values": {"reindex_after_changes": before.get("reindex_after_changes", 5)}},
            token=token)
    code, sett3 = request("/api/settings")'''
new3 = '''    request("/api/settings/global", "POST",
            {"values": {"reindex_after_changes": before.get("reindex_after_changes", 5)}},
            token=token)
    request("/api/cache/clear", "POST", {"token": token}, token=token)
    code, sett3 = request("/api/settings")'''
if old3 in text:
    text = text.replace(old3, new3)
    print("добавлен сброс кэша перед проверкой восстановления")
else:
    print("блок восстановления не найден")

# 4. Проверка 404: путь с кириллицей тоже нужно заменить на ASCII.
old4 = 'code, res = request("/api/нет-такой-точки")'
new4 = 'code, res = request("/api/no-such-endpoint")'
if old4 in text:
    text = text.replace(old4, new4)
    print("путь 404 заменён на ASCII")
else:
    print("строка 404 не найдена")

path.write_text(text, encoding="utf-8", newline="\n")
print("готово")
