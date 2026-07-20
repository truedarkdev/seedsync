#!/usr/bin/env python3
"""Bounded API-driven observations of real historical lftp/extractor states."""

import argparse
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def request(base, path):
    with urlopen(Request(base.rstrip("/") + path), timeout=5) as response:
        return response.read().decode("utf-8", errors="replace")


def model(base):
    with urlopen(base.rstrip("/") + "/server/stream", timeout=5) as response:
        payload = []
        event = None
        while True:
            line = response.readline()
            if not line:
                break
            if line.startswith(b"event:"):
                event = line.decode("utf-8").split(":", 1)[1].strip()
            if line.startswith(b"data:"):
                payload.append(line.decode("utf-8").split(":", 1)[1].strip())
            elif line in (b"\n", b"\r\n") and payload and event == "model-init":
                return json.loads("".join(payload))
            elif line in (b"\n", b"\r\n"):
                payload = []
                event = None
    raise RuntimeError("historical model stream did not provide model-init")


def observe(base, name, target, timeout):
    deadline = time.monotonic() + timeout
    states = []
    first_seen = {}
    while time.monotonic() < deadline:
        try:
            item = next((entry for entry in model(base) if entry.get("name") == name), None)
            state = item.get("state") if item else None
            if state and state not in states:
                states.append(state)
                first_seen[state] = time.time()
            if state == target:
                return {"target": target, "observed": True, "states": states, "first_seen": first_seen}
        except (HTTPError, URLError, TimeoutError, RuntimeError, json.JSONDecodeError):
            pass
        time.sleep(0.5)
    return {"target": target, "observed": False, "states": states, "first_seen": first_seen}


def observe_many(base, targets, timeout):
    """Observe several names from the same model snapshots.

    Keeping the names in one polling loop avoids attributing a state observed
    for one transfer to another transfer that may have completed already.
    """
    deadline = time.monotonic() + timeout
    states = {name: [] for name, _target in targets}
    first_seen = {name: {} for name, _target in targets}
    observed = {name: False for name, _target in targets}
    target_by_name = dict(targets)
    while time.monotonic() < deadline:
        try:
            items = {entry.get("name"): entry for entry in model(base)}
            for name, target in targets:
                state = (items.get(name) or {}).get("state")
                if state and state not in states[name]:
                    states[name].append(state)
                    first_seen[name][state] = time.time()
                if state == target:
                    observed[name] = True
            if all(observed.values()):
                break
        except (HTTPError, URLError, TimeoutError, RuntimeError, json.JSONDecodeError):
            pass
        time.sleep(0.5)
    return {
        name: {
            "target": target_by_name[name],
            "observed": observed[name],
            "states": states[name],
            "first_seen": first_seen[name],
        }
        for name in target_by_name
    }


def action(base, verb, name):
    return request(base, "/server/command/{}/{}".format(verb, quote(name, safe="")))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    evidence = {
        "bounded_seconds_per_observation": 45,
        "controls": {
            "historical_lftp_rate_key": "net:limit-rate",
            "historical_parallel_job_key": "cmd:queue-parallel",
            "historical_parallel_file_key": "mirror:parallel-transfer-count",
            "rate_limit": "256K",
            "parallel_jobs": 1,
            "parallel_files": 1,
            "connect_timeout_seconds": 3,
            "max_retries": 1,
        },
        "actions": [],
        "observations": {},
        "limitations": [],
    }
    try:
        action(args.base_url, "queue", "transient-large.bin")
        evidence["actions"].append({"verb": "queue", "name": "transient-large.bin", "result": "accepted"})
    except Exception as exc:
        evidence["actions"].append({"verb": "queue", "name": "transient-large.bin", "result": "failed", "error": type(exc).__name__})
    try:
        action(args.base_url, "queue", "transient-manual.zip")
        evidence["actions"].append({"verb": "queue", "name": "transient-manual.zip", "result": "accepted"})
    except Exception as exc:
        evidence["actions"].append({"verb": "queue", "name": "transient-manual.zip", "result": "failed", "error": type(exc).__name__})
    concurrent = observe_many(
        args.base_url,
        [("transient-manual.zip", "queued"), ("transient-large.bin", "downloading")],
        45,
    )
    evidence["observations"]["queued"] = concurrent["transient-manual.zip"]
    evidence["observations"]["downloading"] = concurrent["transient-large.bin"]
    downloaded = observe(args.base_url, "transient-large.bin", "downloaded", 45)
    evidence["observations"]["large_downloaded"] = downloaded
    if not downloaded["observed"]:
        evidence["limitations"].append("transient-large.bin did not settle to downloaded within the bounded probe")
    evidence["observations"]["extract_downloaded"] = observe(args.base_url, "transient-manual.zip", "downloaded", 45)
    if evidence["observations"]["extract_downloaded"]["observed"]:
        try:
            action(args.base_url, "extract", "transient-manual.zip")
            evidence["actions"].append({"verb": "extract", "name": "transient-manual.zip", "result": "accepted"})
        except Exception as exc:
            evidence["actions"].append({"verb": "extract", "name": "transient-manual.zip", "result": "failed", "error": type(exc).__name__})
        evidence["observations"]["extracting"] = observe(args.base_url, "transient-manual.zip", "extracting", 45)
    else:
        evidence["limitations"].append("transient-manual.zip was not downloaded, so extracting was not exercised")
    for key in ("queued", "downloading", "extracting"):
        if key in evidence["observations"] and not evidence["observations"][key]["observed"]:
            evidence["limitations"].append("{} was not observed within the bounded poll".format(key))
    Path(args.evidence).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
