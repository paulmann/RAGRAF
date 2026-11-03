"""OCR: вызов распознавания реализован, движок ищется и вне PATH.

Замер 29.10.2026 (эта сессия): `tesseract` установлен через winget и лежит в
`C:\\Program Files\\Tesseract-OCR\\tesseract.exe`, но в PATH его нет — `shutil.which`
возвращает None. Прежняя проверка «движка нет» была формально верной и практически
бесполезной: движок есть, искать надо было в известных каталогах установки.

Что сделано:
  * поиск движка: PATH, затем типовые каталоги установки (Program Files,
    LocalAppData, scoop), затем переменная `RAGRAF_TESSERACT`;
  * языки: спрашиваются у движка (`--list-langs`), а не предполагаются. Русского
    языкового пакета в поставке winget НЕТ (проверено: eng, osd), поэтому для
    русских сканов честный ответ — «язык rus не установлен», а не мусорный текст;
  * изображения читаются напрямую, страницы PDF — через растеризацию PyMuPDF;
  * отсутствие растеризации или языкового пакета — деградация с причиной.

Облачных вызовов нет: только локальный процесс.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile

from ..docnode import DocumentNode, ReadResult

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp", ".gif")

#: Типовые каталоги установки: PATH в этой среде урезан, и движок в него не попадает.
KNOWN_DIRS = (
    r"C:\Program Files\Tesseract-OCR",
    r"C:\Program Files (x86)\Tesseract-OCR",
)

#: Язык по умолчанию: сначала русский с английским, если пакет есть.
PREFERRED_LANGS = ("rus", "eng")


def _settings() -> dict:
    try:
        from ..settings import SettingsStore

        data = SettingsStore().load()
    except Exception:  # noqa: BLE001
        data = {}
    return {"enabled": bool(data.get("documents_ocr_enabled", False)),
            "min_chars": int(data.get("documents_ocr_min_chars", 16) or 16),
            "languages": str(data.get("documents_ocr_languages", "rus+eng")
                            or "rus+eng").strip(),
            "dpi": int(data.get("documents_ocr_dpi", 200) or 200),
            "tessdata": str(data.get("documents_ocr_tessdata", "") or "").strip()}


def engine() -> str:
    """Путь к локальному движку OCR или пустая строка. Проверка вызовом."""
    override = os.environ.get("RAGRAF_TESSERACT", "").strip()
    if override and pathlib.Path(override).exists():
        return override
    found = shutil.which("tesseract")
    if found:
        return found
    candidates = list(KNOWN_DIRS)
    local = os.environ.get("LOCALAPPDATA") or str(
        pathlib.Path.home() / "AppData" / "Local")
    candidates.append(str(pathlib.Path(local) / "Programs" / "Tesseract-OCR"))
    candidates.append(str(pathlib.Path.home() / "scoop" / "shims"))
    for directory in candidates:
        path = pathlib.Path(directory) / "tesseract.exe"
        if path.exists():
            return str(path)
    return ""


def languages(binary: str, tessdata: str = "") -> tuple[str, ...]:
    """Языковые пакеты, которые движок действительно видит.

    Каталог передаётся тот же, что и при распознавании: иначе проверка «язык
    есть» смотрела бы в одно место, а вызов — в другое, и `ready` оказывался
    ложным.
    """
    command = [binary, "--list-langs"]
    if tessdata:
        command += ["--tessdata-dir", tessdata]
    try:
        done = subprocess.run(command, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ()
    lines = [line.strip() for line in (done.stdout or "").splitlines()]
    return tuple(line for line in lines if line and " " not in line and ":" not in line)


def chosen_language(binary: str, requested: str = "",
                    tessdata: str = "") -> tuple[str, str]:
    """Язык распознавания и причина выбора.

    Запрошенные языки берутся из настройки. Если хотя бы одного из них у движка
    нет — отказ с причиной: распознавание «чем есть» на русском скане даёт
    латинскую абракадабру, а такой текст в индексе хуже отсутствия (замер).
    """
    available = languages(binary, tessdata)
    wanted = [part for part in str(requested or "").replace(",", "+").split("+")
              if part.strip()]
    if not wanted:
        wanted = list(PREFERRED_LANGS)
    missing = [lang for lang in wanted if lang not in available]
    if missing:
        return ("", f"языкового пакета {', '.join(missing)} нет у движка; "
                    f"доступны: {', '.join(available) or '—'}. Установите "
                    f"tessdata ({missing[0]}.traineddata) и укажите каталог в "
                    f"TESSDATA_PREFIX")
    return ("+".join(wanted), f"языки распознавания: {'+'.join(wanted)}"
                              f" (доступны: {', '.join(available)})")


def state() -> dict:
    """Состояние возможности: включена ли, есть ли движок, какие языки."""
    settings = _settings()
    binary = engine()
    language, language_note = (chosen_language(binary, settings["languages"],
                                              settings["tessdata"])
                               if binary else ("", "движка нет"))
    if not settings["enabled"]:
        status = "disabled"
        reason = "OCR выключен настройкой `documents_ocr_enabled`"
    elif not binary:
        status = "missing_engine"
        reason = ("OCR включён, но движка нет: tesseract не найден ни в PATH, ни в "
                  "типовых каталогах установки; переменная RAGRAF_TESSERACT не задана")
    elif not language:
        status = "missing_language"
        reason = f"движок найден ({binary}), но {language_note}"
    else:
        status = "ready"
        reason = f"движок: {binary}; {language_note}"
    return {"status": status, "reason": reason, "engine": binary,
            "language": language, "enabled": settings["enabled"]}


def _run_tesseract(binary: str, image_path: pathlib.Path, language: str) -> tuple[str, str]:
    """Распознать файл изображения. Возвращает (текст, ошибка)."""
    command = [binary, str(image_path), "stdout", "-l", language, "--psm", "3"]
    tessdata = _settings()["tessdata"]
    if tessdata:
        command += ["--tessdata-dir", tessdata]
    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return ("", f"{type(exc).__name__}: {exc}")
    if done.returncode != 0:
        return ("", (done.stderr or "ненулевой код возврата").strip()[:200])
    return (done.stdout or "", "")


def _clean(text: str) -> str:
    lines = [line.strip() for line in str(text or "").replace("\x0c", "").splitlines()]
    return "\n".join(line for line in lines if line)


def read_image(rel: str, raw: bytes) -> ReadResult:
    """Изображение → текст. Без движка текста нет, и это названо."""
    info = state()
    if info["status"] == "disabled":
        return ReadResult(status="not_indexed_by_edition", reason=info["reason"],
                          reader="ocr")
    if info["status"] != "ready":
        return ReadResult(status="degraded", reason=info["reason"], reader="ocr")

    suffix = pathlib.Path(rel).suffix.lower() or ".png"
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        handle.write(raw)
        handle.close()
        text, error = _run_tesseract(info["engine"], pathlib.Path(handle.name),
                                     info["language"])
    finally:
        try:
            pathlib.Path(handle.name).unlink()
        except OSError:
            pass
    if error:
        return ReadResult(status="degraded",
                          reason=f"распознавание не удалось: {error}", reader="ocr")
    text = _clean(text)
    if len(text) < _settings()["min_chars"]:
        return ReadResult(status="degraded",
                          reason=(f"распознано {len(text)} символов — меньше порога "
                                  f"{_settings()['min_chars']}; вероятно, текста нет"),
                          reader="ocr")
    node = DocumentNode(kind="image", locator=f"{rel}:image", text=text,
                        structure={"no_heading_reason": "изображение без структуры"},
                        provenance={"ocr_engine": pathlib.Path(info["engine"]).name,
                                    "ocr_language": info["language"]})
    return ReadResult(nodes=[node], status="degraded", reader="ocr",
                      reason=(f"текст получен распознаванием ({info['language']}); "
                              f"структура изображения не восстановлена"),
                      metadata={"ocr_language": info["language"],
                                "chars": len(text)})


def read_pdf_pages(rel: str, raw: bytes, page_numbers: list[int]) -> ReadResult:
    """Распознать указанные страницы PDF (нужен PyMuPDF для растеризации)."""
    info = state()
    if info["status"] != "ready":
        return ReadResult(status="degraded", reason=info["reason"], reader="ocr")
    try:
        import pymupdf
    except Exception:
        try:
            import fitz as pymupdf  # type: ignore
        except Exception as exc:  # noqa: BLE001
            return ReadResult(
                status="degraded",
                reason=("растеризация страниц недоступна: нет PyMuPDF "
                        f"({type(exc).__name__}); сканы без OCR не читаются"),
                reader="ocr",
            )
    if not page_numbers:
        return ReadResult(status="degraded", reason="нет страниц для распознавания",
                          reader="ocr")
    nodes: list[DocumentNode] = []
    warnings: list[str] = []
    try:
        document = pymupdf.open(stream=raw, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        return ReadResult(status="degraded",
                          reason=f"PDF не открылся для растеризации: {type(exc).__name__}",
                          reader="ocr")
    try:
        for number in page_numbers:
            if number < 1 or number > document.page_count:
                continue
            page = document.load_page(number - 1)
            pixmap = page.get_pixmap(dpi=_settings()["dpi"])
            handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            try:
                handle.write(pixmap.tobytes("png"))
                handle.close()
                text, error = _run_tesseract(info["engine"],
                                             pathlib.Path(handle.name), info["language"])
            finally:
                try:
                    pathlib.Path(handle.name).unlink()
                except OSError:
                    pass
            if error:
                warnings.append(f"страница {number}: {error[:80]}")
                continue
            text = _clean(text)
            if len(text) < _settings()["min_chars"]:
                warnings.append(f"страница {number}: распознано {len(text)} символов")
                continue
            nodes.append(DocumentNode(
                kind="section", locator=f"{rel}:page={number}", text=text,
                structure={"no_heading_reason": "страница распознана, разделы не размечены",
                           "page": number},
                provenance={"ocr_engine": pathlib.Path(info["engine"]).name,
                            "ocr_language": info["language"]},
            ))
    finally:
        document.close()
    if not nodes:
        return ReadResult(status="degraded",
                          reason=("распознавание не дало текста: "
                                  + "; ".join(warnings[:3])),
                          reader="ocr", warnings=warnings)
    return ReadResult(nodes=nodes, status="degraded", reader="ocr",
                      reason=(f"страницы распознаны локальным движком ({info['language']}): "
                              f"{len(nodes)} из {len(page_numbers)}"),
                      warnings=warnings,
                      metadata={"ocr_language": info["language"],
                                "pages": len(nodes)})


def supported(rel: str) -> bool:
    return pathlib.Path(rel).suffix.lower() in IMAGE_SUFFIXES


def placeholder_node(rel: str, reason: str) -> DocumentNode:
    """Узел-паспорт изображения: адрес есть, текста нет — и причина рядом."""
    return DocumentNode(kind="image", locator=f"{rel}:image", text="",
                        structure={"no_heading_reason": reason, "role": "image"},
                        extraction_status="degraded")
