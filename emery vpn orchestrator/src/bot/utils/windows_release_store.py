"""Safe Windows release storage and publishing, used only by the Telegram admin bot.

No untrusted executable is executed. Publishing requires BOTH platform
Authenticode verification and a publisher-signed manifest. A staged candidate
may always be discarded without affecting users.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

VERSION_RE = re.compile(r"^(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})$")
MAX_UPLOAD = 2_000_000_000
BASE_CODE = 20261026  # 1.0.0. Each increment must match the actual Windows binary.


class ReleaseError(Exception):
    pass


def release_root() -> Path:
    return Path(os.environ.get("SKRYON_WINDOWS_RELEASE_DIR",
        "/var/lib/skryon-bot/windows-releases")).resolve()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=".release-", delete=False) as target:
        tmp = Path(target.name)
        os.chmod(tmp, 0o600)
        json.dump(payload, target, ensure_ascii=False, indent=2)
        target.flush()
        os.fsync(target.fileno())
    os.replace(tmp, path)


def state() -> dict:
    path = release_root() / "state.json"
    if not path.exists():
        return {"staged": {}, "published": [], "current": None, "minimum": BASE_CODE}
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ReleaseError("Повреждён файл состояния релизов.")
    return obj


def _safe_filename(value: str) -> str:
    # Never use a Telegram filename as a filesystem path.
    if len(value) > 120 or not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ReleaseError("Допускается только обычное имя файла .exe.")
    if not value.lower().endswith(".exe"):
        raise ReleaseError("Нужен установщик Windows (.exe).")
    return value


def _pe_release_identity(file: Path) -> tuple[str, str | None]:
    """Read the internal update version and the *public* Skryon build label.

    Windows installers from build 26K101 onwards use:
      PE FileVersion    = 1.0.1 (monotonic update comparison)
      PE ProductVersion = 26K101 (user-facing build ID).
    Neither value proves authenticity without a valid Authenticode signature.
    """
    import pefile
    pe = pefile.PE(str(file), fast_load=False)
    try:
        if not pe.is_exe() or not pe.VS_FIXEDFILEINFO:
            raise ReleaseError("Нет корректных метаданных версии Windows.")
        build_id: str | None = None
        technical: str | None = None
        for group in getattr(pe, "FileInfo", []):
            for table in group:
                if getattr(table, "Key", b"") != b"StringFileInfo":
                    continue
                for strings in getattr(table, "StringTable", []):
                    entries = strings.entries
                    product_raw = entries.get(b"ProductVersion")
                    file_raw = entries.get(b"FileVersion")
                    product = (product_raw.decode("utf-8", "replace").strip()
                               if isinstance(product_raw, bytes) else str(product_raw or "").strip())
                    file_version = (file_raw.decode("utf-8", "replace").strip()
                                    if isinstance(file_raw, bytes) else str(file_raw or "").strip())
                    if BUILD_RE.fullmatch(product):
                        if build_id and build_id != product:
                            raise ReleaseError("Разные номера сборки в метаданных EXE.")
                        build_id = product
                    for candidate in (file_version, product):
                        m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?", candidate)
                        if not m or (m.group(4) and int(m.group(4)) != 0):
                            continue
                        version = ".".join(str(int(x)) for x in m.groups()[:3])
                        if VERSION_RE.fullmatch(version):
                            if technical and technical != version:
                                raise ReleaseError("Противоречивые технические версии EXE.")
                            technical = version
                            break
        if not technical:
            fixed = pe.VS_FIXEDFILEINFO[0]
            version_parts = (
                fixed.ProductVersionMS >> 16, fixed.ProductVersionMS & 65535,
                fixed.ProductVersionLS >> 16, fixed.ProductVersionLS & 65535,
            )
            technical = ".".join(str(x) for x in version_parts[:3])
            if not VERSION_RE.fullmatch(technical) or version_parts == (0, 0, 0, 0):
                raise ReleaseError("В установщике нет метаданных версии.")
        return technical, build_id
    finally:
        pe.close()


BUILD_RE = re.compile(r"^[0-9]{2}[A-HJ-M][0-9]{3,}$")


def _pe_version(file: Path) -> str:
    return _pe_release_identity(file)[0]


def _pe_build_id(file: Path) -> str | None:
    return _pe_release_identity(file)[1]


def code_for(version: str) -> int:
    """Only one monotonic release lane until a signed binary-embedded build id ships.

    We NEVER pretend that PE ProductVersion cryptographically proves the
    Windows client protocol code. The verified publisher build pipeline must
    generate both from one version variable.
    """
    m = VERSION_RE.fullmatch(version)
    if not m: raise ReleaseError("Некорректная версия.")
    major, minor, patch = map(int, m.groups())
    if major != 1 or minor != 0 or patch > 10000:
        raise ReleaseError("Версия вне поддерживаемой линейки 1.0.x.")
    return BASE_CODE + patch


def _sha256(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def has_valid_authenticode(file: Path) -> tuple[bool, str]:
    tool = shutil.which("osslsigncode")
    if tool is None:
        return False, "Проверка Authenticode недоступна (osslsigncode не установлен)"
    try:
        result = subprocess.run([tool, "verify", "-in", str(file)],
                                capture_output=True, text=True, timeout=25, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False, "Не удалось проверить Authenticode"
    if result.returncode != 0:
        return False, "Отсутствует действительная подпись Windows или не подтверждена цепочка"
    return True, "Подпись Windows подтверждена"


def _signing_key():
    key_path = os.environ.get("SKRYON_RELEASE_SIGNING_KEY_FILE", "").strip()
    if not key_path:
        raise ReleaseError("На боте не настроен отдельный ключ подписи релизов.")
    path = Path(key_path)
    if not path.is_file() or (path.stat().st_mode & 0o077):
        raise ReleaseError("Ключ подписи отсутствует или доступен другим пользователям.")
    password = os.environ.get("SKRYON_RELEASE_SIGNING_KEY_PASSWORD", "")
    key = serialization.load_pem_private_key(
        path.read_bytes(), password=password.encode() if password else None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ReleaseError("Неожиданный тип ключа издателя.")
    if not isinstance(key.curve, ec.SECP256R1):
        raise ReleaseError("Требуется ключ ECDSA P-256.")
    return key


def _current_key_matches_client() -> bool:
    # Must be set to the exact SPKI key embedded in the distributed client.
    expected = os.environ.get("SKRYON_RELEASE_CLIENT_PUBLIC_SPKI_B64", "").strip()
    if not expected:
        return False
    try:
        key = _signing_key()
        actual = key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        return base64.b64encode(actual).decode() == expected
    except (ReleaseError, ValueError):
        return False


def publishing_readiness() -> list[str]:
    problems = []
    # Standard Telegram cloud API is supported by uploading 18 MiB chunks
    # and validating the reconstructed EXE. No Telegram API ID/hash required.
    if shutil.which("osslsigncode") is None:
        problems.append("Не установлен проверяющий подпись Windows osslsigncode")
    if not _current_key_matches_client():
        problems.append("Подписывающий ключ не совпадает с ключом Windows-клиента")
    if not os.environ.get("SKRYON_RELEASE_SFTP_HOST", ""):
        problems.append("Не задан SFTP сервер")
    if not os.environ.get("SKRYON_RELEASE_SFTP_USER", ""):
        problems.append("Не задан SFTP пользователь")
    if not os.environ.get("SKRYON_RELEASE_SFTP_PRIVATE_KEY", ""):
        problems.append("Не задан SFTP ключ")
    if not os.environ.get("SKRYON_RELEASE_SFTP_HOSTKEY_SHA256", ""):
        problems.append("Не задан отпечаток SFTP хоста")
    return problems


def stage(file: Path, original_name: str, admin_id: int) -> dict:
    _safe_filename(original_name)
    if not file.is_file() or not 100_000 < file.stat().st_size < MAX_UPLOAD:
        raise ReleaseError("Неподдерживаемый размер установщика.")
    with file.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ReleaseError("Файл не является Windows PE (.exe).")
    version, build_id = _pe_release_identity(file)
    code = code_for(version)
    if code > BASE_CODE and not build_id:
        raise ReleaseError("В новом установщике отсутствует номер сборки Skryon.")
    digest = _sha256(file)
    signed, signing_message = has_valid_authenticode(file)
    record = {
        "version": version, "buildId": build_id, "versionCode": code, "sha256": digest,
        "size": file.stat().st_size, "authenticodeValid": signed,
        "authenticodeReason": signing_message, "admin": admin_id,
        "uploadedAt": int(time.time()), "filename": f"SkryonSetup-{version}.exe",
    }
    root = release_root()
    staged = root / "staged"
    staged.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = staged / f"{digest}.exe"
    os.replace(file, destination)
    os.chmod(destination, 0o600)
    record["path"] = str(destination)
    all_state = state()
    all_state.setdefault("staged", {})[digest] = record
    _atomic_json(root / "state.json", all_state)
    return record


def _signed_manifest(item: dict, key) -> dict:
    url = "https://skryon.ru/downloads/windows/" + item["filename"]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "skryon.ru":
        raise ReleaseError("Некорректный URL релиза.")
    canonical = "\n".join([
        "skryon-windows-update-v1",
        "version_code=" + str(item["versionCode"]),
        "version=" + item["version"],
        "download_url=" + url,
        "sha256=" + item["sha256"],
    ]).encode()
    signature = key.sign(canonical, ec.ECDSA(hashes.SHA256()))
    return {"versionCode": item["versionCode"], "version": item["version"],
            "downloadUrl": url, "sha256": item["sha256"],
            "signature": base64.b64encode(signature).decode("ascii")}


def _upload_to_site(local_installer: Path, manifest: dict, remote_filename: str) -> None:
    # SFTP deploy user must be jailed / restricted to this static site directory.
    import paramiko
    host = os.environ["SKRYON_RELEASE_SFTP_HOST"]
    user = os.environ["SKRYON_RELEASE_SFTP_USER"]
    keyfile = os.environ["SKRYON_RELEASE_SFTP_PRIVATE_KEY"]
    fp = os.environ["SKRYON_RELEASE_SFTP_HOSTKEY_SHA256"]
    root = os.environ.get("SKRYON_RELEASE_SITE_ROOT",
                          "/var/www/skryon.ru/html").rstrip("/")
    sock = None
    transport = None
    try:
        import socket
        sock = socket.create_connection((host, int(os.environ.get(
            "SKRYON_RELEASE_SFTP_PORT", "22"))), timeout=12)
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=12)
        server_key = transport.get_remote_server_key()
        observed = base64.b64encode(hashlib.sha256(server_key.asbytes()).digest()).decode().rstrip("=")
        if observed != fp.removeprefix("SHA256:").rstrip("="):
            raise ReleaseError("SFTP: отпечаток сервера изменился, публикация запрещена.")
        auth_key = paramiko.Ed25519Key.from_private_key_file(keyfile)
        transport.auth_publickey(user, auth_key)
        if not transport.is_authenticated():
            raise ReleaseError("SFTP: авторизация не удалась.")
        client = paramiko.SFTPClient.from_transport(transport)
        # Directories must be pre-provisioned with correct ownership.
        installer_dest = f"{root}/downloads/windows/{remote_filename}"
        manifest_dest = f"{root}/updates/windows/latest.json"
        suffix = str(int(time.time())) + "." + os.urandom(4).hex()
        new_installer = installer_dest + ".pending-" + suffix
        new_manifest = manifest_dest + ".pending-" + suffix
        try:
            client.put(str(local_installer), new_installer, confirm=True)
            with client.open(new_installer, "rb") as remote:
                digest = hashlib.sha256()
                while True:
                    chunk = remote.read(1024 * 1024)
                    if not chunk: break
                    digest.update(chunk)
                if digest.hexdigest() != manifest["sha256"]:
                    raise ReleaseError("SFTP: хеш на сервере не совпал.")
            client.chmod(new_installer, 0o644)
            client.rename(new_installer, installer_dest)
            with client.open(new_manifest, "wb") as output:
                output.write(json.dumps(manifest, ensure_ascii=False,
                                        separators=(",", ":")).encode("utf-8"))
            client.chmod(new_manifest, 0o644)
            # Latest pointer MUST be published last, after binary is available.
            # OpenSSH posix-rename performs an atomic replacement of the
            # previous latest.json (standard SFTP rename may fail if it exists).
            # If the extension isn't supported, fail closed: never delete
            # the old signed manifest before a new one is safely uploaded.
            client.posix_rename(new_manifest, manifest_dest)
        except Exception:
            for name in (new_installer, new_manifest):
                try: client.remove(name)
                except OSError: pass
            raise
        finally:
            client.close()
    finally:
        if transport: transport.close()
        if sock: sock.close()


async def publish(digest: str, requesting_admin: int) -> dict:
    data = state()
    record = data.get("staged", {}).get(digest)
    if not record: raise ReleaseError("Сборка не найдена среди подготовленных.")
    if record["admin"] != requesting_admin:
        raise ReleaseError("Подтверждать эту загрузку может только загрузивший администратор.")
    if not record["authenticodeValid"]:
        raise ReleaseError("Нельзя публиковать неподписанный установщик. Подпишите Windows Authenticode.")
    errors = publishing_readiness()
    if errors: raise ReleaseError("; ".join(errors))
    current = data.get("current")
    if current and record["versionCode"] <= current["versionCode"]:
        raise ReleaseError("Версия должна быть строго новее опубликованной. Для отката есть отдельная функция.")
    file = Path(record["path"])
    if not file.is_file() or _sha256(file) != digest:
        raise ReleaseError("Файл изменился после проверки. Публикация отменена.")
    key = _signing_key()
    manifest = _signed_manifest(record, key)
    # Use a worker thread: hashing/uploading 100+ MB must not block Telegram.
    await asyncio.to_thread(_upload_to_site, file, manifest, record["filename"])
    record = dict(record)
    record["publishedAt"] = int(time.time())
    record.pop("path", None)
    data.setdefault("published", []).append(record)
    data["current"] = record
    data["staged"].pop(digest, None)
    _atomic_json(release_root() / "state.json", data)
    return record


def discard(digest: str, admin_id: int) -> None:
    data = state()
    row = data.get("staged", {}).get(digest)
    if row is None or row.get("admin") != admin_id:
        raise ReleaseError("Нет такой загрузки.")
    Path(row["path"]).unlink(missing_ok=True)
    del data["staged"][digest]
    _atomic_json(release_root() / "state.json", data)
