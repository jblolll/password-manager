"""The link between the Password Manager on your computers and the Recall app on your phone: who is around, and "lock now" orders.

Every note is signed with a key made from the phone key (the one you typed into the phone once), so only your own computers and phone can
write one the others accept, and the Recall server, which only carries the notes, can neither invent a device nor give an order.
No Qt in here, so it can be tested on its own. The same signing is done in the phone app (phone/src/lib/link.ts): both must produce the same bytes.
"""
import hashlib
import hmac
import json
import re
import secrets
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

V = 1
ONLINE_S = 75            # a device that reported this recently counts as online
ORDER_TTL_S = 120        # an order older than this is ignored (so one left lying around never locks you later)
SEEN_KEEP = 60


def mac_key(phone_key_bytes):
    return HKDF(hashes.SHA256(), 32, None, b"jblol-pm link v1").derive(phone_key_bytes)


def canon(msg):
    """The exact bytes that get signed: sorted keys, no spaces, everything outside printable ASCII written as \\uXXXX (the phone does the same)."""
    return json.dumps(msg, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sign(key, msg):
    return {"msg": msg, "mac": hmac.new(key, canon(msg), hashlib.sha256).hexdigest()}


def verify(key, note):
    """The message if its signature is right, else None."""
    try:
        msg, mac = note["msg"], note["mac"]
        good = hmac.compare_digest(hmac.new(key, canon(msg), hashlib.sha256).hexdigest(), str(mac))
        return msg if good and isinstance(msg, dict) and msg.get("v") == V else None
    except (KeyError, TypeError, ValueError):
        return None


def device_id(name):
    return "pc-" + (re.sub(r"[^A-Za-z0-9._-]", "-", name)[:56] or "computer")


def beacon(key, did, name, locked, now=None, **extra):
    msg = {"v": V, "id": did, "name": name[:60], "kind": "computer", "iat": int(now or time.time()), "locked": bool(locked)}
    msg.update({k: v for k, v in extra.items() if v not in (None, "")})
    return sign(key, msg)


def order(key, to, frm, from_name, what="lock", now=None):
    return sign(key, {"v": V, "to": to, "from": frm, "from_name": from_name[:60], "do": what, "iat": int(now or time.time()), "n": secrets.token_hex(8)})


def devices(key, listing, me, now=None):
    """The other devices in a server listing, newest first: [{id, name, kind, locked, age, online, ...}]. `age` is in seconds by the server's clock."""
    server_now = float(listing.get("now") or now or time.time())
    out = []
    for d in listing.get("devices", []):
        msg = verify(key, d)
        if not msg or msg.get("id") != d.get("id") or msg.get("kind") not in ("phone", "computer") or msg["id"] == me:
            continue
        age = max(0.0, server_now - float(d.get("at") or 0))
        out.append({**{k: v for k, v in msg.items() if k not in ("v", "iat")}, "age": age, "online": age < ONLINE_S})
    return sorted(out, key=lambda x: x["age"])


def orders_for(key, listing, me, seen, now=None):
    """Fresh, genuine, not-yet-carried-out orders addressed to `me`. `seen` is a list of order nonces already done; returns (orders, new seen)."""
    server_now = float(listing.get("now") or now or time.time())
    got, seen = [], list(seen)
    for o in listing.get("orders", []):
        msg = verify(key, o)
        if not msg or msg.get("to") != me or msg.get("do") != "lock" or msg.get("n") in seen:
            continue
        if abs(server_now - float(msg.get("iat", 0))) > ORDER_TTL_S:
            continue
        got.append(msg)
        seen.append(msg["n"])
    return got, seen[-SEEN_KEEP:]
