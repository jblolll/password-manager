"""Passkeys: the part of a WebAuthn "authenticator" that lives in this app. The browser extension asks; this makes the key pair, the signed answers and the
stored record. Only ES256 (ECDSA P-256), which every site that supports passkeys accepts, and "none" attestation, which is what password managers use.

A passkey is stored as an ordinary vault item of the Passkey kind: the site, the account, and the private key (inside the encrypted vault, never anywhere else).
Passkeys saved here are marked "backed up", which tells sites they are not tied to one device.
"""
import base64
import hashlib
import os
import re
import struct
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

ALG_ES256 = -7
# authenticator data flags (WebAuthn): user present, user verified, backup eligible, backed up, attested credential data included
UP, UV, BE, BS, AT = 0x01, 0x04, 0x08, 0x10, 0x40
AAGUID = bytes(16)                                  # all zeros: "no particular make of authenticator" (keeps passkeys from being fingerprinted)


class PasskeyError(Exception):
    """`name` is the WebAuthn error the page should see: NotAllowedError, InvalidStateError, NotSupportedError, SecurityError."""

    def __init__(self, name, text=""):
        super().__init__(text or name)
        self.name = name


def b64u(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64u(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ------------------------------------------------------------------ a small CBOR writer (the answers are CBOR; the browser reads them)
def _head(major, n):
    if n < 24:
        return bytes([major << 5 | n])
    if n < 256:
        return bytes([major << 5 | 24, n])
    if n < 65536:
        return bytes([major << 5 | 25]) + struct.pack(">H", n)
    return bytes([major << 5 | 26]) + struct.pack(">I", n)


def cbor(v):
    """Encode ints, bytes, text, dicts (kept in the order given) and lists."""
    if isinstance(v, bool):
        return bytes([0xF5 if v else 0xF4])
    if isinstance(v, int):
        return _head(0, v) if v >= 0 else _head(1, -1 - v)
    if isinstance(v, (bytes, bytearray)):
        return _head(2, len(v)) + bytes(v)
    if isinstance(v, str):
        b = v.encode()
        return _head(3, len(b)) + b
    if isinstance(v, (list, tuple)):
        return _head(4, len(v)) + b"".join(cbor(x) for x in v)
    if isinstance(v, dict):
        return _head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(type(v))


# ------------------------------------------------------------------ is this site allowed to ask for this rpId?
def check_origin(origin, rp_id):
    """The WebAuthn rule: https (or localhost), and the rpId must be the page's own domain or a parent of it that is not just a public suffix like 'com'."""
    from urllib.parse import urlparse
    u = urlparse(origin or "")
    host = (u.hostname or "").lower()
    local = host == "localhost"
    if re.fullmatch(r"[\d.]+|.*:.*", host):                              # an address, not a domain name: sites can't have passkeys on those
        raise PasskeyError("SecurityError", "Passkeys belong to a website name, not an address.")
    if not host or not (u.scheme == "https" or (u.scheme == "http" and local)):
        raise PasskeyError("SecurityError", "Passkeys only work on secure (https) pages.")
    rp_id = (rp_id or host).lower()
    if not re.fullmatch(r"[a-z0-9.-]+", rp_id) or not (host == rp_id or host.endswith("." + rp_id)):
        raise PasskeyError("SecurityError", "That site can't ask for a passkey for another site.")
    if not local:
        from jblol_pm import reg_domain
        if len(rp_id.split(".")) < len(reg_domain(host).split(".")):
            raise PasskeyError("SecurityError", "That is too broad: a passkey must belong to one site.")
    return rp_id


# ------------------------------------------------------------------ making a passkey
def _cose_key(pub):
    n = pub.public_numbers()
    return cbor({1: 2, 3: ALG_ES256, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})


def _auth_data(rp_id, flags, counter, attested=b""):
    return hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", counter) + attested


def create(rp_id, rp_name, user, client_data_hash, algs=None):
    """A new passkey. Returns (record to store, answer for the browser). `user` = {id (base64url), name, displayName}."""
    if algs and ALG_ES256 not in algs:
        raise PasskeyError("NotSupportedError", "This password manager makes ES256 passkeys only, and this site doesn't accept those.")
    priv = ec.generate_private_key(ec.SECP256R1())
    cred_id = os.urandom(32)
    pub = priv.public_key()
    attested = AAGUID + struct.pack(">H", len(cred_id)) + cred_id + _cose_key(pub)
    auth = _auth_data(rp_id, UP | UV | BE | BS | AT, 0, attested)
    spki = pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    record = {"rp_id": rp_id, "rp_name": rp_name or rp_id, "cred_id": b64u(cred_id), "user_handle": user.get("id", ""), "user_name": user.get("name", ""),
              "display_name": user.get("displayName", ""), "alg": ALG_ES256, "created": time.strftime("%Y-%m-%d"),
              "key": b64u(priv.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))}
    answer = {"credentialId": b64u(cred_id), "authData": b64u(auth), "publicKey": b64u(spki), "publicKeyAlgorithm": ALG_ES256,
              "attestationObject": b64u(cbor({"fmt": "none", "attStmt": {}, "authData": auth}))}
    return record, answer


def sign(record, client_data_hash):
    """Sign in with a stored passkey. Returns the answer for the browser."""
    priv = serialization.load_der_private_key(unb64u(record["key"]), None)
    auth = _auth_data(record["rp_id"], UP | UV | BE | BS, 0)
    sig = priv.sign(auth + client_data_hash, ec.ECDSA(hashes.SHA256()))
    return {"credentialId": record["cred_id"], "authenticatorData": b64u(auth), "signature": b64u(sig), "userHandle": record.get("user_handle", "")}


def as_item(record, tags=None):
    """The vault item for a passkey record."""
    who = record.get("user_name") or record.get("display_name") or ""
    return {"group": "Passkeys", "title": f"{record['rp_name']}" + (f" ({who})" if who else ""), "username": who, "password": "", "url": f"https://{record['rp_id']}",
            "notes": "", "otp": "", "history": [], "pending_for": None, "modified": "", "kind": "passkey", "data": dict(record, **({"tags": tags} if tags else {}))}


def find(entries, rp_id, allow=None):
    """Stored passkeys for a site (newest first), limited to the ids the site named, if it named any."""
    out = []
    for e in entries:
        d = e.get("data") or {}
        if e.get("kind") == "passkey" and d.get("rp_id") == rp_id and d.get("key") and (not allow or d.get("cred_id") in allow):
            out.append(e)
    return sorted(out, key=lambda e: (e["data"].get("created", ""), e["title"]), reverse=True)
