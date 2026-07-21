#!/usr/bin/env python3
"""Materialize and validate the pinned v0.8.6 synthetic fixture manifest."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import unicodedata
import zipfile


HISTORICAL_COMMIT = "ff2a1039935beccbbf7ec76134b41d2e91137742"
BACKEND_STATES = {"default", "downloading", "queued", "downloaded", "deleted", "extracting", "extracted"}
UI_STATES = BACKEND_STATES | {"stopped"}
AUTOQUEUE_EXPECTATIONS = {"unmatched", "substring", "glob", "case-insensitive", "auto-extract", "nonarchive"}
TOPOLOGIES = {"root-file", "root-directory", "nested-directory", "child-file"}
STABLE_UI_STATES = ["default", "stopped", "downloaded", "deleted", "extracted"]
TRANSIENT_UI_STATES = ["queued", "downloading", "extracting"]
MAX_CASES = 64
MAX_ENTRIES = 512
MAX_TOTAL_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_TEXT_BYTES = 1 * 1024 * 1024
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *("COM{}".format(i) for i in range(1, 10)), *("LPT{}".format(i) for i in range(1, 10))}


def safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("unsafe fixture path: {}".format(value))
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("fixture path contains a control character: {}".format(value))
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError("fixture path is not NFC-normalized: {}".format(value))
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("unsafe fixture path: {}".format(value))
    for component in path.parts:
        if component in {"", ".", ".."} or component.endswith((".", " ")) or ":" in component:
            raise ValueError("ambiguous fixture path component: {}".format(component))
        if component.split(".", 1)[0].upper() in WINDOWS_RESERVED:
            raise ValueError("Windows-reserved fixture path component: {}".format(component))
    return path


def source_stats(source, relative, seen, counters):
    """Validate and count every generated destination/member in a source."""
    if not isinstance(source, dict):
        raise ValueError("fixture source must be an object")
    if "archive" in source:
        outer = safe_relative(relative)
        register_path("archive", outer, seen, counters)
        members = source["archive"]
        if not isinstance(members, dict) or not members:
            raise ValueError("archive must contain at least one member")
        for member, content in members.items():
            member_path = safe_relative(member)
            register_path("archive-member", member_path, seen, counters)
            if isinstance(content, dict) and "generated_bytes" in content:
                size = bounded_generated(content["generated_bytes"])
                counters["generated"] += size
                counters["total"] += size
            else:
                payload = str(content).encode("utf-8")
                counters["text"] += len(payload)
                counters["total"] += len(payload)
            counters["entries"] += 1
        return
    if "directory" in source:
        safe_relative(relative)
        for child, content in source["directory"].items():
            child_path = str(PurePosixPath(relative) / safe_relative(child))
            if isinstance(content, dict) and set(content) == {"directory"}:
                source_stats(content, child_path, seen, counters)
            else:
                register_path("directory-child", safe_relative(child_path), seen, counters)
                if isinstance(content, dict) and "generated_bytes" in content:
                    size = bounded_generated(content["generated_bytes"])
                    counters["generated"] += size
                    counters["total"] += size
                else:
                    payload = str(content).encode("utf-8")
                    counters["text"] += len(payload)
                    counters["total"] += len(payload)
                counters["entries"] += 1
        return
    safe_relative(relative)
    register_path("file", safe_relative(relative), seen, counters)
    if "generated_bytes" in source:
        size = bounded_generated(source["generated_bytes"])
        counters["generated"] += size
        counters["total"] += size
    else:
        payload = str(source.get("content", "")).encode("utf-8")
        counters["text"] += len(payload)
        counters["total"] += len(payload)
    counters["entries"] += 1


def bounded_generated(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 16 * 1024 * 1024:
        raise ValueError("generated fixture size must be an integer between 1 and 16 MiB")
    return value


def _resolved_source(case, key):
    source = case.get(key)
    if isinstance(source, dict) and source.get("same_as_remote"):
        return case.get("remote")
    return source


def _directory_shape(source):
    """Return whether a directory source contains nested paths and file entries."""
    if not isinstance(source, dict) or "directory" not in source:
        return False, False
    nested = False
    child_file = False

    def walk(mapping, depth):
        nonlocal nested, child_file
        if not isinstance(mapping, dict):
            return
        for relative, content in mapping.items():
            parts = PurePosixPath(relative).parts
            below_root = depth > 0 or len(parts) > 1
            nested = nested or below_root
            if isinstance(content, dict) and set(content) == {"directory"}:
                walk(content["directory"], depth + len(parts))
            else:
                child_file = True

    walk(source["directory"], 0)
    return nested, child_file


def derive_topologies(case):
    """Derive the exact topology represented by a case's source shapes."""
    sources = [_resolved_source(case, "remote"), _resolved_source(case, "local")]
    directory_sources = [source for source in sources if isinstance(source, dict) and "directory" in source]
    if not directory_sources:
        if not any(source is not None for source in sources):
            raise ValueError("{} has no remote or local source to derive topology".format(case.get("id")))
        return {"root-file"}
    topologies = {"root-directory"}
    shapes = [_directory_shape(source) for source in directory_sources]
    if any(shape[0] for shape in shapes):
        topologies.add("nested-directory")
    if any(shape[1] for shape in shapes):
        topologies.add("child-file")
    return topologies


