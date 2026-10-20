"""Static package checks for the initial offline Docker LAB preview.

No Docker daemon, network or host service is touched by these tests.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "lab"


class DockerLabPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
        cls.dockerfile = (DEPLOY / "Dockerfile.preview").read_text(encoding="utf-8")
        cls.ignore = (DEPLOY / "Dockerfile.preview.dockerignore").read_text(
            encoding="utf-8"
        )
        cls.preflight = (DEPLOY / "preflight-macos.sh").read_text(encoding="utf-8")
        cls.runbook = (DEPLOY / "README.md").read_text(encoding="utf-8")

    def test_compose_is_distinct_resource_limited_and_not_autostart(self):
        for directive in (
            "name: horizont-lab",
            'restart: "no"',
            "read_only: true",
            "cap_drop:",
            "no-new-privileges:true",
            "pids_limit: 64",
            "mem_limit: 128m",
            "cpus: 0.5",
            "driver: bridge",
            "internal: false",
            "healthcheck:",
        ):
            self.assertIn(directive, self.compose)
        self.assertNotIn("privileged: true", self.compose)
        self.assertIn("-p horizont-lab", self.preflight)
        self.assertIn("--env-file /dev/null", self.preflight)
        self.assertIn("docker context inspect", self.preflight)
        self.assertIn("-p horizont-lab", self.runbook)
        self.assertIn("--env-file /dev/null", self.runbook)
        self.assertNotIn("internal: true", self.compose)
        self.assertNotIn("network_mode: host", self.compose)
        self.assertNotIn("docker.sock", self.compose)
        self.assertNotIn("volumes:", self.compose)

    def test_only_loopback_port_is_published(self):
        ports = re.findall(r'^\s+-\s+"([^"]+:8080)"', self.compose, re.MULTILINE)
        self.assertEqual(ports, ["127.0.0.1:${HORIZONT_LAB_PORT:-15480}:8080"])
        self.assertNotIn("0.0.0.0:15480", self.compose)

    def test_image_runs_as_nonroot_and_copies_only_preview(self):
        self.assertIn("USER 10001:10001", self.dockerfile)
        self.assertIn("COPY --chown=10001:10001 preview/ ./", self.dockerfile)
        self.assertIn('CMD ["python", "-m", "http.server"', self.dockerfile)
        self.assertIn("**", self.ignore)
        for path in ("index.html", "app.js", "styles.css"):
            self.assertTrue((ROOT / "preview" / path).exists())
            self.assertIn("!preview/" + path, self.ignore)
        self.assertNotIn("!.env", self.ignore)
        self.assertNotIn("!**", self.ignore)

    def test_preflight_is_read_only_and_not_executing_containers(self):
        self.assertIn('if [ "$(uname -s)" != "Darwin" ]', self.preflight)
        self.assertIn("docker compose --env-file /dev/null -p horizont-lab -f deploy/lab/compose.yaml config --quiet", self.preflight)
        self.assertIn("lsof -nP -iTCP:", self.preflight)
        for dangerous in (
            "docker system prune",
            "docker volume prune",
            "docker compose up",
            "docker compose down",
            "sudo ",
            "rm -rf",
            "killall ",
        ):
            self.assertNotIn(dangerous, self.preflight)

    def test_runbook_requires_preflight_and_has_scoped_rollback(self):
        self.assertIn("sh deploy/lab/preflight-macos.sh", self.runbook)
        self.assertIn("docker compose --env-file /dev/null -p horizont-lab -f deploy/lab/compose.yaml up -d --build preview", self.runbook)
        self.assertIn("docker compose --env-file /dev/null -p horizont-lab -f deploy/lab/compose.yaml down", self.runbook)
        self.assertIn("synthetic", self.runbook)
        self.assertIn("No owner device", self.runbook)


if __name__ == "__main__":
    unittest.main()
