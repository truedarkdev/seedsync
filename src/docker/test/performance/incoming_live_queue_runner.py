"""Externally configured, one-shot Incoming Queue caller for the maintained observer."""
from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import sys
import urllib.request
from urllib.error import HTTPError
from urllib.parse import quote

_helper_path = Path(__file__).with_name("incoming-recovery-observer.py")
if not _helper_path.is_file():
    _helper_path = Path(__file__).with_name("incoming_recovery_observer.py")
_spec = importlib.util.spec_from_file_location("incoming_recovery_observer", _helper_path)
if _spec is None or _spec.loader is None:
    raise RuntimeError("maintained observer helper is unavailable")
_observer = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _observer
_spec.loader.exec_module(_observer)
PassiveQueueCaller = _observer.PassiveQueueCaller
QueueGate = _observer.QueueGate
QueueTransportResponse = _observer.QueueTransportResponse


class HttpAdapter:
    """Keep secrets in the caller and return an explicit Queue status result."""
    def __init__(self, key_path: Path, opener=urllib.request.urlopen):
        self.key_path, self.opener = key_path, opener

    def request(self, path: str, method: str = "GET"):
        key = self.key_path.read_text(encoding="utf-8").strip()
        request = urllib.request.Request("http://127.0.0.1:8800" + path, method=method,
            headers={"Authorization": "Bearer " + key})
        try:
            with self.opener(request, timeout=35 if method == "POST" else 5) as response:
                body = response.read()
                if method == "POST":
                    return QueueTransportResponse(response.status, body)
                return json.loads(body.decode("utf-8"))
        except HTTPError as error:
            if method != "POST":
                raise
            return QueueTransportResponse(error.code, error.read())


def main(config_path: str, dry_run: bool = False) -> int:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    adapter = HttpAdapter(Path(config["key_path"]))
    gate = QueueGate(config["pair_id"], config["pair_name"], config["root_id"],
        config["root_name"], require_pending_transfer=True, artifact_path=config["artifact_path"])
    if dry_run:
        gate.preflight(adapter.request)
        _observer.FixedGetSampler(tuple(config["passive_paths"]), artifact_path=config["artifact_path"],
            continue_on_error=False).sample(adapter.request)
        return 0
    if os.environ.get("INCOMING_RECOVERY_ALLOW_QUEUE") != "1":
        raise SystemExit("Queue gate must be explicitly enabled")
    caller = PassiveQueueCaller(gate, adapter.request,
        lambda path: adapter.request(path, "POST"), tuple(config["passive_paths"]),
        config["artifact_path"], os.environ, 35)
    caller.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[-1], "--dry-run" in sys.argv[1:-1]))