def register_path(kind, path, seen, counters):
    key = unicodedata.normalize("NFC", str(path)).casefold()
    if key in seen:
        raise ValueError("case-fold fixture path collision: {} and {}".format(seen[key], path))
    seen[key] = "{}:{}".format(kind, path)


def load_manifest(path):
    with open(path, encoding="utf-8") as stream:
        manifest = json.load(stream)
    if manifest.get("schema_version") != 1:
        raise ValueError("fixture manifest schema_version must be 1")
    if manifest.get("historical_commit") != HISTORICAL_COMMIT:
        raise ValueError("fixture manifest historical_commit is not the pinned v0.8.6 commit")
    contract = manifest.get("historical_contract", {})
    if contract.get("model_states") != ["default", "downloading", "queued", "downloaded", "deleted", "extracting", "extracted"]:
        raise ValueError("historical model state contract does not match ff2a")
    if contract.get("ui_synthetic_status") != "stopped":
        raise ValueError("historical UI synthetic status contract does not match ff2a")
    if contract.get("controller_persist_keys") != ["downloaded", "extracted"]:
        raise ValueError("historical controller.persist contract does not match ff2a")
    if contract.get("autoqueue_persist_key") != "patterns":
        raise ValueError("historical AutoQueue persist contract does not match ff2a")
    if contract.get("autoqueue_config_keys") != ["enabled", "patterns_only", "auto_extract"]:
        raise ValueError("historical AutoQueue config contract does not match ff2a")
    config = manifest.get("config", {}).get("autoqueue", {})
    if set(config) != {"enabled", "patterns_only", "auto_extract", "patterns"}:
        raise ValueError("fixture manifest autoqueue config must contain only historical enabled/patterns_only/auto_extract/patterns")
    if not all(isinstance(config[key], bool) for key in ("enabled", "patterns_only", "auto_extract")):
        raise ValueError("fixture manifest autoqueue booleans are invalid")
    if not isinstance(config["patterns"], list) or not all(isinstance(item, str) and item.strip() for item in config["patterns"]):
        raise ValueError("fixture manifest AutoQueue patterns must be non-empty strings")
    cases = manifest.get("cases", [])
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError("fixture case count must be between 1 and {}".format(MAX_CASES))
    names = set()
    ids = set()
    cases_by_id = {}
    counters = {"entries": 0, "generated": 0, "total": 0, "text": 0}
    remote_seen = {}
    local_seen = {}
    for case in cases:
        case_id = case.get("id")
        name = case.get("name")
        expected = case.get("expected", {})
        if not case_id or case_id in ids:
            raise ValueError("fixture case IDs must be unique and non-empty")
        if not name or name in names:
            raise ValueError("fixture root names must be unique and non-empty")
        safe_relative(name)
        ids.add(case_id)
        names.add(name)
        cases_by_id[case_id] = case
        if expected.get("backend_state") not in BACKEND_STATES:
            raise ValueError("{} uses an unknown historical backend state".format(case_id))
        if expected.get("ui_status") not in UI_STATES:
            raise ValueError("{} uses an unknown historical UI status".format(case_id))
        if expected.get("autoqueue") not in AUTOQUEUE_EXPECTATIONS:
            raise ValueError("{} uses an unknown AutoQueue expectation".format(case_id))
        if not expected.get("migration_invariant"):
            raise ValueError("{} is missing a migration invariant".format(case_id))
        if "topologies" not in case:
            raise ValueError("{} is missing an exact topology declaration".format(case_id))
        if (
            not isinstance(case["topologies"], list)
            or not case["topologies"]
            or not set(case["topologies"]) <= TOPOLOGIES
        ):
            raise ValueError("{} uses malformed topology declarations".format(case_id))
        derived_topologies = derive_topologies(case)
        if set(case["topologies"]) != derived_topologies:
            raise ValueError(
                "{} topology declaration differs from fixture sources: expected {} got {}".format(
                    case_id, sorted(derived_topologies), sorted(case["topologies"])
                )
            )
        markers = case.get("markers", {})
        if set(markers) - {"downloaded", "extracted"}:
            raise ValueError("{} uses unknown controller.persist markers".format(case_id))
        if markers.get("extracted") and not markers.get("downloaded"):
            raise ValueError("{} cannot be extracted without the historical downloaded marker".format(case_id))
        for source_key in ("remote", "local"):
            source = case.get(source_key)
            if source and set(source) - {"content", "same_as_remote", "archive", "directory", "generated_bytes"}:
                raise ValueError("{} uses unknown {} fixture fields".format(case_id, source_key))
            if source and sum(key in source for key in ("content", "same_as_remote", "archive", "directory", "generated_bytes")) != 1:
                raise ValueError("{} must select exactly one {} fixture source".format(case_id, source_key))
        if case.get("transient"):
            remote = case.get("remote", {})
            has_bounded_payload = bool(remote.get("generated_bytes")) or any(
                isinstance(value, dict) and value.get("generated_bytes")
                for value in remote.get("archive", {}).values()
            )
            if not has_bounded_payload:
                raise ValueError("transient case {} must use a bounded generated_bytes payload".format(case_id))
        for source_key in ("remote", "local"):
            source = case.get(source_key)
            if source:
                source_stats(source, name, remote_seen if source_key == "remote" else local_seen, counters)
    if counters["entries"] > MAX_ENTRIES:
        raise ValueError("fixture entry count exceeds {}".format(MAX_ENTRIES))
    if counters["total"] > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise ValueError("fixture uncompressed payload exceeds {} bytes".format(MAX_TOTAL_UNCOMPRESSED_BYTES))
    if counters["text"] > MAX_TEXT_BYTES:
        raise ValueError("fixture text payload exceeds {} bytes".format(MAX_TEXT_BYTES))
    excluded = manifest.get("excluded")
    if excluded != [".lftp-pget-status"]:
        raise ValueError("fixture must explicitly exclude the historical .lftp-pget-status scanner artifact")
    generated_roots = manifest.get("generated_roots", [])
    if not isinstance(generated_roots, list) or not all(isinstance(item, str) and item for item in generated_roots):
        raise ValueError("generated_roots must be a list of names")
    validate_evidence_contract(manifest.get("evidence_contract"), cases_by_id)
    return manifest


