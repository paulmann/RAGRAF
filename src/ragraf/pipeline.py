"""Стадии индексации (архитектура §9).

Релиз 1, M1: работают стадии scan, ast, load, seal. CPG (Joern), символы (SCIP),
схема БД и маршруты добавляются на M2/M3 — интерфейс stage-функций уже готов.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import util
from .config import ProjectConfig
from .corpus import CorpusScan


@dataclass
class StageResult:
    name: str
    seconds: float
    items: int
    note: str | None = None
    skipped: bool = False


@dataclass
class StageContext:
    """Состояние, передаваемое между стадиями одного прогона."""

    cfg: ProjectConfig
    rev_id: str
    parent_rev: str | None
    scan: CorpusScan | None = None
    capability: str = "degraded"
    joern_used: bool = False
    extractor_versions: dict[str, str] = field(default_factory=dict)
    results: list[StageResult] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)

    def note_versions(self) -> None:
        """Версии всех экстракторов — обязательная часть ревизии (ФТ-10)."""
        self.extractor_versions = collect_extractor_versions(self.cfg)


class Timer:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    def stop(self) -> float:
        return round(time.perf_counter() - self.t0, 3)


def run_stage(ctx: StageContext, name: str, fn: Callable[[], tuple[int, str | None]]) -> StageResult:
    """Обёртка стадии: время, счётчик, обработка исключений с сохранением контекста."""
    timer = Timer()
    try:
        items, note = fn()
        res = StageResult(name=name, seconds=timer.stop(), items=items, note=note)
    except Exception as exc:  # стадия не должна ронять всю сборку молча
        res = StageResult(
            name=name,
            seconds=timer.stop(),
            items=0,
            note=f"ОШИБКА: {type(exc).__name__}: {exc}",
        )
    ctx.results.append(res)
    return res


# --------------------------------------------------------------------------
# Версии экстракторов
# --------------------------------------------------------------------------


def collect_extractor_versions(cfg: ProjectConfig) -> dict[str, str]:
    """Версии инструментов. Отсутствующий инструмент помечается явно."""
    versions: dict[str, str] = {"ragraf": _pkg_version("ragraf")}

    versions["python"] = _python_version()
    versions["tree_sitter"] = _pkg_version("tree_sitter")
    versions["tree_sitter_language_pack"] = _pkg_version("tree_sitter_language_pack")
    versions["sqlite"] = _sqlite_version()
    versions["sqlite_vec"] = _pkg_version("sqlite_vec")
    versions["fastembed"] = _pkg_version("fastembed")
    versions["tiktoken"] = _pkg_version("tiktoken")

    vocab = _tree_sitter_php_available()
    versions["tree_sitter_php_grammar"] = "available" if vocab else "MISSING"

    joern = _joern_path(cfg)
    versions["joern"] = str(joern) if joern else "MISSING"
    versions["java"] = _java_version(cfg)
    return versions


def _pkg_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return "MISSING"


def _python_version() -> str:
    import sys

    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _sqlite_version() -> str:
    import sqlite3

    return sqlite3.sqlite_version


def _tree_sitter_php_available() -> bool:
    try:
        from tree_sitter_language_pack import get_parser  # type: ignore

        get_parser("php")
        return True
    except Exception:
        return False


def _joern_path(cfg: ProjectConfig) -> Path | None:
    """Joern: сначала .tools корпуса RAGRAF, затем переменная окружения."""
    import os

    candidates: list[Path] = []
    env = os.environ.get("RAGRAF_JOERN_HOME")
    if env:
        candidates.append(Path(env))
    candidates.append(Path(__file__).resolve().parents[2] / ".tools" / "joern")
    for base in candidates:
        for name in ("joern-cli/joern.bat", "joern.bat", "joern-cli/joern", "joern"):
            p = base / name
            if p.exists():
                return p
    return None


def _java_version(cfg: ProjectConfig) -> str:
    import os
    import subprocess

    java_home = os.environ.get("RAGRAF_JAVA_HOME")
    java = Path(java_home) / "bin" / "java.exe" if java_home else None
    if java is None or not java.exists():
        java = Path(__file__).resolve().parents[2] / ".tools" / "jdk" / "bin" / "java.exe"
    if not java.exists():
        return "MISSING"
    try:
        out = subprocess.run(
            [str(java), "-version"], capture_output=True, text=True, timeout=30
        )
        text = (out.stderr or out.stdout or "").strip().splitlines()
        return text[0] if text else "unknown"
    except Exception:
        return "unknown"
