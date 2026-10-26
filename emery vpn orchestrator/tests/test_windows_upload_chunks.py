"""Tests: 18 MiB Telegram cloud upload parts, ordering, integrity and safety."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.bot.utils import windows_release_store as store
from src.bot.utils import windows_upload_chunks as chunks


def filenames(data: bytes, part_bytes: int) -> list[tuple[str, bytes]]:
    digest = hashlib.sha256(data).hexdigest()
    count = (len(data) + part_bytes - 1) // part_bytes
    name = "SkryonSetup-1.0.2.exe"
    return [
        (f"{name}.{digest}.part{i:03d}of{count:03d}.skpart",
         data[(i-1)*part_bytes:i*part_bytes])
        for i in range(1, count + 1)
    ]


def test_parse_strict_and_no_path_traversal():
    digest = "f" * 64
    good = f"SkryonSetup-1.0.2.exe.{digest}.part001of006.skpart"
    assert chunks.parse(good).index == 1
    for wrong in (
        "../" + good, good + ".exe",
        good.replace("part001", "part000"),
        good.replace("of006", "of000"),
        good.replace("of006", "of111"),
        good.replace(digest, "GG" * 32),
        good.replace("1.0.2", "2.0.2"),
    ):
        with pytest.raises(store.ReleaseError):
            chunks.parse(wrong)


def test_parts_out_of_order_and_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("SKRYON_WINDOWS_RELEASE_DIR", str(tmp_path))
    monkeypatch.setattr(chunks, "PART_BYTES", 8)
    data = b"MZ" + bytes(range(31))
    expected_sha = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(store, "_pe_version", lambda file: "1.0.2")

    def fake_stage(file, filename, admin):
        assert file.read_bytes() == data
        assert filename == "SkryonSetup-1.0.2.exe"
        assert admin == 42
        file.unlink()
        return {"sha256": expected_sha, "version": "1.0.2"}

    monkeypatch.setattr(store, "stage", fake_stage)
    files = filenames(data, 8)
    assert len(files) == 5
    # Same bot state directory survives process restarts.
    for step, i in enumerate([3, 1, 4, 2, 5], start=1):
        name, payload = files[i - 1]
        received = tmp_path / f"incoming-{i}.part"
        received.write_bytes(payload)
        n, total, row = chunks.add_part(received, name, len(payload), 42)
        assert n == step
        assert total == 5
        assert (row is None) == (i != 5)
    assert row["sha256"] == expected_sha
    assert not chunks.part_dir(42, chunks.parse(files[0][0])).exists()


def test_duplicate_part_not_corrupted(tmp_path, monkeypatch):
    monkeypatch.setenv("SKRYON_WINDOWS_RELEASE_DIR", str(tmp_path))
    monkeypatch.setattr(chunks, "PART_BYTES", 4)
    first = filenames(b"ABCD" * 3, 4)[0]
    file = tmp_path / "first"
    file.write_bytes(first[1])
    assert chunks.add_part(file, first[0], 4, 77)[0] == 1
    repeat = tmp_path / "repeat"
    repeat.write_bytes(first[1])
    assert chunks.add_part(repeat, first[0], 4, 77)[0] == 1
    assert not repeat.exists()
    bad = tmp_path / "bad"
    bad.write_bytes(b"xxxx")
    with pytest.raises(store.ReleaseError, match="отличается"):
        chunks.add_part(bad, first[0], 4, 77)


def test_hash_mismatch_rejected_and_clears_incomplete_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("SKRYON_WINDOWS_RELEASE_DIR", str(tmp_path))
    monkeypatch.setattr(chunks, "PART_BYTES", 5)
    packets = filenames(b"MZabcdefg", 5)
    altered = [packets[0], (packets[1][0], b"0000")]
    for filename, payload in altered[:-1]:
        f = tmp_path / "p1"
        f.write_bytes(payload)
        chunks.add_part(f, filename, len(payload), 3)
    filename, payload = altered[-1]
    f = tmp_path / "p2"
    f.write_bytes(payload)
    with pytest.raises(store.ReleaseError, match="SHA-256"):
        chunks.add_part(f, filename, len(payload), 3)
    assert not chunks.part_dir(3, chunks.parse(filename)).exists()


def test_cancel_user_does_not_delete_other_user(tmp_path, monkeypatch):
    monkeypatch.setenv("SKRYON_WINDOWS_RELEASE_DIR", str(tmp_path))
    monkeypatch.setattr(chunks, "PART_BYTES", 4)
    name, content = filenames(b"abcd1234", 4)[0]
    for admin in (4, 5):
        f = tmp_path / f"upload-{admin}"
        f.write_bytes(content)
        chunks.add_part(f, name, len(content), admin)
    assert chunks.cancel_parts(4) == 1
    assert chunks.cancel_parts(4) == 0
    assert chunks.part_dir(5, chunks.parse(name)).exists()
