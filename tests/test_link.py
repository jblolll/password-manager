"""The phone link: signed device notes and lock orders (pmapp/link.py), with no network."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jblol_pm as pm  # noqa: E402
from pmapp import link  # noqa: E402

KEY = link.mac_key(pm.phone_key_bytes(pm.new_phone_key()))
OTHER = link.mac_key(pm.phone_key_bytes(pm.new_phone_key()))
NOW = 1_800_000_000


def listing(devs=(), orders=(), now=NOW):
    return {"now": now, "devices": [{"id": d["msg"]["id"], "at": now - age, **d} for d, age in devs], "orders": [{"to": o["msg"]["to"], "at": now, **o} for o in orders]}


class Signing(unittest.TestCase):
    def test_roundtrip_and_tamper(self):
        n = link.beacon(KEY, "pc-a", "Justin’s laptop 💻", True, NOW)
        self.assertEqual(link.verify(KEY, n)["name"], "Justin’s laptop 💻")
        self.assertIsNone(link.verify(OTHER, n), "another key can't pass")
        bad = {**n, "msg": {**n["msg"], "locked": False}}
        self.assertIsNone(link.verify(KEY, bad), "a changed note fails")
        self.assertIsNone(link.verify(KEY, {"msg": "x"}))
        self.assertIsNone(link.verify(KEY, {}))

    def test_canonical_bytes_are_plain_ascii(self):
        self.assertEqual(link.canon({"b": 1, "a": "é\x7f\n"}), b'{"a":"\\u00e9\\u007f\\n","b":1}')

    def test_device_list(self):
        phone = link.sign(KEY, {"v": 1, "id": "ph-1", "name": "iPhone", "kind": "phone", "iat": NOW, "locked": False})
        me = link.beacon(KEY, "pc-me", "me", False, NOW)
        forged = link.sign(OTHER, {"v": 1, "id": "ph-2", "name": "Evil", "kind": "phone", "iat": NOW, "locked": False})
        swapped = {"id": "ph-9", **phone}                                     # a real note filed under another device's id
        out = link.devices(KEY, {**listing([(phone, 10), (me, 5), (forged, 5)]), "devices": listing([(phone, 10), (me, 5), (forged, 5)])["devices"] + [{**swapped, "at": NOW}]}, "pc-me")
        self.assertEqual([d["id"] for d in out], ["ph-1"])
        self.assertTrue(out[0]["online"] and out[0]["age"] == 10)
        old = link.devices(KEY, listing([(phone, 600)]), "pc-me")
        self.assertFalse(old[0]["online"])


class Orders(unittest.TestCase):
    def test_fresh_order_once(self):
        o = link.order(KEY, "pc-me", "ph-1", "iPhone", now=NOW - 5)
        got, seen = link.orders_for(KEY, listing(orders=[o]), "pc-me", [])
        self.assertEqual([m["from_name"] for m in got], ["iPhone"])
        again, _ = link.orders_for(KEY, listing(orders=[o]), "pc-me", seen)
        self.assertEqual(again, [], "one order never locks twice")

    def test_rejects_wrong_target_stale_forged(self):
        other = link.order(KEY, "pc-other", "ph-1", "iPhone", now=NOW)
        stale = link.order(KEY, "pc-me", "ph-1", "iPhone", now=NOW - 400)
        forged = link.order(OTHER, "pc-me", "ph-1", "iPhone", now=NOW)
        none, _ = link.orders_for(KEY, listing(orders=[other, stale, forged]), "pc-me", [])
        self.assertEqual(none, [])

    def test_fixture_for_the_phone_app(self):
        """The phone app's tests check these exact notes (the same bytes must verify there)."""
        k = link.mac_key(pm.phone_key_bytes("AAAA" * 13))
        notes = {"beacon": link.beacon(k, "pc-x", "Justin’s laptop 💻", True, NOW, os="Ubuntu 26.04"),
                 "order": link.order(k, "ph-1", "pc-x", "Justin’s laptop 💻", now=NOW)}
        out = Path(__file__).resolve().parent / "link-fixture.json"
        out.write_text(json.dumps({"key": "AAAA" * 13, "now": NOW, **notes}, indent=1, ensure_ascii=False))
        self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
