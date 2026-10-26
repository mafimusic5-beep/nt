"""Cloud Telegram Bot API-compatible transfer of large EXE via 18 MiB documents.

Telegram's standard getFile endpoint cannot download a whole ~105 MB
installer. Split into .skpart documents and reconstruct it in the bot's
private filesystem, with whole-file SHA256 validation before PE inspection.
The file is never executed. Part names are a strict wire format, not paths.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import NamedTuple

from . import windows_release_store as store

PART_BYTES = 18 * 1024 * 1024
MAX_PARTS = 110
PART_RE = re.compile(
    r"^SkryonSetup-(?P<version>1\.0\.[0-9]{1,4})\.exe\."
    r"(?P<digest>[0-9a-f]{64})\.part(?P<index>[0-9]{3})of"
    r"(?P<count>[0-9]{3})\.skpart$"
)


class Chunk(NamedTuple):
    version: str
    digest: str
    index: int
    total: int


def parse(filename: str) -> Chunk:
    match = PART_RE.fullmatch(filename)
    if not match:
        raise store.ReleaseError(
            "Неверное имя части. Создавайте части скриптом Skryon.")
    version = match.group("version")
    store.code_for(version)
    index, total = int(match.group("index")), int(match.group("count"))
    if not (2 <= total <= MAX_PARTS and 1 <= index <= total):
        raise store.ReleaseError("Некорректный номер или количество частей.")
    return Chunk(version, match.group("digest"), index, total)


def part_dir(admin_id: int, chunk: Chunk) -> Path:
    if not isinstance(admin_id, int) or admin_id <= 0:
        raise store.ReleaseError("Неверный администратор.")
    return (store.release_root() / "incoming_parts" / str(admin_id) /
            chunk.digest)


def _output_name(chunk: Chunk, index: int) -> str:
    return f"{index:03d}.part"


def _all_present(directory: Path, chunk: Chunk) -> bool:
    return all((directory / _output_name(chunk, index)).is_file()
               for index in range(1, chunk.total + 1))


def add_part(uploaded: Path, filename: str, declared_size: int,
             admin_id: int) -> tuple[int, int, dict | None]:
    """Return (received, total, staged_release). Called off the asyncio loop."""
    chunk = parse(filename)
    if not 0 < declared_size <= PART_BYTES:
        raise store.ReleaseError("Каждая часть должна быть не больше 18 МиБ.")
    if chunk.index != chunk.total and declared_size != PART_BYTES:
        raise store.ReleaseError("Размер промежуточной части неверен.")
    if not uploaded.is_file() or uploaded.stat().st_size != declared_size:
        raise store.ReleaseError("Часть Telegram получена не полностью.")

    directory = part_dir(admin_id, chunk)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    meta = directory / "upload.json"
    if meta.exists():
        import json
        saved = json.loads(meta.read_text(encoding="utf-8"))
        if saved != {"total": chunk.total, "version": chunk.version}:
            raise store.ReleaseError("Части разных сборок смешаны, загрузка отменена.")
    else:
        store._atomic_json(meta, {"total": chunk.total, "version": chunk.version})
    path = directory / _output_name(chunk, chunk.index)
    # Resending a part is safe. Do not overwrite unexpected different contents.
    if path.exists():
        if (path.stat().st_size != declared_size or
                store._sha256(path) != store._sha256(uploaded)):
            raise store.ReleaseError("Повторная часть отличается от ранее принятой.")
        uploaded.unlink(missing_ok=True)
    else:
        os.replace(uploaded, path)
        os.chmod(path, 0o600)

    received = sum((directory / _output_name(chunk, i)).is_file()
                   for i in range(1, chunk.total + 1))
    if received != chunk.total:
        return received, chunk.total, None

    # Only the final part can be short. Reject unbounded or malformed batches.
    last = directory / _output_name(chunk, chunk.total)
    if not (0 < last.stat().st_size <= PART_BYTES):
        raise store.ReleaseError("Последняя часть пустая или слишком большая.")
    calculated_size = (chunk.total - 1) * PART_BYTES + last.stat().st_size
    if calculated_size >= store.MAX_UPLOAD:
        raise store.ReleaseError("Общий размер выпуска превышает лимит.")

    dest = None
    try:
        incoming = store.release_root() / "incoming"
        incoming.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=incoming, prefix="assembled-", suffix=".exe", delete=False
        ) as out:
            dest = Path(out.name)
            os.chmod(dest, 0o600)
            digest = hashlib.sha256()
            for i in range(1, chunk.total + 1):
                piece = directory / _output_name(chunk, i)
                if i != chunk.total and piece.stat().st_size != PART_BYTES:
                    raise store.ReleaseError("Повреждена промежуточная часть.")
                with piece.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        out.write(block)
                        digest.update(block)
            out.flush()
            os.fsync(out.fileno())
        if digest.hexdigest() != chunk.digest:
            # Cannot restore missing bytes by retrying just the last piece.
            shutil.rmtree(directory)
            raise store.ReleaseError(
                "SHA-256 собранного EXE не совпадает. Подготовьте и отправьте части заново.")
        if store._pe_version(dest) != chunk.version:
            raise store.ReleaseError("Версия Windows в EXE не совпала с названием частей.")
        row = store.stage(dest, f"SkryonSetup-{chunk.version}.exe", admin_id)
        dest = None
        shutil.rmtree(directory)
        return received, chunk.total, row
    finally:
        if dest is not None:
            dest.unlink(missing_ok=True)


def cancel_parts(admin_id: int) -> int:
    user_root = store.release_root() / "incoming_parts" / str(admin_id)
    if not user_root.is_dir():
        return 0
    count = sum(p.is_dir() for p in user_root.iterdir())
    shutil.rmtree(user_root)
    return count
