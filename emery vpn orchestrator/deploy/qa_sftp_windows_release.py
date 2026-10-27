#!/usr/bin/env python3
"""Non-publishing Skryon release-site SFTP test: uploads only hidden .probe files."""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import time
from pathlib import Path

import paramiko

host = os.environ["SKRYON_RELEASE_SFTP_HOST"]
user = os.environ["SKRYON_RELEASE_SFTP_USER"]
keyfile = os.environ["SKRYON_RELEASE_SFTP_PRIVATE_KEY"]
expected_fp = os.environ["SKRYON_RELEASE_SFTP_HOSTKEY_SHA256"].removeprefix("SHA256:").rstrip("=")
root = os.environ.get("SKRYON_RELEASE_SITE_ROOT", "/var/www/skryon.ru/html").rstrip("/")
assert host == "31.70.117.82", "Refusing unexpected site"
assert user == "skryonrelease", "Refusing unexpected SFTP account"
assert root == "", "Chrooted SFTP root must be /"
assert os.stat(keyfile).st_mode & 0o077 == 0, "Private SSH key must be owner-only"

sock = socket.create_connection((host, 22), timeout=12)
transport = paramiko.Transport(sock)
try:
    transport.get_security_options().key_types = ("ssh-ed25519",)
    transport.start_client(timeout=12)
    observed_fp = base64.b64encode(
        hashlib.sha256(transport.get_remote_server_key().asbytes()).digest()
    ).decode().rstrip("=")
    assert observed_fp == expected_fp, "Host identity mismatched"
    transport.auth_publickey(user, paramiko.Ed25519Key.from_private_key_file(keyfile))
    assert transport.is_authenticated(), "SFTP authentication failed"
    sftp = paramiko.SFTPClient.from_transport(transport)
    try:
        for location in (f"{root}/downloads/windows", f"{root}/updates/windows"):
            assert sftp.stat(location).st_mode, f"Missing target dir {location}"
            name = f"{location}/.skryon-private-sftp-probe-{int(time.time())}-{os.getpid()}"
            body = ("Skryon site-only SFTP QA " + str(time.time())).encode()
            try:
                with sftp.open(name, "wb") as f:
                    f.write(body)
                sftp.chmod(name, 0o644)
                with sftp.open(name, "rb") as f:
                    assert hashlib.sha256(f.read()).digest() == hashlib.sha256(body).digest()
            finally:
                try:
                    sftp.remove(name)
                except OSError:
                    pass
            print(f"SFTP_WRITE_READ_DELETE_OK={location}", flush=True)
        first = f"{root}/updates/windows/.skryon-atomic-probe-a-{os.getpid()}"
        second = f"{root}/updates/windows/.skryon-atomic-probe-b-{os.getpid()}"
        try:
            with sftp.open(first, "wb") as f:
                f.write(b"new")
            with sftp.open(second, "wb") as f:
                f.write(b"old")
            sftp.posix_rename(first, second)
            with sftp.open(second, "rb") as f:
                assert f.read() == b"new", "OpenSSH posix-rename wasn't atomic"
            print("SFTP_ATOMIC_REPLACE_OK=True", flush=True)
        finally:
            for p in (first, second):
                try:
                    sftp.remove(p)
                except OSError:
                    pass
    finally:
        sftp.close()
finally:
    transport.close()
    sock.close()
print("NO_REAL_INSTALLER_OR_LATEST_JSON_TOUCHED=True")