def validate_evidence_contract(contract, cases_by_id):
    """Validate the explicit stable/transient evidence matrix.

    This contract is intentionally separate from fixture expectations so that
    transient observations remain best-effort and cannot be mistaken for
    deterministic migration or screenshot oracles.
    """
    if not isinstance(contract, dict):
        raise ValueError("manifest evidence_contract must be an object")
    required = {"states", "stable_coverage", "transient_probes", "autoqueue", "exclusions"}
    if set(contract) != required:
        raise ValueError("manifest evidence_contract keys differ: expected {}".format(sorted(required)))
    states = contract["states"]
    if not isinstance(states, dict) or set(states) != {"backend", "ui_derived", "stable", "transient"}:
        raise ValueError("evidence state declarations are malformed")
    if states["backend"] != ["default", "downloading", "queued", "downloaded", "deleted", "extracting", "extracted"]:
        raise ValueError("evidence backend states do not match the pinned historical contract")
    if states["ui_derived"] != ["stopped"] or states["stable"] != STABLE_UI_STATES or states["transient"] != TRANSIENT_UI_STATES:
        raise ValueError("evidence stable/transient UI state declarations are malformed")

    coverage = contract["stable_coverage"]
    if not isinstance(coverage, list) or not coverage:
        raise ValueError("evidence stable_coverage must be a non-empty list")
    covered_states = set()
    covered_topologies = set()
    covered_case_ids = set()
    for entry in coverage:
        if not isinstance(entry, dict) or set(entry) != {"case_id", "ui_status", "topologies"}:
            raise ValueError("stable coverage entries must contain case_id, ui_status, and topologies")
        case = cases_by_id.get(entry["case_id"])
        if case is None or case.get("transient"):
            raise ValueError("stable coverage references an unknown or transient case: {}".format(entry.get("case_id")))
        if entry["case_id"] in covered_case_ids:
            raise ValueError("stable coverage case IDs must be unique: {}".format(entry["case_id"]))
        if entry["ui_status"] not in STABLE_UI_STATES or case["expected"]["ui_status"] != entry["ui_status"]:
            raise ValueError("stable coverage state disagrees with case {}".format(entry["case_id"]))
        topologies = entry["topologies"]
        if not isinstance(topologies, list) or not topologies or not set(topologies) <= TOPOLOGIES:
            raise ValueError("stable coverage topologies are malformed for {}".format(entry["case_id"]))
        derived_topologies = derive_topologies(case)
        if set(topologies) != derived_topologies:
            raise ValueError("stable coverage topologies differ from fixture sources for {}".format(entry["case_id"]))
        covered_states.add(entry["ui_status"])
        covered_topologies.update(topologies)
        covered_case_ids.add(entry["case_id"])
    if covered_states != set(STABLE_UI_STATES):
        raise ValueError("stable coverage must include every stable UI state")
    if covered_topologies != TOPOLOGIES:
        raise ValueError("stable coverage must include root-file, root-directory, nested-directory, and child-file")

    probes = contract["transient_probes"]
    if not isinstance(probes, list) or {item.get("target") for item in probes if isinstance(item, dict)} != set(TRANSIENT_UI_STATES):
        raise ValueError("transient probes must cover queued, downloading, and extracting")
    seen_targets = set()
    for probe in probes:
        if not isinstance(probe, dict) or set(probe) != {"case_id", "target", "timeout_seconds"}:
            raise ValueError("transient probe entries are malformed")
        if probe["target"] in seen_targets or probe["target"] not in TRANSIENT_UI_STATES:
            raise ValueError("transient probe targets must be unique historical transient states")
        case = cases_by_id.get(probe["case_id"])
        if case is None or not case.get("transient") or case["expected"]["backend_state"] != "default":
            raise ValueError("transient probe references an invalid case: {}".format(probe["case_id"]))
        if isinstance(probe["timeout_seconds"], bool) or not isinstance(probe["timeout_seconds"], int) or not 1 <= probe["timeout_seconds"] <= 300:
            raise ValueError("transient probe timeout must be between 1 and 300 seconds")
        seen_targets.add(probe["target"])

    autoqueue = contract["autoqueue"]
    if not isinstance(autoqueue, dict) or set(autoqueue) != {"rules", "positive", "negative", "out_of_run"}:
        raise ValueError("evidence AutoQueue mapping is malformed")
    if autoqueue["rules"] != {
        "substring": "case-insensitive substring",
        "glob": "case-insensitive fnmatch wildcard",
    }:
        raise ValueError("evidence AutoQueue matching rules are malformed")
    positive = autoqueue["positive"]
    if not isinstance(positive, list) or not positive:
        raise ValueError("evidence AutoQueue positive mapping must be non-empty")
    positive_ids = set()
    for entry in positive:
        if not isinstance(entry, dict) or set(entry) != {"case_id", "match"}:
            raise ValueError("evidence AutoQueue positive entries are malformed")
        case = cases_by_id.get(entry["case_id"])
        if case is None or case["expected"]["autoqueue"] != entry["match"] or entry["case_id"] in positive_ids:
            raise ValueError("evidence AutoQueue positive mapping disagrees with {}".format(entry.get("case_id")))
        positive_ids.add(entry["case_id"])
    negative = autoqueue["negative"]
    if not isinstance(negative, list) or not negative:
        raise ValueError("evidence AutoQueue negative mapping must be non-empty")
    negative_ids = set()
    for entry in negative:
        if not isinstance(entry, dict) or set(entry) != {"case_id", "reason"}:
            raise ValueError("evidence AutoQueue negative entries are malformed")
        case = cases_by_id.get(entry["case_id"])
        if case is None or case["expected"]["autoqueue"] != "unmatched" or entry["case_id"] in negative_ids or not entry["reason"]:
            raise ValueError("evidence AutoQueue negative mapping disagrees with {}".format(entry.get("case_id")))
        negative_ids.add(entry["case_id"])
    if not isinstance(autoqueue["out_of_run"], list) or not all(isinstance(item, str) and item for item in autoqueue["out_of_run"]):
        raise ValueError("evidence AutoQueue out_of_run declarations are malformed")
    if positive_ids & negative_ids:
        raise ValueError("evidence AutoQueue positive and negative mappings overlap")

    exclusions = contract["exclusions"]
    if not isinstance(exclusions, list):
        raise ValueError("evidence exclusions must be a list")
    exclusion_ids = {item.get("id") for item in exclusions if isinstance(item, dict)}
    if exclusion_ids != {"same-directory-duplicate-identity", "timing-dependent-transient-stable-oracles", "directory-extracted-stable-case"}:
        raise ValueError("evidence exclusions must document duplicate identity, transient timing, and directory extraction")
    if any(not isinstance(item, dict) or set(item) != {"id", "reason"} or not item["reason"] for item in exclusions):
        raise ValueError("evidence exclusion entries are malformed")


