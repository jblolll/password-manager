#!/usr/bin/python3 -I
# PAM helper, second half (run by recall-stick-check when the stick isn't plugged in HERE): succeed only while your stick is
# plugged into another of your paired computers. Installed root-owned by `install.sh --cross-device`; it never imports
# anything from your home folder. It reads the newest heartbeat the Password Manager's presence service fetched, and accepts
# it only if it is signed by the stick (checked against the public key installed when this computer was paired, with the
# stick plugged in), addressed to this computer, current, and not a "stick removed" notice. Same checks as presence.verify.
import base64
import json
import os
import pwd
import stat
import sys
import syslog
import time

USER = "__USER__"
PUB = "/etc/recall-stick.presence.pub"
NAME = "/etc/recall-stick.presence.name"
V, TTL_S, SKEW_S = 1, 75, 120


def refuse(why):
    """Say why in the system log (journalctl -t recall-stick), so a lock screen that didn't open can be explained."""
    if os.geteuid() != 0:
        return 1                    # the app checking status every few seconds: nothing to log
    try:
        syslog.openlog("recall-stick", 0, syslog.LOG_AUTH)
        syslog.syslog(syslog.LOG_INFO, f"{os.environ.get('PAM_SERVICE', '?')}: stick not accepted from your other computer: {why}")
    except OSError:
        pass
    return 1


def main():
    if os.environ.get("PAM_USER") != USER:
        return 1
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    pw = pwd.getpwnam(USER)
    path = os.path.join(pw.pw_dir, ".config", "jblol-password-manager", "presence-in.json")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return refuse("no current heartbeat (is the stick in your other computer, and the presence service running here?)")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != pw.pw_uid or st.st_size > 65536:
            return refuse("heartbeat file isn't a plain file of yours")
        data = os.read(fd, 65536)
    finally:
        os.close(fd)
    try:
        with open(PUB, "rb") as f:
            pub = serialization.load_pem_public_key(f.read())
        with open(NAME) as f:
            me = f.read().strip()
        msg = json.loads(data)
        body = msg["body"]
        pub.verify(base64.b64decode(msg["sig"], validate=True), json.dumps(body, sort_keys=True, separators=(",", ":")).encode())
        now = time.time()
        if body.get("v") != V or body.get("revoked"):
            return refuse("the stick was removed from the other computer")
        if me not in body.get("to", []) or body.get("from") == me:
            return refuse(f"heartbeat isn't addressed to {me} (pair this computer again with the stick in it)")
        if not (body["iat"] - SKEW_S <= now <= body["exp"] and body["exp"] - body["iat"] <= TTL_S):
            return refuse(f"heartbeat out of date by {int(now - body['exp'])} s (network, or the two clocks disagree)")
        return 0
    except InvalidSignature:
        return refuse("signature doesn't match this stick")
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as e:
        return refuse(f"unreadable heartbeat ({type(e).__name__})")


if __name__ == "__main__":
    sys.exit(main())
