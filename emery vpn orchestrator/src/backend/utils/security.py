import hashlib
import secrets
import string


_ALPHABET = string.ascii_uppercase + string.digits


def generate_activation_code(length: int = 12) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def hash_activation_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def mask_secret(secret: str, visible: int = 4) -> str:
    if len(secret) <= visible:
        return "*" * len(secret)
    return f"{secret[:visible]}{'*' * (len(secret) - visible)}"
