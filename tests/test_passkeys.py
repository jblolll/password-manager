"""Passkeys: the authenticator logic in pmapp/passkeys.py checked the way a website would check it (parse what the browser would hand over, verify the signature).

    python3 -m unittest tests.test_passkeys -v
"""
import hashlib
import os
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

from pmapp import model, passkeys as pk  # noqa: E402


def cbor_decode(data):
    """A small CBOR reader (what a site's server library does with the attestation object)."""
    def item(i):
        major, info = data[i] >> 5, data[i] & 31
        i += 1
        if info < 24:
            n = info
        elif info == 24:
            n, i = data[i], i + 1
        elif info == 25:
            n, i = struct.unpack(">H", data[i:i + 2])[0], i + 2
        elif info == 26:
            n, i = struct.unpack(">I", data[i:i + 4])[0], i + 4
        else:
            raise ValueError("unsupported")
        if major == 0:
            return n, i
        if major == 1:
            return -1 - n, i
        if major == 2:
            return bytes(data[i:i + n]), i + n
        if major == 3:
            return data[i:i + n].decode(), i + n
        if major == 4:
            out = []
            for _ in range(n):
                v, i = item(i)
                out.append(v)
            return out, i
        if major == 5:
            out = {}
            for _ in range(n):
                k, i = item(i)
                v, i = item(i)
                out[k] = v
            return out, i
        raise ValueError("unsupported")
    v, end = item(0)
    return v, end


def parse_attested(auth):
    rp_hash, flags, counter = auth[:32], auth[32], struct.unpack(">I", auth[33:37])[0]
    aaguid, n = auth[37:53], struct.unpack(">H", auth[53:55])[0]
    cred_id = auth[55:55 + n]
    cose, _ = cbor_decode(auth[55 + n:])
    return rp_hash, flags, counter, aaguid, cred_id, cose


def public_key_from_cose(cose):
    return ec.EllipticCurvePublicNumbers(int.from_bytes(cose[-2], "big"), int.from_bytes(cose[-3], "big"), ec.SECP256R1()).public_key()


USER = {"id": pk.b64u(b"user-handle-1"), "name": "justin@example.com", "displayName": "Justin"}


class Making(unittest.TestCase):
    def setUp(self):
        self.chash = hashlib.sha256(b'{"type":"webauthn.create","challenge":"abc","origin":"https://example.com"}').digest()
        self.record, self.answer = pk.create("example.com", "Example", USER, self.chash, [-7, -257])

    def test_the_browser_gets_a_valid_attestation_object(self):
        att, _ = cbor_decode(pk.unb64u(self.answer["attestationObject"]))
        self.assertEqual(att["fmt"], "none")
        self.assertEqual(att["attStmt"], {})
        self.assertEqual(att["authData"], pk.unb64u(self.answer["authData"]))
        rp_hash, flags, counter, aaguid, cred_id, cose = parse_attested(att["authData"])
        self.assertEqual(rp_hash, hashlib.sha256(b"example.com").digest())
        self.assertEqual(flags, pk.UP | pk.UV | pk.BE | pk.BS | pk.AT)
        self.assertEqual((counter, aaguid), (0, bytes(16)))
        self.assertEqual(cred_id, pk.unb64u(self.answer["credentialId"]))
        self.assertEqual((cose[1], cose[3], cose[-1]), (2, -7, 1), "an EC2 / ES256 / P-256 key")
        self.assertEqual(len(cose[-2]), 32)
        self.assertEqual(list(cose), [1, 3, -1, -2, -3], "keys in the canonical order")

    def test_the_stored_record_holds_what_is_needed_to_sign_in_later(self):
        r = self.record
        self.assertEqual((r["rp_id"], r["rp_name"], r["user_name"], r["user_handle"], r["alg"]), ("example.com", "Example", "justin@example.com", USER["id"], -7))
        self.assertEqual(r["cred_id"], self.answer["credentialId"])
        self.assertGreaterEqual(len(pk.unb64u(r["cred_id"])), 16)
        self.assertNotEqual(pk.create("example.com", "Example", USER, self.chash)[0]["cred_id"], r["cred_id"], "every passkey is new")

    def test_the_public_key_the_browser_is_told_about_matches(self):
        att, _ = cbor_decode(pk.unb64u(self.answer["attestationObject"]))
        cose = parse_attested(att["authData"])[5]
        spki_key = __import__("cryptography.hazmat.primitives.serialization", fromlist=["x"]).load_der_public_key(pk.unb64u(self.answer["publicKey"]))
        self.assertEqual(spki_key.public_numbers(), public_key_from_cose(cose).public_numbers())

    def test_only_es256_is_made(self):
        with self.assertRaises(pk.PasskeyError) as cm:
            pk.create("example.com", "Example", USER, self.chash, [-257, -8])
        self.assertEqual(cm.exception.name, "NotSupportedError")
        pk.create("example.com", "Example", USER, self.chash, [])            # a site that names none is fine


