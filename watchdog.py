#!/usr/bin/env python3
import base64
import json
import os
import socket
import sys
import urllib.error
import urllib.request

HOST = os.environ.get("OCI_HOST", "193.123.162.8")
PORT = int(os.environ.get("PORT", "22"))
STATE_PATH = "state.json"
RESTART_AFTER = int(os.environ.get("RESTART_AFTER", "3"))
REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]


def _api(url, method="GET", body=None):
    req = urllib.request.Request("https://api.github.com" + url, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            message = e.read().decode()
        except Exception:
            message = ""
        print("[watchdog] API %s %s -> HTTP %s %s" % (method, url.split("/repos/")[-1], e.code, message[:160]))
        return e.code, {}


def _read_state():
    code, res = _api("/repos/%s/contents/%s" % (REPO, STATE_PATH))
    if code == 200:
        try:
            content = json.loads(base64.b64decode(res["content"]).decode("utf-8"))
            return int(content.get("fail_count", 0)), res["sha"]
        except Exception as e:
            print("[watchdog] state decode error: %s" % e)
    return 0, None


def _write_state(fail_count, sha):
    for _attempt in range(3):
        try:
            body = {
                "message": "watchdog state: fail_count=%d" % fail_count,
                "content": base64.b64encode(
                    json.dumps({"fail_count": fail_count}).encode()
                ).decode(),
                "branch": "main",
            }
            if sha:
                body["sha"] = sha
            code, res = _api("/repos/%s/contents/%s" % (REPO, STATE_PATH), "PUT", body)
            if code in (200, 201):
                return True
            if code == 409:
                stored, sha = _read_state()
                _ = stored
                continue
            return False
        except Exception as e:
            print("[watchdog] state write error: %s" % e)
            return False
    return False


def host_up(timeout=12):
    try:
        with socket.create_connection((HOST, PORT), timeout=timeout) as s:
            s.settimeout(timeout)
            banner = s.recv(128)
            return banner.startswith(b"SSH-")
    except Exception:
        return False


def reboot_instance():
    import oci

    config = {
        "tenancy": os.environ["OCI_TENANCY"],
        "user": os.environ["OCI_USER"],
        "fingerprint": os.environ["OCI_FINGERPRINT"],
        "key_file_content": os.environ["OCI_KEY"],
        "region": os.environ["OCI_REGION"],
    }
    compute = oci.core.ComputeClient(config)
    resp = compute.instance_action(instance_id=os.environ["OCI_INSTANCE_ID"], action="RESET")
    return resp.status


def main():
    up = host_up()
    stored, sha = _read_state()
    if up:
        if stored != 0:
            _write_state(0, sha)
            print("[watchdog] SSH OK: fail count reset to 0")
        else:
            print("[watchdog] OK: SSH banner received from %s:%s" % (HOST, PORT))
        return 0
    count = stored + 1
    print("[watchdog] UNREACHABLE: consecutive failures = %d (restart threshold=%d)" % (count, RESTART_AFTER))
    if count < RESTART_AFTER:
        _write_state(count, sha)
        return 0
    try:
        status = reboot_instance()
        print("[watchdog] OCI instance RESET initiated (status=%s)" % status)
        _write_state(0, sha)
    except Exception as e:
        print("[watchdog] REBOOT FAILED: %s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())