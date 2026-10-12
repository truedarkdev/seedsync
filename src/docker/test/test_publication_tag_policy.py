"""No-network contract checks of the actual Make publication recipes."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
GUARD = ROOT / "src/docker/publication_tag_policy.sh"
DIGEST = "sha256:" + "a" * 64


class PublicationPolicyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="seedsync-tag-policy-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.log = self.directory / "docker.jsonl"
        self.stub = self.directory / "docker"
        self.stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "with open(os.environ['DOCKER_STUB_LOG'], 'a') as log:\n"
            "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if sys.argv[1:4] == ['buildx', 'imagetools', 'inspect']:\n"
            "    print(json.dumps(os.environ['DOCKER_STUB_DIGEST']))\n",
            encoding="utf-8",
        )
        self.stub.chmod(0o755)
        self.env = dict(os.environ, DOCKER_STUB_LOG=str(self.log),
                        DOCKER_STUB_DIGEST=DIGEST, TERM="dumb")

    def make(self, target, **variables):
        result = subprocess.run(
            ["make", "--no-print-directory", target, f"DOCKER={self.stub}",
             "red=", "green=", "reset=", *[f"{k}={v}" for k, v in variables.items()]],
            cwd=ROOT, env=self.env, text=True, capture_output=True,
        )
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return result, calls

    def release(self, **overrides):
        values = dict(STAGING_REGISTRY="ghcr.io/example", STAGING_VERSION="tested",
                      STAGING_DIGEST=DIGEST, RELEASE_REGISTRY="docker.io/example",
                      RELEASE_VERSION="1.2.3")
        values.update(overrides)
        return self.make("docker-image-release", **values)

    def test_forbidden_release_never_invokes_docker(self):
        for tag in ["debugging-extra", "cache", "v1.2.3", "1.2.3-rc1", "latest", "debugging"]:
            with self.subTest(tag=tag):
                result, calls = self.release(RELEASE_VERSION=tag)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [], result.stdout + result.stderr)

    def test_hub_staging_never_invokes_privileged_prerequisite(self):
        for prefix in ["example", "docker.io/example", "index.docker.io/example",
                       "registry-1.docker.io/example", "docker.io",
                       "docker.io:443/example"]:
            with self.subTest(prefix=prefix):
                result, calls = self.make("docker-image", STAGING_REGISTRY=prefix,
                                          STAGING_VERSION="debugging")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [], result.stdout + result.stderr)

    def test_non_hub_staging_retains_push_and_cache(self):
        for prefix in ["ghcr.io/example", "localhost:5000", "registry:5000"]:
            with self.subTest(prefix=prefix):
                self.log.unlink(missing_ok=True)
                result, calls = self.make("docker-image", STAGING_REGISTRY=prefix,
                                          STAGING_VERSION="test-build")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(calls[0][:3], ["run", "--rm", "--privileged"])
                self.assertIn("--push", calls[1])
                self.assertIn(f"--cache-to=type=registry,ref={prefix}/seedsync:cache,mode=max", calls[1])

    def test_numeric_release_retains_digest_and_latest(self):
        result, calls = self.release()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls[1], ["buildx", "imagetools", "create", "--tag",
                                  "docker.io/example/seedsync:1.2.3", "--tag",
                                  "docker.io/example/seedsync:latest",
                                  f"ghcr.io/example/seedsync@{DIGEST}"])
        self.assertEqual(len(calls), 4)

    def test_hub_source_reads_are_permitted(self):
        result, calls = self.release(STAGING_REGISTRY="docker.io/example",
                                     STAGING_VERSION="historical-source")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls[1][-1], f"docker.io/example/seedsync@{DIGEST}")

    def test_digest_mismatch_cannot_publish(self):
        self.env["DOCKER_STUB_DIGEST"] = "sha256:" + "b" * 64
        result, calls = self.release()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:3], ["buildx", "imagetools", "inspect"])

    def test_shell_metacharacters_are_data(self):
        sentinel = self.directory / "injected"
        result, calls = self.release(RELEASE_VERSION=f'1.2.3"; touch {sentinel}; #')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(sentinel.exists())
        self.assertEqual(calls, [])

    def test_dry_run_does_not_execute_docker(self):
        result = subprocess.run(
            ["make", "-n", "docker-image", f"DOCKER={self.stub}",
             "STAGING_REGISTRY=ghcr.io/example", "red=", "green=", "reset="],
            cwd=ROOT, env=self.env, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.log.exists())

    def test_allowlist_and_registry_aliases(self):
        for prefix in ["example", "docker.io/example", "index.docker.io/example",
                       "registry-1.docker.io/example"]:
            for tag in ["latest", "debugging", "01.2.3", "1.2.3"]:
                result = subprocess.run(["bash", str(GUARD), "tag", prefix, tag], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            for tag in ["debugging-extra", "cache", "v1.2.3", "1.2", "1.2.3-rc1", ""]:
                result = subprocess.run(["bash", str(GUARD), "tag", prefix, tag], capture_output=True)
                self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