class SigningIn(unittest.TestCase):
    def test_a_site_can_verify_the_signature_with_the_key_it_was_given_at_sign_up(self):
        record, answer = pk.create("example.com", "Example", USER, b"x" * 32)
        att, _ = cbor_decode(pk.unb64u(answer["attestationObject"]))
        pub = public_key_from_cose(parse_attested(att["authData"])[5])
        client_data = b'{"type":"webauthn.get","challenge":"zzz","origin":"https://example.com"}'
        chash = hashlib.sha256(client_data).digest()
        got = pk.sign(record, chash)
        auth, sig = pk.unb64u(got["authenticatorData"]), pk.unb64u(got["signature"])
        self.assertEqual(auth[:32], hashlib.sha256(b"example.com").digest())
        self.assertEqual(auth[32], pk.UP | pk.UV | pk.BE | pk.BS)
        self.assertEqual(len(auth), 37, "no attested data when signing in")
        pub.verify(sig, auth + chash, ec.ECDSA(hashes.SHA256()))             # raises if wrong
        self.assertEqual((got["credentialId"], got["userHandle"]), (record["cred_id"], USER["id"]))
        with self.assertRaises(Exception):
            pub.verify(sig, auth + hashlib.sha256(b"another challenge").digest(), ec.ECDSA(hashes.SHA256()))

    def test_a_passkey_for_one_site_signs_for_that_site_only(self):
        record, _ = pk.create("example.com", "Example", USER, b"x" * 32)
        got = pk.sign(record, b"y" * 32)
        self.assertNotEqual(pk.unb64u(got["authenticatorData"])[:32], hashlib.sha256(b"evil.com").digest())


class WhoMayAsk(unittest.TestCase):
    def test_origin_rules(self):
        ok = pk.check_origin
        self.assertEqual(ok("https://example.com", "example.com"), "example.com")
        self.assertEqual(ok("https://login.example.com", "example.com"), "example.com", "a parent domain is allowed")
        self.assertEqual(ok("https://login.example.com", ""), "login.example.com", "no rpId means the page's own host")
        self.assertEqual(ok("http://localhost:8000", "localhost"), "localhost")
        for origin, rp in (("http://example.com", "example.com"), ("https://example.com", "evil.com"), ("https://evil.com", "example.com"),
                           ("https://notexample.com", "example.com"), ("https://example.com", "com"), ("https://shop.bbc.co.uk", "co.uk"),
                           ("https://example.com", "exa mple.com"), ("", "example.com"), ("ftp://example.com", "example.com"), ("https://1.2.3.4", "2.3.4")):
            with self.assertRaises(pk.PasskeyError, msg=(origin, rp)) as cm:
                ok(origin, rp)
            self.assertEqual(cm.exception.name, "SecurityError")


class Storing(unittest.TestCase):
    def test_a_passkey_is_a_vault_item_that_survives_the_notes_round_trip(self):
        record, _ = pk.create("example.com", "Example", USER, b"x" * 32)
        item = pk.as_item(record, tags=["work"])
        self.assertEqual((item["kind"], item["group"], item["title"], item["username"], item["url"]), ("passkey", "Passkeys", "Example (justin@example.com)", "justin@example.com", "https://example.com"))
        notes = model.pack("", "passkey", item["data"])
        _plain, kind, data = model.unpack(notes)
        self.assertEqual(kind, "passkey")
        self.assertEqual(data["key"], record["key"])
        self.assertEqual(data["tags"], ["work"])
        self.assertEqual(model.kind_of({"group": "Passkeys", "data": {}}), "passkey")

    def test_finding_the_passkeys_for_a_site(self):
        a, _ = pk.create("example.com", "Example", {"id": "u1", "name": "a"}, b"x" * 32)
        b, _ = pk.create("example.com", "Example", {"id": "u2", "name": "b"}, b"x" * 32)
        c, _ = pk.create("other.org", "Other", {"id": "u3", "name": "c"}, b"x" * 32)
        entries = [pk.as_item(r) for r in (a, b, c)] + [{"kind": "login", "data": {}, "title": "x", "group": "Websites"}]
        self.assertEqual({e["data"]["user_name"] for e in pk.find(entries, "example.com")}, {"a", "b"})
        self.assertEqual([e["data"]["user_name"] for e in pk.find(entries, "example.com", allow=[b["cred_id"]])], ["b"])
        self.assertEqual(pk.find(entries, "nothing.net"), [])


class Cbor(unittest.TestCase):
    def test_round_trip(self):
        for v in (0, 23, 24, 255, 256, 70000, -1, -24, -25, -300, b"", b"\x01\x02", "", "hé", [1, [2, b"x"]], {"a": 1, 2: {"b": b"c"}, -1: "z"}):
            got, end = cbor_decode(pk.cbor(v))
            self.assertEqual(got, v)
            self.assertEqual(end, len(pk.cbor(v)))
        self.assertEqual(pk.cbor({1: 2}), bytes([0xA1, 0x01, 0x02]))
        self.assertEqual(pk.cbor(-7), bytes([0x26]))


if __name__ == "__main__":
    unittest.main()