def write_bytes(root, relative, data):
    relative = safe_relative(relative)
    root = Path(root).resolve()
    target = root.joinpath(*relative.parts)
    if os.path.commonpath((str(root), str(target.resolve(strict=False)))) != str(root):
        raise ValueError("fixture destination escaped root: {}".format(relative))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def source_bytes(source):
    if "content" in source:
        return source["content"].encode("utf-8")
    if "generated_bytes" in source:
        size = bounded_generated(source["generated_bytes"])
        return (b"seedsync-v086-transient-" * ((size // 24) + 1))[:size]
    raise ValueError("source is not a regular file")


def archive_bytes(files):
    import io
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for relative, content in files.items():
            safe_relative(relative)
            if isinstance(content, dict) and "generated_bytes" in content:
                payload = source_bytes(content)
            else:
                payload = str(content).encode("utf-8")
            archive.writestr(relative, payload)
    return output.getvalue()


def materialize_source(root, relative, source):
    if "archive" in source:
        write_bytes(root, relative, archive_bytes(source["archive"]))
        return
    if "directory" in source:
        safe_relative(relative)
        for child, content in source["directory"].items():
            child_path = str(PurePosixPath(relative) / safe_relative(child))
            write_bytes(root, child_path, content.encode("utf-8"))
        return
    write_bytes(root, relative, source_bytes(source))


def materialize(manifest, run_dir):
    run_dir = Path(run_dir).resolve()
    remote_root = run_dir / "remote-files"
    local_root = run_dir / "downloads"
    for root in (remote_root, local_root):
        root.mkdir(parents=True, exist_ok=True)
        if any(root.iterdir()):
            raise ValueError("fixture roots must be empty before materialization: {}".format(root))

    downloaded = []
    extracted = []
    for case in manifest["cases"]:
        name = case["name"]
        remote = case.get("remote")
        local = case.get("local")
        if remote:
            materialize_source(remote_root, name, remote)
        if local:
            materialize_source(local_root, name, remote if local.get("same_as_remote") else local)
        markers = case.get("markers", {})
        if markers.get("downloaded"):
            downloaded.append(name)
        if markers.get("extracted"):
            extracted.append(name)

    # The lftp scanner creates/consumes this historical status artifact, but
    # scan_fs must never expose it as a user file.
    write_bytes(remote_root, ".lftp-pget-status", b"synthetic scanner control artifact")
    config_root = run_dir / "config"
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "controller.persist").write_text(
        json.dumps({"downloaded": sorted(downloaded), "extracted": sorted(extracted)}, indent=2) + "\n",
        encoding="utf-8",
    )
    patterns = [json.dumps({"pattern": pattern}, separators=(",", ":")) for pattern in manifest["config"]["autoqueue"]["patterns"]]
    (config_root / "autoqueue.persist").write_text(
        json.dumps({"patterns": patterns}, indent=2) + "\n", encoding="utf-8"
    )
    expected = {case["name"]: case["expected"] for case in manifest["cases"]}
    (run_dir / "evidence" / "fixture-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (run_dir / "evidence" / "fixture-expected.json").write_text(
        json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    (run_dir / "evidence" / "fixture-evidence.json").write_text(
        json.dumps(build_fixture_evidence(manifest), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def build_fixture_evidence(manifest):
    """Build the deterministic evidence payload from a validated manifest."""
    evidence_contract = manifest["evidence_contract"]
    case_index = []
    for case in manifest["cases"]:
        case_index.append({
            "case_id": case["id"],
            "name": case["name"],
            "topologies": sorted(derive_topologies(case)),
            "backend_state": case["expected"]["backend_state"],
            "ui_status": case["expected"]["ui_status"],
            "autoqueue": case["expected"]["autoqueue"],
        })
    fixture_evidence = {
        "schema_version": 1,
        "historical_commit": HISTORICAL_COMMIT,
        "states": evidence_contract["states"],
        "stable_coverage": evidence_contract["stable_coverage"],
        "transient_probes": evidence_contract["transient_probes"],
        "autoqueue": evidence_contract["autoqueue"],
        "exclusions": evidence_contract["exclusions"],
        "case_index": case_index,
    }
    return fixture_evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate", "materialize", "config"))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--run-dir")
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    if args.command == "materialize":
        if not args.run_dir:
            parser.error("--run-dir is required for materialize")
        materialize(manifest, args.run_dir)
    elif args.command == "config":
        config = manifest["config"]["autoqueue"]
        print("{} {} {}".format(
            str(config["enabled"]).lower(),
            str(config["patterns_only"]).lower(),
            str(config["auto_extract"]).lower(),
        ))
    else:
        print(json.dumps({"cases": len(manifest["cases"]), "sha256": hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
