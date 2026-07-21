#!/usr/bin/env python3
"""Bounded API-driven observations of real historical lftp/extractor states."""

import argparse
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from fixture import build_fixture_evidence, load_manifest


def request(base, path, timeout=5):
    with urlopen(Request(base.rstrip("/") + path), timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def model(base, timeout=5):
    with urlopen(base.rstrip("/") + "/server/stream", timeout=timeout) as response:
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
    started_at = time.time()
    deadline = time.monotonic() + timeout
    states = []
    first_seen = {}
    current_state = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return observation_result(target, False, timeout, started_at, states, first_seen, current_state)
        if remaining <= 0.001:
            time.sleep(remaining)
            continue
        try:
            item = next((entry for entry in model(base, timeout=min(5, remaining)) if entry.get("name") == name), None)
            completed_at = time.monotonic()
            if completed_at >= deadline:
                return observation_result(target, False, timeout, started_at, states, first_seen, current_state)
            state = item.get("state") if item else None
            current_state = state
            if state and state not in states:
                states.append(state)
                first_seen[state] = time.time()
            if state == target:
                return observation_result(target, True, timeout, started_at, states, first_seen, current_state, time.time())
        except (HTTPError, URLError, TimeoutError, RuntimeError, json.JSONDecodeError):
            if time.monotonic() >= deadline:
                return observation_result(target, False, timeout, started_at, states, first_seen, current_state)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.5, max(0.001, remaining / 2)))


def observation_result(target, observed, timeout, started_at, states, first_seen, current_state, finished_at=None):
    return {
        "target": target,
        "observed": observed,
        "timeout_seconds": timeout,
        "started_at": started_at,
        "finished_at": time.time() if finished_at is None else finished_at,
        "current_state": current_state,
        "states": states,
        "first_seen": first_seen,
    }


def observe_many(base, targets):
    """Observe several names from the same model snapshots.

    Keeping the names in one polling loop avoids attributing a state observed
    for one transfer to another transfer that may have completed already.
    """
    if len({name for name, _target, _timeout in targets}) != len(targets):
        raise ValueError("observe_many target names must be unique")
    started_monotonic = time.monotonic()
    started_at = time.time()
    deadlines = {name: started_monotonic + timeout for name, _target, timeout in targets}
    states = {name: [] for name, _target, _timeout in targets}
    first_seen = {name: {} for name, _target, _timeout in targets}
    current_state = {name: None for name, _target, _timeout in targets}
    target_by_name = {name: target for name, target, _timeout in targets}
    timeout_by_name = {name: timeout for name, _target, timeout in targets}
    active = set(target_by_name)
    results = {}
    while active:
        now = time.monotonic()
        for name in list(active):
            if now >= deadlines[name]:
                results[name] = observation_result(
                    target_by_name[name], False, timeout_by_name[name], started_at,
                    states[name], first_seen[name], current_state[name], time.time(),
                )
                active.remove(name)
        if not active:
            break
        nearest_remaining = min(deadlines[name] - time.monotonic() for name in active)
        if nearest_remaining <= 0:
            continue
        if nearest_remaining <= 0.001:
            time.sleep(nearest_remaining)
            continue
        try:
            items = {entry.get("name"): entry for entry in model(base, timeout=min(5, nearest_remaining))}
            completed_monotonic = time.monotonic()
            completed_wall = time.time()
            for name in list(active):
                if completed_monotonic >= deadlines[name]:
                    continue
                target = target_by_name[name]
                state = (items.get(name) or {}).get("state")
                current_state[name] = state
                if state and state not in states[name]:
                    states[name].append(state)
                    first_seen[name][state] = completed_wall
                if state == target:
                    results[name] = observation_result(
                        target, True, timeout_by_name[name], started_at,
                        states[name], first_seen[name], current_state[name], completed_wall,
                    )
                    active.remove(name)
        except (HTTPError, URLError, TimeoutError, RuntimeError, json.JSONDecodeError):
            pass
        now = time.monotonic()
        for name in list(active):
            if now >= deadlines[name]:
                results[name] = observation_result(
                    target_by_name[name], False, timeout_by_name[name], started_at,
                    states[name], first_seen[name], current_state[name], time.time(),
                )
                active.remove(name)
        remaining = [deadlines[name] - time.monotonic() for name in active]
        if remaining:
            nearest_remaining = min(remaining)
            time.sleep(min(0.5, max(0.001, nearest_remaining / 2)))
    return {
        name: results[name]
        for name in target_by_name
    }


