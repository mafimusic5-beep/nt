"""Skryon Windows release staging and atomic publication.

The publisher ECDSA private key stays on the developer's Windows machine.
The Telegram bot may only distribute a release for which it receives a
valid signed manifest. Publication does not execute the uploaded installer.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

PUBLISHER_PUBLIC_SPKI = "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAETxGHQgaATere1BFfKQLU9gZzvqesNsAtZoo+0YDYhn/gfPdhvh/ooV+b4WeAlSL+e35ODRckNozzr/uc9HJs9w=="
PART_SIZE = 18 * 1024 * 1024
MAX_PARTS = 110
MAX_INSTALLER_BYTES = 2 * 1024 * 1024 * 1024
CHUNK_RE = re.compile(r"^SkryonSetup-(?P<ver>\d+\.\d+\.\d+)\.exe\.(?P<sha>[a-f0-9]{64})\.part(?P<idx>\d{3})of(?P<total>\d{3})\.skpart$")
MANIFEST_RE = re.compile(r"^SkryonRelease-(?P<build>[0-9]{2}[A-HJ-M]\d{3,})\.json$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
BUILD_RE = re.compile(r"^[0-9]{2}[A-HJ-M]\d{3,}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class ReleaseError(ValueError):
    pass


@dataclass(frozen=True)
class Manifest:
    version_code: int
    version: str
    download_url: str
    sha256: str
    signature: str
    build: str  # Filename label, corroborated with PE metadata before publish.

    @property
    def installer_name(self) -> str:
        return f"SkryonSetup-{self.version}.exe"


def _canonical(data: dict) -> bytes:
    return ("skryon-windows-update-v1\n"
            f"version_code={data['versionCode']}\n"
            f"version={data['version']}\n"
            f"download_url={data['downloadUrl']}\n"
            f"sha256={data['sha256']}").encode('utf-8')


def validate_manifest(blob: bytes, file_name: str) -> Manifest:
    name = MANIFEST_RE.fullmatch(file_name)
    if name is None or len(blob) > 12288:
        raise ReleaseError("Invalid Skryon manifest filename or file size")
    try:
        data = json.loads(blob)
    except (UnicodeError, ValueError) as exc:
        raise ReleaseError("Manifest is not valid JSON") from exc
    if not isinstance(data, dict) or set(data) != {'versionCode', 'version', 'downloadUrl', 'sha256', 'signature'}:
        raise ReleaseError("Manifest fields do not match the signed feed schema")
    code = data['versionCode']
    version = data['version']
    digest = data['sha256']
    url = data['downloadUrl']
    signature = data['signature']
    if type(code) is not int or not (1 <= code <= 2_147_483_647):
        raise ReleaseError('Invalid version code')
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise ReleaseError('Invalid release version')
    if not isinstance(digest, str) or not HEX_RE.fullmatch(digest):
        raise ReleaseError('Invalid installer SHA256')
    if not isinstance(signature, str) or len(signature) > 200:
        raise ReleaseError('Invalid manifest signature')
    if not isinstance(url, str):
        raise ReleaseError('Invalid download URL')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'skryon.ru' or
            parsed.netloc != 'skryon.ru' or parsed.query or parsed.fragment or
            parsed.username or parsed.password or
            parsed.path != f'/downloads/windows/SkryonSetup-{version}.exe'):
        raise ReleaseError('Installer must use the official immutable Skryon download URL')
    try:
        signature_bytes = base64.b64decode(signature, validate=True)
        key = serialization.load_der_public_key(base64.b64decode(PUBLISHER_PUBLIC_SPKI))
        key.verify(signature_bytes, _canonical(data), ec.ECDSA(hashes.SHA256()))
    except (ValueError, InvalidSignature, TypeError) as exc:
        raise ReleaseError('Publisher ECDSA signature is invalid') from exc
    return Manifest(code, version, url, digest, signature, name['build'])


def inspect_exe_version(path: Path) -> tuple[str, str]:
    """Read real PE VERSIONINFO, NOT a heuristic bytes/string search."""
    try:
        import pefile  # type: ignore
    except ImportError as exc:
        raise ReleaseError('PE version validator unavailable (install pefile)') from exc
    pe = None
    try:
        pe = pefile.PE(str(path), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
        values: dict[str, str] = {}
        for entry in getattr(pe, 'FileInfo', []) or []:
            if isinstance(entry, list):
                entries = entry
            else:
                entries = [entry]
            for info in entries:
                for table in getattr(info, 'StringTable', []) or []:
                    for key, value in table.entries.items():
                        k = key.decode('utf-8', errors='ignore') if isinstance(key, bytes) else str(key)
                        v = value.decode('utf-8', errors='ignore') if isinstance(value, bytes) else str(value)
                        if k in ('ProductVersion', 'FileVersion'):
                            values[k] = v.strip().strip('\0')
        build = values.get('ProductVersion', '')
        version = values.get('FileVersion', '')
        if version.endswith('.0') and version.count('.') == 3:
            version = version[:-2]
        if not BUILD_RE.fullmatch(build) or not VERSION_RE.fullmatch(version):
            raise ReleaseError('Windows installer has no valid Skryon VERSIONINFO')
        return build, version
    except ReleaseError:
        raise
    except Exception as exc:
        raise ReleaseError('Cannot inspect Skryon installer PE version resources') from exc
    finally:
        if pe is not None:
            pe.close()


class ReleaseStore:
    def __init__(self, staging: Path, site_root: Path, *, enabled: bool = False):
        self.staging = Path(staging)
        self.site_root = Path(site_root)
        self.enabled = enabled
        self.staging.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.public_installer_dir = self.site_root / 'downloads' / 'windows'
        self.public_manifest_dir = self.site_root / 'updates' / 'windows'
        self.public_manifest = self.public_manifest_dir / 'latest.json'

    def stage_manifest(self, payload: bytes, name: str) -> Manifest:
        manifest = validate_manifest(payload, name)
        directory = self.staging / manifest.sha256
        directory.mkdir(mode=0o700, exist_ok=True)
        # This cannot mutate any already stored signed manifest.
        output = directory / 'manifest.json'
        if output.exists():
            if output.read_bytes() != payload:
                raise ReleaseError('Another manifest already exists for this installer hash')
        else:
            self._atomic_bytes(output, payload, 0o600)
        (directory / 'build.txt').write_text(manifest.build, encoding='ascii')
        return manifest

    def chunk_target(self, name: str, declared_size: int) -> tuple[Path, str, int, int]:
        match = CHUNK_RE.fullmatch(name)
        if not match:
            raise ReleaseError('Expected an 18 MiB .skpart generated by Skryon')
        idx, total = int(match['idx']), int(match['total'])
        if not (1 <= total <= MAX_PARTS and 1 <= idx <= total):
            raise ReleaseError('Invalid part number')
        if not (1 <= declared_size <= PART_SIZE):
            raise ReleaseError('Invalid document size')
        if idx < total and declared_size != PART_SIZE:
            raise ReleaseError('All nonfinal parts must be exactly 18 MiB')
        directory = self.staging / match['sha']
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = directory / f'{idx:03d}-of-{total:03d}.part'
        return target, match['sha'], idx, total

    def stage_chunk_file(self, name: str, path: Path) -> tuple[str, int, int]:
        size = path.stat().st_size
        target, digest, index, total = self.chunk_target(name, size)
        # Refuse an accidental overwrite when a Telegram message is resent.
        if target.exists():
            if target.stat().st_size == size and hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(path.read_bytes()).digest():
                path.unlink(missing_ok=True)
                return digest, index, total
            raise ReleaseError('This part number already contains different bytes')
        os.replace(path, target)
        target.chmod(0o600)
        return digest, index, total

    def get_manifest(self, digest: str) -> Manifest:
        if not HEX_RE.fullmatch(digest):
            raise ReleaseError('Invalid release identifier')
        p = self.staging / digest / 'manifest.json'
        if not p.is_file():
            raise ReleaseError('Send signed SkryonRelease-BUILD.json before publishing')
        build = (self.staging / digest / 'build.txt').read_text(encoding='ascii').strip()
        manifest = validate_manifest(p.read_bytes(), f'SkryonRelease-{build}.json')
        if manifest.sha256 != digest:
            raise ReleaseError('Signed SHA256 and staged directory disagree')
        return manifest

    def progress(self, digest: str) -> tuple[Manifest, int, int]:
        manifest = self.get_manifest(digest)
        files = sorted((self.staging / digest).glob('???-of-???.part'))
        if not files:
            return manifest, 0, 0
        numbers = set()
        total_set = set()
        for file in files:
            match = re.fullmatch(r'(\d{3})-of-(\d{3})\.part', file.name)
            if match is None:
                continue
            index, total = map(int, match.groups())
            numbers.add(index)
            total_set.add(total)
        if len(total_set) != 1:
            raise ReleaseError('Multiple incompatible chunk series')
        total = next(iter(total_set))
        if total < 1 or total > MAX_PARTS or not all(1 <= n <= total for n in numbers):
            raise ReleaseError('Invalid chunk series')
        return manifest, len(numbers), total

    def _assembled_file(self, manifest: Manifest) -> Path:
        d = self.staging / manifest.sha256
        _, count, total = self.progress(manifest.sha256)
        if not total or count != total:
            raise ReleaseError(f'Incomplete installer: {count}/{total} parts')
        output = d / 'validated-installer.exe'
        if output.is_file():
            actual = self._sha_file(output)
            if actual == manifest.sha256:
                build, version = inspect_exe_version(output)
                if build != manifest.build or version != manifest.version:
                    raise ReleaseError('Cached installer VERSIONINFO does not match release')
                return output
            output.unlink()
        fd, tempfile_name = tempfile.mkstemp(prefix='.assembling-', dir=d)
        os.close(fd)
        temp = Path(tempfile_name)
        sha = hashlib.sha256()
        length = 0
        try:
            with temp.open('wb') as out:
                for i in range(1, total + 1):
                    part = d / f'{i:03d}-of-{total:03d}.part'
                    if not part.is_file():
                        raise ReleaseError(f'Missing part {i}/{total}')
                    size = part.stat().st_size
                    if size > PART_SIZE or (i < total and size != PART_SIZE):
                        raise ReleaseError('Invalid part byte length')
                    length += size
                    if length > MAX_INSTALLER_BYTES:
                        raise ReleaseError('Installer exceeds size limit')
                    with part.open('rb') as inp:
                        while data := inp.read(1024*1024):
                            sha.update(data)
                            out.write(data)
                out.flush()
                os.fsync(out.fileno())
            if sha.hexdigest() != manifest.sha256:
                raise ReleaseError('Installer hash does not match offline-signed manifest')
            build, version = inspect_exe_version(temp)
            if build != manifest.build or version != manifest.version:
                raise ReleaseError('Installer PE ProductVersion or FileVersion does not match the signed release')
            os.replace(temp, output)
            output.chmod(0o600)
            return output
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _sha_file(path: Path) -> str:
        h = hashlib.sha256()
        with path.open('rb') as f:
            while block := f.read(1024*1024):
                h.update(block)
        return h.hexdigest()

    @staticmethod
    def _atomic_bytes(path: Path, content: bytes, mode: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix='.skryon-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            Path(tmp_name).chmod(mode)
            os.replace(tmp_name, path)
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    def _existing_release(self) -> Manifest | None:
        if not self.public_manifest.exists():
            return None
        # Manifest name is contextual for validation; read current released binary's
        # build label from matching published record if present. ECDSA verification
        # must not depend on this unsigned informational build ID.
        data = json.loads(self.public_manifest.read_bytes())
        if not isinstance(data, dict):
            raise ReleaseError('Current production manifest is malformed')
        code = data.get('versionCode')
        if type(code) is not int or code < 1:
            raise ReleaseError('Current production versionCode invalid')
        if not isinstance(data.get('sha256'), str):
            raise ReleaseError('Current production digest invalid')
        # Verify signature with a well-formed temporary contextual build label.
        content = self.public_manifest.read_bytes()
        return validate_manifest(content, 'SkryonRelease-26K100.json')

    def validate_candidate(self, digest: str) -> Manifest:
        manifest = self.get_manifest(digest)
        current = self._existing_release()
        if current and manifest.version_code <= current.version_code:
            raise ReleaseError(f'Only newer version codes may be published (current: {current.version_code})')
        if not self.enabled:
            raise ReleaseError('Server publication is disabled until deployment and QA are completed')
        self._assembled_file(manifest)
        return manifest

    def publish(self, digest: str) -> Manifest:
        manifest = self.validate_candidate(digest)
        src = self._assembled_file(manifest)
        destination = self.public_installer_dir / manifest.installer_name
        # Symlink/path safety preflight.
        for parent in (self.site_root, self.public_installer_dir, self.public_manifest_dir):
            if parent.is_symlink():
                raise ReleaseError('Public destination cannot be a symlink')
        self.public_installer_dir.mkdir(parents=True, exist_ok=True)
        self.public_manifest_dir.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise ReleaseError('Installer path is a symlink')
        if destination.exists():
            if self._sha_file(destination) != manifest.sha256:
                raise ReleaseError('Existing installer filename points to different content')
        else:
            fd, tmp = tempfile.mkstemp(prefix='.upload-', dir=self.public_installer_dir)
            try:
                with os.fdopen(fd, 'wb') as out, src.open('rb') as inp:
                    shutil.copyfileobj(inp, out, length=1024*1024)
                    out.flush();os.fsync(out.fileno())
                if self._sha_file(Path(tmp)) != manifest.sha256:
                    raise ReleaseError('Staged public download hash mismatch')
                Path(tmp).chmod(0o644)
                os.replace(tmp, destination)
            finally:
                Path(tmp).unlink(missing_ok=True)
        # Manifest goes LAST. Any failure before this cannot announce a bad binary.
        manifest_blob = (self.staging / digest / 'manifest.json').read_bytes()
        self._atomic_bytes(self.public_manifest, manifest_blob, 0o644)
        return manifest
