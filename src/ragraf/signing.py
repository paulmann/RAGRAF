"""Подпись файлов поставки ключом Ed25519.

Зачем отдельный модуль. Подписывать нужно три разные вещи — экспорт аудита,
SBOM и манифест офлайн-набора, — и каждая должна проверяться ОДНИМ кодом. Иначе
проверка подписи в трёх местах разойдётся в трёх местах.

Ключ подписи ОТДЕЛЬНЫЙ от мастер-ключа хранилища секретов (`catalog/secrets.key`).
Это не удобство, а разделение последствий: компрометация ключа подписи позволяет
подделать утверждение «набор собран нами», но не открыть сохранённые пароли.

Где лежит ключ. `catalog/audit-signing.key` — каталог состояния, исключённый из
корпуса, поэтому ключ не попадает ни в индекс, ни в поисковую выдачу. Путь можно
задать переменной `RAGRAF_SIGNING_KEY` (тогда путь называет окружение).

Формат подписи — один JSON-файл рядом с подписанным:

    {"algorithm": "Ed25519", "file": "<имя>", "sha256": "<хэш содержимого>",
     "signature": "<base64>", "public_key": "<base64>", "signed_at": "<ISO>"}

Подпись считается по sha256 содержимого, а не по самому файлу: так проверка не
зависит от размера (SBOM крупного корпуса — мегабайты).

Без библиотеки PyNaCl подпись невозможна, и это честная причина (`SigningUnavailable`),
а не молчаливое «подписи нет, но выглядит как подписано». PyNaCl не входит в
обязательные зависимости пакета — это найдено при сборке офлайн-набора 24.10.2026.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import secrets as _secrets
import time

try:  # pragma: no cover - наличие библиотеки определяется в момент вызова
    from nacl import signing as _nacl_signing

    NACL_AVAILABLE = True
    NACL_REASON = ""
except Exception as exc:  # noqa: BLE001
    _nacl_signing = None  # type: ignore[assignment]
    NACL_AVAILABLE = False
    NACL_REASON = f"нет библиотеки PyNaCl: {type(exc).__name__}"

KEY_NAME = "audit-signing.key"
DEFAULT_KEY = pathlib.Path(__file__).resolve().parents[2] / "catalog" / KEY_NAME


class SigningUnavailable(RuntimeError):
    """Подписать нельзя, и причина названа. Подавлять нельзя: «подписи нет» ≠ «подписано»."""


class SignatureBroken(RuntimeError):
    """Подпись не сходится с содержимым или ключ не тот."""


def key_path(explicit: str | pathlib.Path | None = None) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit)
    from_env = str(os.environ.get("RAGRAF_SIGNING_KEY") or "").strip()
    if from_env:
        return pathlib.Path(from_env)
    return DEFAULT_KEY


def sha256_file(path: str | pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1_048_576), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_key(path: str | pathlib.Path | None = None) -> pathlib.Path:
    """Вернуть путь к ключу, создав его при первом обращении.

    Создание ключа — необратимое действие по последствиям (потеря ключа делает
    прежние подписи непроверяемыми), поэтому вызывающий обязан сказать о нём
    вслух. Здесь ключ только создаётся и НИКОГДА не перезаписывается.
    """
    if not NACL_AVAILABLE:
        raise SigningUnavailable(NACL_REASON)
    target = key_path(path)
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    private = _nacl_signing.SigningKey.generate()
    encoded = base64.b64encode(bytes(private)).decode("ascii")
    target.write_bytes((encoded + "\n").encode("ascii"))
    try:  # права сужаются там, где это возможно (Windows: через ACL не всегда)
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def _private(key_file: str | pathlib.Path | None) -> "_nacl_signing.SigningKey":
    if not NACL_AVAILABLE:
        raise SigningUnavailable(NACL_REASON)
    path = key_path(key_file)
    if not path.exists():
        raise SigningUnavailable(f"нет ключа подписи: {path}")
    raw = base64.b64decode(path.read_text(encoding="ascii").strip())
    if len(raw) != 32:
        raise SigningUnavailable(
            f"ключ подписи повреждён: ожидалось 32 байта, получено {len(raw)}")
    return _nacl_signing.SigningKey(raw)


def public_key_b64(key_file: str | pathlib.Path | None = None) -> str:
    return base64.b64encode(bytes(_private(key_file).verify_key)).decode("ascii")


def _sign_digest(digest: str, key_file: str | pathlib.Path | None,
                 name: str) -> dict:
    """Подписать хэш содержимого. Одна точка подписи на все виды наборов.

    Разделение «считать хэш» и «подписать хэш» появилось вместе с выгрузкой в
    SIEM (ПРОМТ-И7): набор уходит по сети, и файла у него нет. Раньше подписать
    можно было только файл, поэтому второй вид подписи пришлось бы писать
    заново — и две подписи разошлись бы форматом. Теперь формат один.
    """
    key = _private(key_file)
    signature = key.sign(digest.encode("ascii"))
    return {
        "algorithm": "Ed25519",
        "file": name,
        "sha256": digest,
        "signature": base64.b64encode(signature.signature).decode("ascii"),
        "public_key": base64.b64encode(bytes(key.verify_key)).decode("ascii"),
        "signed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "nonce": _secrets.token_hex(8),
    }


def sign_digest_bytes(data: bytes, key_file: str | pathlib.Path | None = None,
                      *, name: str = "payload.bin") -> dict:
    """Подписать ПРОИЗВОЛЬНЫЕ байты: sha256 считается по ним.

    Нужно там, где подписывается не файл, а набор, уходящий по сети (выгрузка в
    SIEM). Формат подписи тот же, что у `sign_file`: принимающая сторона
    проверяет оба вида одним кодом.
    """
    if not NACL_AVAILABLE:
        raise SigningUnavailable(NACL_REASON)
    return _sign_digest(hashlib.sha256(data).hexdigest(), key_file, name)


def verify_digest_bytes(data: bytes, payload: dict) -> dict:
    """Проверить подпись набора: хэш содержимого плюс подпись по нему."""
    if not NACL_AVAILABLE:
        raise SigningUnavailable(NACL_REASON)
    digest = hashlib.sha256(data).hexdigest()
    if str(payload.get("sha256") or "") != digest:
        raise SignatureBroken(
            f"содержимое набора изменилось после подписи: в подписи "
            f"{payload.get('sha256')}, сейчас {digest}")
    public = base64.b64decode(str(payload.get("public_key") or ""))
    signature = base64.b64decode(str(payload.get("signature") or ""))
    verifier = _nacl_signing.VerifyKey(public)
    try:
        verifier.verify(digest.encode("ascii"), signature)
    except Exception as exc:  # noqa: BLE001
        raise SignatureBroken(f"подпись набора не сходится с ключом: {type(exc).__name__}") from exc
    return {"ok": True, "sha256": digest, "algorithm": payload.get("algorithm"),
            "signed_at": payload.get("signed_at")}


def sign_file(path: str | pathlib.Path, key_file: str | pathlib.Path | None = None,
              signature_path: str | pathlib.Path | None = None) -> dict:
    """Подписать файл. Возвращает метаданные подписи (они же пишутся в `.sig`)."""
    target = pathlib.Path(path)
    if not target.exists():
        raise SigningUnavailable(f"подписывать нечего: нет файла {target}")
    payload = _sign_digest(sha256_file(target), key_file, target.name)
    out = pathlib.Path(signature_path) if signature_path else target.with_name(
        target.name + ".sig")
    out.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return payload


def verify_file(path: str | pathlib.Path,
                signature_path: str | pathlib.Path | None = None) -> dict:
    """Проверить подпись. Возвращает отчёт; при расхождении поднимает SignatureBroken."""
    if not NACL_AVAILABLE:
        raise SigningUnavailable(NACL_REASON)
    target = pathlib.Path(path)
    sig_path = pathlib.Path(signature_path) if signature_path else target.with_name(
        target.name + ".sig")
    if not target.exists():
        raise SignatureBroken(f"нет подписанного файла: {target}")
    if not sig_path.exists():
        raise SignatureBroken(f"нет файла подписи: {sig_path}")
    payload = json.loads(sig_path.read_text(encoding="utf-8"))
    digest = sha256_file(target)
    if payload.get("sha256") != digest:
        raise SignatureBroken(
            f"содержимое изменилось после подписи: в подписи {payload.get('sha256')}, "
            f"сейчас {digest}")
    public = base64.b64decode(str(payload.get("public_key") or ""))
    signature = base64.b64decode(str(payload.get("signature") or ""))
    verifier = _nacl_signing.VerifyKey(public)
    try:
        verifier.verify(digest.encode("ascii"), signature)
    except Exception as exc:  # noqa: BLE001
        raise SignatureBroken(f"подпись не сходится с ключом: {type(exc).__name__}") from exc
    return {"ok": True, "file": target.name, "sha256": digest,
            "algorithm": payload.get("algorithm"), "signed_at": payload.get("signed_at"),
            "public_key": str(payload.get("public_key"))[:16] + "…"}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Подпись и проверка файлов Ed25519")
    parser.add_argument("--sign", action="append", default=[], metavar="ФАЙЛ")
    parser.add_argument("--verify", action="append", default=[], metavar="ФАЙЛ")
    parser.add_argument("--key", default="")
    parser.add_argument("--init", action="store_true", help="создать ключ, если его нет")
    args = parser.parse_args(argv)

    if args.init:
        path = ensure_key(args.key or None)
        print(json.dumps({"key": str(path), "public_key": public_key_b64(args.key or None)},
                         ensure_ascii=False, indent=2))
        return 0
    if not args.sign and not args.verify:
        parser.print_help()
        return 2
    try:
        for item in args.sign:
            payload = sign_file(item, args.key or None)
            print(json.dumps({"signed": item, "sha256": payload["sha256"]},
                             ensure_ascii=False))
        for item in args.verify:
            print(json.dumps(verify_file(item), ensure_ascii=False))
    except (SigningUnavailable, SignatureBroken) as exc:
        print(f"отказ: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
