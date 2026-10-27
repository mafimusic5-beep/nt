"""Offline verification of the exact publisher signature pinned in Windows."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.bot.utils import windows_release_store as store

HASH = "b7ff8ff40565e0994dc3762344039d95aae57703642791205bf23b72ca656f3c"
SIGNATURE = (
    "MEYCIQClVBNvlM3CYKF8UDG3ThaLQihr84TqEqTRXXApfscgk"
    "AIhAJNAVr0YsfrQZvPVhAzzcemnBYeKh2dKe6kVZSPQYo3I"
)
VALID = {
    "versionCode": 20261027,
    "version": "1.0.1",
    "downloadUrl": "https://skryon.ru/downloads/windows/SkryonSetup-1.0.1.exe",
    "sha256": HASH,
    "signature": SIGNATURE,
}


def candidate():
    return {
        "admin": 42,
        "version": "1.0.1",
        "versionCode": 20261027,
        "buildId": "26K101",
        "sha256": HASH,
        "filename": "SkryonSetup-1.0.1.exe",
        "authenticodeValid": False,
    }


def manifest_bytes(data=None):
    return json.dumps(VALID if data is None else data).encode("utf-8")


def test_real_26k101_signature_uses_windows_pinned_publisher_key():
    assert store._validate_windows_publisher_manifest(
        manifest_bytes(), candidate()
    )["versionCode"] == 20261027


@pytest.mark.parametrize("field,value", [
    ("version", "1.0.2"),
    ("versionCode", 20261028),
    ("downloadUrl", "https://evil.example/setup.exe"),
    ("sha256", "a" * 64),
    ("signature", "AAAAAAAA"),
])
def test_tampered_publisher_manifest_rejected(field, value):
    obj = dict(VALID)
    obj[field] = value
    with pytest.raises(store.ReleaseError):
        store._validate_windows_publisher_manifest(manifest_bytes(obj), candidate())


def test_duplicate_json_fields_rejected():
    payload = manifest_bytes()[:-1] + b',"versionCode":20261027}'
    with pytest.raises(store.ReleaseError, match="Повторяющееся"):
        store._validate_windows_publisher_manifest(payload, candidate())


def test_manifest_with_unknown_field_rejected():
    obj = dict(VALID, extra="sneaky")
    with pytest.raises(store.ReleaseError):
        store._validate_windows_publisher_manifest(manifest_bytes(obj), candidate())


def test_staged_installer_must_match_filename_and_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("SKRYON_WINDOWS_RELEASE_DIR", str(tmp_path))
    row = candidate()
    state = store.state()
    state["staged"][HASH] = row
    store._atomic_json(tmp_path / "state.json", state)

    with pytest.raises(store.ReleaseError):
        store.attach_publisher_manifest(
            manifest_bytes(), 7, "SkryonRelease-26K101.json"
        )
    with pytest.raises(store.ReleaseError):
        store.attach_publisher_manifest(
            manifest_bytes(), 42, "SkryonRelease-26K102.json"
        )
    attached = store.attach_publisher_manifest(
        manifest_bytes(), 42, "SkryonRelease-26K101.json"
    )
    assert attached["publisherSignatureValid"] is True
    assert store.state()["staged"][HASH]["signedManifest"] == VALID


def test_unsigned_publish_is_default_denied_even_with_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("SKRYON_WINDOWS_RELEASE_DIR", str(tmp_path))
    monkeypatch.delenv("SKRYON_RELEASE_ALLOW_UNSIGNED", raising=False)
    installer = tmp_path / "staged.exe"
    installer.write_bytes(b"MZplaceholder")
    state = store.state()
    row = candidate()
    row["signedManifest"] = dict(VALID)
    row["path"] = str(installer)
    state["staged"][HASH] = row
    store._atomic_json(tmp_path / "state.json", state)
    with pytest.raises(store.ReleaseError, match="неподписанный"):
        asyncio.run(store.publish(HASH, 42))


def test_missing_offline_manifest_prevents_even_signed_publish(tmp_path, monkeypatch):
    monkeypatch.setenv("SKRYON_WINDOWS_RELEASE_DIR", str(tmp_path))
    monkeypatch.delenv("SKRYON_RELEASE_ALLOW_UNSIGNED", raising=False)
    row = candidate()
    row["authenticodeValid"] = True
    state = store.state()
    state["staged"][HASH] = row
    store._atomic_json(tmp_path / "state.json", state)
    with pytest.raises(store.ReleaseError, match="SkryonRelease"):
        asyncio.run(store.publish(HASH, 42))
