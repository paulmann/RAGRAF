"""Зафиксировать в реестре фактические ревизии и суммы зарегистрированных разработок.

Зачем: команда `register` заводит запись, но не индексирует, поэтому поле
ревизии оставалось пустым. Здесь в запись подставляются фактические значения
из индекса каждой разработки — иначе реестр показывал бы «не индексировалась»
для уже проиндексированных корпусов.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ragraf import catalog as cm  # noqa: E402
from ragraf import watch  # noqa: E402
from ragraf.config import load  # noqa: E402

for cfg_name in ("z-batteryservice", "revoap"):
    cfg_path = REPO / "config" / f"{cfg_name}.yaml"
    cfg = load(cfg_path)
    cat = cm.Catalog()
    name = cat.category_for_root(cfg.root) or cfg.project
    entry = cat.get(name)
    if entry is None:
        print(f"{name}: не зарегистрирована")
        continue

    # Значения берутся из индекса разработки: у каждой свой каталог .ragraf,
    # поэтому смешения данных между разработками не происходит.
    ch, cfgh, rev = watch.indexed_hashes(name, cfg)
    if rev:
        entry.rev_id = rev
    if ch:
        entry.corpus_hash = ch
    if cfgh:
        entry.config_hash = cfgh

    now_hash, now_config, files_now, _ = watch.current_hashes(cfg)
    entry.file_count = entry.file_count or files_now
    cat.put(entry)

    state = "актуален" if entry.corpus_hash == now_hash else "ТРЕБУЕТСЯ РЕИНДЕКСАЦИЯ"
    print(f"{name}")
    print(f"  ревизия:  {entry.rev_id or '(нет)'}")
    print(f"  сумма:    {entry.corpus_hash[:24]}")
    print(f"  файлов:   {entry.file_count} (сейчас {files_now})")
    print(f"  состояние: {state}")
