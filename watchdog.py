#!/usr/bin/env python3
import json
import os
import socket
import sys
import urllib.error
import urllib.request

HOST = os.environ.get("OCI_HOST", "193.123.162.8")
PORT = int(os.environ.get("PORT", "22"))
VAR_NAME = "FAIL_COUNT"
RESTART_AFTER = int(os.environ.get("RESTART_AFTER", "3"))
REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]


def _req(url, method="GET", value=None):
    req = urllib.request.Request("https://api.github.com" + url, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    body = None
    if value is not None:
        req.add_header("Content-Type", "application/json")
        body = json.dumps(value).encode()
    with urllib.request.urlopen(req, data=body, timeout=30) as r:
        return r.status, r.read()


def get_fail_count():
    try:
        status, data = _req("/repos/%s/actions/variables/%s" % (REPO, VAR_NAME))
        return int(json.loads(data)["value"])
    except urllib.error.HTTPError:
        return 0
    except Exception:
        return 0


def set_fail_count(n):
    try:
        _req(
            "/repos/%s/actions/variables/%s" % (REPO, VAR_NAME),
            "PATCH",
            {"name": VAR_NAME, "value": str(n)},
        )
    except urllib.error.HTTPError as e:
        if e.code == 404:
            try:
                _req("/repos/%s/actions/variables" % REPO, "POST", {"name": VAR_NAME, "value": str(n)})
            except Exception as ex:
                print("[watchdog] variable create failed: %s" % ex)
        else:
            print("[watchdog] variable update failed: %s" % e)


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
    if host_up():
        set_fail_count(0)
        print("[watchdog] OK: SSH banner received from %s:%s (fail count reset)" % (HOST, PORT))
        return 0
    count = get_fail_count() + 1
    print("[watchdog] UNREACHABLE: consecutive failures = %d (restart threshold=%d)" % (count, RESTART_AFTER))
    if count < RESTART_AFTER:
        set_fail_count(count)
        return 0
    try:
        status = reboot_instance()
        print("[watchdog] OCI instance RESET initiated (status=%s)" % status)
        set_fail_count(0)
    except Exception as e:
        print("[watchdog] REBOOT FAILED: %s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())