def action(base, verb, name):
    return request(base, "/server/command/{}/{}".format(verb, quote(name, safe="")))


def load_probe_contract(manifest_path, evidence_path):
    """Rebuild and exactly compare the trusted fixture evidence before actions."""
    manifest = load_manifest(manifest_path)
    expected = build_fixture_evidence(manifest)
    actual = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    if actual != expected:
        raise ValueError("fixture evidence does not exactly match the validated pinned manifest")
    return expected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--fixture-evidence", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    fixture_evidence = load_probe_contract(args.manifest, args.fixture_evidence)
    probes = {probe["target"]: probe for probe in fixture_evidence["transient_probes"]}
    queued_probe = probes.get("queued", {"case_id": "transient-manual", "timeout_seconds": 45})
    downloading_probe = probes.get("downloading", {"case_id": "transient-large", "timeout_seconds": 45})
    extracting_probe = probes.get("extracting", queued_probe)
    by_case = {item["case_id"]: item["name"] for item in fixture_evidence["case_index"]}
    queued_name = by_case.get(queued_probe["case_id"], "transient-manual.zip")
    downloading_name = by_case.get(downloading_probe["case_id"], "transient-large.bin")
    extracting_name = by_case.get(extracting_probe["case_id"], queued_name)
    evidence = {
        "observation_timeouts_seconds": {
            target: probes[target]["timeout_seconds"] for target in ("queued", "downloading", "extracting")
        },
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
        action(args.base_url, "queue", downloading_name)
        evidence["actions"].append({"verb": "queue", "name": downloading_name, "result": "accepted", "timestamp": time.time()})
    except Exception as exc:
        evidence["actions"].append({"verb": "queue", "name": downloading_name, "result": "failed", "error": type(exc).__name__, "timestamp": time.time()})
    try:
        action(args.base_url, "queue", queued_name)
        evidence["actions"].append({"verb": "queue", "name": queued_name, "result": "accepted", "timestamp": time.time()})
    except Exception as exc:
        evidence["actions"].append({"verb": "queue", "name": queued_name, "result": "failed", "error": type(exc).__name__, "timestamp": time.time()})
    concurrent = observe_many(
        args.base_url,
        [
            (queued_name, "queued", queued_probe["timeout_seconds"]),
            (downloading_name, "downloading", downloading_probe["timeout_seconds"]),
        ],
    )
    evidence["observations"]["queued"] = concurrent[queued_name]
    evidence["observations"]["downloading"] = concurrent[downloading_name]
    downloaded = observe(args.base_url, downloading_name, "downloaded", downloading_probe["timeout_seconds"])
    evidence["observations"]["large_downloaded"] = downloaded
    if not downloaded["observed"]:
        evidence["limitations"].append("{} did not settle to downloaded within the bounded probe".format(downloading_name))
    evidence["observations"]["extract_downloaded"] = observe(args.base_url, extracting_name, "downloaded", extracting_probe["timeout_seconds"])
    if evidence["observations"]["extract_downloaded"]["observed"]:
        try:
            action(args.base_url, "extract", extracting_name)
            evidence["actions"].append({"verb": "extract", "name": extracting_name, "result": "accepted", "timestamp": time.time()})
        except Exception as exc:
            evidence["actions"].append({"verb": "extract", "name": extracting_name, "result": "failed", "error": type(exc).__name__, "timestamp": time.time()})
        evidence["observations"]["extracting"] = observe(args.base_url, extracting_name, "extracting", extracting_probe["timeout_seconds"])
    else:
        evidence["limitations"].append("{} was not downloaded, so extracting was not exercised".format(extracting_name))
    for key in ("queued", "downloading", "extracting"):
        if key in evidence["observations"] and not evidence["observations"][key]["observed"]:
            evidence["limitations"].append("{} was not observed within the bounded poll".format(key))
    Path(args.evidence).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
