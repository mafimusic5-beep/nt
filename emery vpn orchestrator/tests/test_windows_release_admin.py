"""Release safeguards run without Telegram, SSH keys or production endpoints."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from src.bot.utils import windows_release_store as store


def test_version_code_monotonic_for_release_lane():
    assert store.code_for("1.0.0") == 20261026
    assert store.code_for("1.0.1") == 20261027
    assert store.code_for("1.0.42") == 20261068
    with pytest.raises(store.ReleaseError):
        store.code_for("2.0.0")
    with pytest.raises(store.ReleaseError):
        store.code_for("../1.0.2")


@pytest.mark.parametrize("filename", [
    "../../trick.exe", "/tmp/evil.exe", "bad\\name.exe", "abc.exe .",
    "payload.ps1", "evil☃.exe",
])
def test_staging_rejects_unsafe_filenames(filename):
    with pytest.raises(store.ReleaseError):
        store._safe_filename(filename)


def test_staging_rejects_non_pe_files(tmp_path):
    payload = tmp_path / "sample.exe"
    payload.write_bytes(b"XX" * 200000)
    with pytest.raises(store.ReleaseError, match="Windows PE"):
        store.stage(payload, "SkryonSetup-1.0.0.exe", 42)


def test_signed_manifest_canonical_matches_client():
    key = ec.generate_private_key(ec.SECP256R1())
    row = {
        "version": "1.0.2",
        "versionCode": 20261028,
        "filename": "SkryonSetup-1.0.2.exe",
        "sha256": "ab" * 32,
    }
    manifest = store._signed_manifest(row, key)
    signed = "\n".join([
        "skryon-windows-update-v1",
        "version_code=20261028",
        "version=1.0.2",
        "download_url=https://skryon.ru/downloads/windows/SkryonSetup-1.0.2.exe",
        "sha256=" + "ab" * 32,
    ]).encode()
    key.public_key().verify(
        base64.b64decode(manifest["signature"]), signed, ec.ECDSA(hashes.SHA256()))
    assert manifest["downloadUrl"].startswith("https://skryon.ru/")
    assert manifest["versionCode"] == row["versionCode"]


def test_missing_publish_config_fails_closed(monkeypatch):
    for setting in ("SKRYON_RELEASE_SIGNING_KEY_FILE",
                    "SKRYON_RELEASE_CLIENT_PUBLIC_SPKI_B64",
                    "SKRYON_RELEASE_SFTP_HOST",
                    "SKRYON_RELEASE_SFTP_USER",
                    "SKRYON_RELEASE_SFTP_PRIVATE_KEY",
                    "SKRYON_RELEASE_SFTP_HOSTKEY_SHA256"):
        monkeypatch.delenv(setting, raising=False)
    issues = store.publishing_readiness()
    assert issues and any("ключ" in issue.lower() for issue in issues)


def test_cannot_publish_unsigned_binary(tmp_path, monkeypatch):
    monkeypatch.setenv("SKRYON_WINDOWS_RELEASE_DIR", str(tmp_path))
    exe = tmp_path / "unsigned.exe"
    exe.write_bytes(b"MZ" + b"0" * 150000)
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    state = store.state()
    state["staged"][digest] = {
        "admin": 42, "version": "1.0.1",
        "versionCode": 20261027, "sha256": digest,
        "path": str(exe), "authenticodeValid": False,
        "filename": "SkryonSetup-1.0.1.exe",
    }
    store._atomic_json(tmp_path / "state.json", state)
    with pytest.raises(store.ReleaseError, match="неподписанный"):
        asyncio.run(store.publish(digest, 42))


def test_real_installer_pe_version_if_present():
    installer = os.environ.get("SKRYON_TEST_PE_INSTALLER", "")
    if not installer:
        pytest.skip("No test Windows installer on this environment")
    assert store._pe_version(Path(installer)) == "1.0.0"
