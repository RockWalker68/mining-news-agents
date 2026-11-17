"""
vault.py: locks Dan's Tab with a password.

Your repository and its web pages are public, so everything personal on Dan's
Tab is encrypted before it is saved or published: the page itself, The Rat's
notes on your interests, your ratings, and the messages you send from the
page. Without the password it is unreadable; with it, your phone unlocks the
page in a fraction of a second.

The password is the DAN_PASSWORD secret in GitHub (Settings -> Secrets and
variables -> Actions). Type the same password on the page once; your phone
remembers it.

How: AES-256-GCM encryption, with the key made from the password by PBKDF2
(SHA-256, 310,000 rounds). The page uses the same method, built into every
modern browser, to unlock itself.
"""

import base64
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ROUNDS = 310_000

_keys = {}


def _key(password, salt):
    cache = (password, salt)
    if cache not in _keys:
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ROUNDS)
        _keys[cache] = kdf.derive(password.encode("utf-8"))
    return _keys[cache]


def lock(text, password, salt=None):
    """Encrypt text. Returns a small dict that is safe to publish."""
    salt = salt or os.urandom(16)
    iv = os.urandom(12)
    sealed = AESGCM(_key(password, salt)).encrypt(iv, text.encode("utf-8"), None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"v": 1, "rounds": ROUNDS, "salt": b64(salt), "iv": b64(iv), "ct": b64(sealed)}


def unlock(box, password):
    """Decrypt what lock() made. Raises ValueError if the password is wrong."""
    try:
        salt, iv, sealed = (base64.b64decode(box[k]) for k in ("salt", "iv", "ct"))
        return AESGCM(_key(password, salt)).decrypt(iv, sealed, None).decode("utf-8")
    except Exception as error:
        raise ValueError("wrong password or damaged data") from error


def load(path, password, default):
    """Read an encrypted JSON file (or return `default` if it doesn't exist)."""
    if not path.exists():
        return default
    return json.loads(unlock(json.loads(path.read_text(encoding="utf-8")), password))


def save(path, data, password):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lock(json.dumps(data, ensure_ascii=False), password)), encoding="utf-8")
