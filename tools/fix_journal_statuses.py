"""Правка генератора конфигураций: привести статусы в соответствие белому списку.

Проверка связности (probe_journals.php) нашла расхождения:
  * в общих подписях был статус postponed — он есть только у журнала PL,
    где добавляется отдельно; в белые списки DG/SH/OT/TD он не входит;
  * у SH в белом списке есть статус assigned, для которого не было подписи,
    и была подпись refused, которого в белом списке нет.
"""

from __future__ import annotations

import pathlib

p = pathlib.Path("tools/gen_journal_configs.py")
t = p.read_text(encoding="utf-8")

# 1. Убрать postponed из общих подписей (у PL он добавляется отдельно).
old1 = '''    "postponed": "Отложен",
    "rejected": "Отклонён",
    "cancelled"'''
new1 = '''    "cancelled"'''
if old1 in t:
    t = t.replace(old1, new1)
    print("postponed убран из общих подписей")
else:
    print("фрагмент с postponed не найден")

# 2. Подписи журнала SH: добавить assigned и objection, убрать refused.
old2 = '''        status_titles=dict(STATUS_TITLES, acknowledged="Ознакомлен",
                           refused="Отказ от ознакомления"),'''
new2 = '''        status_titles=dict(STATUS_TITLES, assigned="Назначено",
                           acknowledged="Ознакомлен",
                           objection="Возражение"),'''
if old2 in t:
    t = t.replace(old2, new2)
    print("подписи SH приведены к белому списку")
else:
    print("фрагмент подписей SH не найден")

# 3. Журналу PL нужен postponed (действие postpone есть в матрице прав).
old3 = '''        status_titles=dict(STATUS_TITLES, signed="Подписан"),'''
new3 = '''        status_titles=dict(STATUS_TITLES, signed="Подписан"),'''
# PL описан в отдельном файле pl.php — он не генерируется, проверяем его наличие.
pl = pathlib.Path("C:/AutoClaw/RAGRAF/.staging/agreements/journals/pl.php")
if pl.exists():
    pt = pl.read_text(encoding="utf-8")
    print("pl.php содержит postponed:", "postponed" in pt)

p.write_text(t, encoding="utf-8", newline="\n")
print("готово")
