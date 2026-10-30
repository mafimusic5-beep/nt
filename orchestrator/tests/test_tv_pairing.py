"""Contract tests for QR transfer: the backend sees ciphertext, not codes."""
import base64
import hashlib
import sqlite3

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

import api
import config
import device_auth
import storage
import tv_pairing


def _start(client, key):
    public = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    response = client.post("/api/tv/pair/start", json={
        "public_key": base64.b64encode(public).decode("ascii")
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_qr_pair_transfers_only_ciphertext_once(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tv-pair.sqlite3")
    for module in (config, storage, device_auth, tv_pairing):
        monkeypatch.setattr(module, "DATABASE_PATH", db_path)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    activation_code = "A123456789B"
    with TestClient(api.app, base_url="https://skryon.ru") as client:
        session = _start(client, private_key)
        der = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        fingerprint = hashlib.sha256(der).hexdigest()
        assert session["url"] == (
            "https://skryon.ru/api/tv/activate?ticket=" + session["ticket"]
            + "#key=" + fingerprint
        )
        assert activation_code not in str(session)
        page = client.get("/api/tv/activate", params={"ticket": session["ticket"]})
        assert page.status_code == 200
        assert "expectedKeyHash" in page.text
        assert "actualHash !== expectedKeyHash" in page.text
        assert client.get("/api/tv/pair", params={"ticket": session["ticket"]}).status_code == 200
        info = client.get("/api/tv/pair/info", params={"ticket": session["ticket"]})
        assert info.status_code == 200
        encrypted = private_key.public_key().encrypt(
            activation_code.encode("ascii"),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        ciphertext = base64.b64encode(encrypted).decode("ascii")
        poll_body = {"ticket": session["ticket"], "poll_secret": session["poll_secret"]}
        assert client.post("/api/tv/pair/poll", json=poll_body).json()["status"] == "waiting"
        wrong_secret = client.post("/api/tv/pair/poll", json={
            **poll_body, "poll_secret": "x" * 43,
        })
        assert wrong_secret.status_code == 401
        submit = client.post("/api/tv/pair/submit", json={
            "ticket": session["ticket"], "ciphertext": ciphertext,
        }, headers={"origin": "https://skryon.ru"})
        assert submit.status_code == 200
        assert client.post("/api/tv/pair/submit", json={
            "ticket": session["ticket"], "ciphertext": ciphertext,
        }).status_code == 410
        assert activation_code.encode("ascii") not in (tmp_path / "tv-pair.sqlite3").read_bytes()
        received = client.post("/api/tv/pair/poll", json=poll_body)
        assert received.status_code == 200
        assert received.json() == {
            "ok": True, "status": "submitted", "ciphertext": ciphertext,
        }
        decrypted = private_key.decrypt(
            base64.b64decode(received.json()["ciphertext"]),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        assert decrypted.decode() == activation_code
        assert client.post("/api/tv/pair/poll", json=poll_body).status_code == 404


def test_qr_pair_rejects_old_ticket_invalid_cipher_and_foreign_origin(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tv-pair.sqlite3")
    for module in (config, storage, device_auth, tv_pairing):
        monkeypatch.setattr(module, "DATABASE_PATH", db_path)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with TestClient(api.app, base_url="https://skryon.ru") as client:
        session = _start(client, key)
        base = {"ticket": session["ticket"], "ciphertext": base64.b64encode(b"x" * 256).decode()}
        assert client.post("/api/tv/pair/submit", json=base, headers={
            "origin": "https://attacker.example"
        }).status_code == 403
        assert client.post("/api/tv/pair/submit", json={
            "ticket": session["ticket"], "ciphertext": "bad"
        }).status_code == 422
        with sqlite3.connect(db_path) as db:
            db.execute("UPDATE tv_pairing SET expires_at=1 WHERE ticket=?", (session["ticket"],))
        assert client.post("/api/tv/pair/submit", json=base).status_code == 410
        assert client.post("/api/tv/pair/poll", json={
            "ticket": session["ticket"], "poll_secret": session["poll_secret"],
        }).status_code == 410
        assert client.get("/api/tv/pair/info", params={"ticket": session["ticket"]}).status_code == 410
