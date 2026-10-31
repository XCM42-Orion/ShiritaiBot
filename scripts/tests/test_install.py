"""Installer integration checks with isolated downloads and runtime executables.

Run: python3 -m unittest discover -s scripts/tests -v
No network, Python package installation, real server, or user data is used.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="shiritai-installer-test-")
        self.root = Path(self.scratch.name).resolve()
        self.bin = self.root / "tools"
        self.fixtures = self.root / "fixtures"
        self.target = self.root / "install's folder with $literal spaces"
        self.log = self.root / "calls.jsonl"
        self.command_dir = self.root / "command shortcuts"
        self.bin.mkdir()
        self.fixtures.mkdir()
        # Keep host-installed Node/npm out of the fixture PATH on Linux runners.
        for utility in ('bash', 'sh', 'tar', 'awk', 'mktemp', 'chmod', 'mkdir', 'mv', 'rm', 'cp', 'cat', 'dirname', 'ls', 'sed', 'sha256sum', 'shasum', 'gzip', 'python3'):
            resolved = shutil.which(utility)
            if resolved:
                (self.bin / utility).symlink_to(resolved)
        self.env = {
            **os.environ,
            "HOME": str(self.root / "user-home"),
            "PATH": str(self.bin),
            "SHIRITAI_TEST_FIXTURES": str(self.fixtures),
            "SHIRITAI_TEST_LOG": str(self.log),
            "SHIRITAI_COMMAND_DIR": str(self.command_dir),
        }
        self.executable(self.bin / "uname", '#!/bin/sh\ncase "$1" in -s) echo Darwin;; -m) echo arm64;; esac\n')
        self.executable(self.bin / "curl", '''#!/usr/bin/env python3
import json, os, pathlib, shutil, sys
args = sys.argv[1:]
output = args[args.index('--output') + 1]
url = args[-1]
with open(os.environ['SHIRITAI_TEST_LOG'], 'a') as f:
    f.write(json.dumps(['curl', url]) + '\\n')
fixtures = pathlib.Path(os.environ['SHIRITAI_TEST_FIXTURES'])
if 'main.tar.gz' in url:
    kind = 'source'
    source = 'source.tar.gz'
elif url.endswith('/uv/install.sh'):
    kind = 'uv'
    source = 'uv-install.sh'
elif url.endswith('SHASUMS256.txt'):
    kind = 'manifest'
    source = 'SHASUMS256.txt'
else:
    kind = 'node'
    source = 'node.tar.gz'
if os.environ.get('SHIRITAI_TEST_DOWNLOAD_FAILURE') == kind:
    pathlib.Path(output).write_text('partial download')
    sys.exit(22)
shutil.copyfile(fixtures / source, output)
''')
        self.executable(self.fixtures / "uv", '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['SHIRITAI_TEST_LOG'], 'a') as f:
    f.write(json.dumps(['uv'] + sys.argv[1:]) + '\\n')
if sys.argv[1:] == ['--version']:
    print('uv fixture')
elif sys.argv[1] == 'sync' and os.environ.get('SHIRITAI_TEST_UV_FAILURE'):
    sys.exit(33)
elif 'token' in sys.argv:
    print('fixture-token')
''')
        self.executable(self.fixtures / "uv-install.sh", '''#!/bin/sh
mkdir -p "$UV_UNMANAGED_INSTALL"
cp "$SHIRITAI_TEST_FIXTURES/uv" "$UV_UNMANAGED_INSTALL/uv"
chmod 755 "$UV_UNMANAGED_INSTALL/uv"
''')
        source = self.fixtures / "ShiritaiBot-main"
        (source / "src/shiritaibot").mkdir(parents=True)
        (source / "web").mkdir()
        (source / "src/shiritaibot/cli.py").write_text("# fixture source\n")
        (source / "uv.lock").write_text("version = 1\n")
        (source / ".python-version").write_text("3.13\n")
        (source / "web/package-lock.json").write_text("{}\n")
        self.pack(source, self.fixtures / "source.tar.gz")
        node = self.fixtures / "node-v24.0.0-darwin-arm64"
        (node / "bin").mkdir(parents=True)
        self.executable(node / "bin/node", '#!/bin/sh\n[ "$1" != --version ] || echo v24.0.0\nexit 0\n')
        self.executable(node / "bin/npm", '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['SHIRITAI_TEST_LOG'], 'a') as f:
    f.write(json.dumps(['npm'] + sys.argv[1:]) + '\\n')
if os.environ.get('SHIRITAI_TEST_NPM_FAILURE') == sys.argv[-1]:
    sys.exit(44)
''')
        self.pack(node, self.fixtures / "node.tar.gz")
        digest = hashlib.sha256((self.fixtures / "node.tar.gz").read_bytes()).hexdigest()
        (self.fixtures / "SHASUMS256.txt").write_text(f"{digest}  node-v24.0.0-darwin-arm64.tar.gz\n")

    def tearDown(self):
        self.scratch.cleanup()

    @staticmethod
    def executable(path, content):
        path.write_text(content)
        path.chmod(0o755)

    @staticmethod
    def pack(source, output):
        with tarfile.open(output, "w:gz") as archive:
            archive.add(source, arcname=source.name)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def install(self, *args, no_start=True, **env):
        command = ["/bin/bash", str(INSTALLER), "--dir", str(self.target)]
        if no_start:
            command.append("--no-start")
        return subprocess.run(command + list(args), env={**self.env, **env}, text=True, capture_output=True, timeout=20)

    def assert_no_staging(self):
        self.assertEqual(list(self.target.glob(".install-*")), [])

    def test_fresh_install_bootstraps_private_tools_and_builds(self):
        result = self.install("--port", "8877")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.target / "runtime/bin/uv").is_file())
        self.assertTrue((self.target / "runtime/node/bin/node").is_file())
        self.assertTrue((self.target / "start.sh").is_file())
        calls = self.calls()
        self.assertIn(["uv", "sync", "--frozen", "--no-dev"], calls)
        self.assertIn(["npm", "--prefix", "web", "ci", "--no-audit", "--no-fund"], calls)
        self.assertIn(["npm", "--prefix", "web", "run", "build"], calls)
        self.assertFalse(any(call[:2] == ["uv", "run"] for call in calls))
        self.assert_no_staging()

    def test_foreground_and_token_launcher_use_same_home_and_saved_port(self):
        result = self.install("--port", "8877", no_start=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(["uv", "run", "--frozen", "--no-sync", "python", "-m", "shiritaibot.cli", "run", "--home", str(self.target / "data"), "--port", "8877"], self.calls())
        token = subprocess.run([str(self.target / "start.sh"), "token"], env=self.env, text=True, capture_output=True)
        self.assertEqual(token.returncode, 0, token.stderr)
        self.assertEqual(token.stdout.strip(), "fixture-token")
        self.assertIn(["uv", "run", "--frozen", "--no-sync", "python", "-m", "shiritaibot.cli", "token", "--home", str(self.target / "data")], self.calls())

    def test_rerun_keeps_source_edits_and_data(self):
        self.assertEqual(self.install().returncode, 0)
        edited = self.target / "app/src/shiritaibot/cli.py"
        edited.write_text("# user edit\n")
        secret = self.target / "data/keep.me"
        secret.write_text("existing local data")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(edited.read_text(), "# user edit\n")
        self.assertEqual(secret.read_text(), "existing local data")
        source_downloads = [call for call in self.calls() if call[0] == "curl" and 'main.tar.gz' in call[1]]
        self.assertEqual(len(source_downloads), 1)

    def test_short_command_works_outside_the_install_directory(self):
        self.assertEqual(self.install().returncode, 0)
        command = self.command_dir / "shiritaibot"
        self.assertTrue(command.is_file())
        result = subprocess.run([str(command), "token"], cwd=self.root, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "fixture-token")
        self.assertIn(["uv", "run", "--frozen", "--no-sync", "python", "-m", "shiritaibot.cli", "token", "--home", str(self.target / "data")], self.calls())

    def test_existing_unrelated_command_is_preserved(self):
        self.command_dir.mkdir()
        command = self.command_dir / "shiritaibot"
        self.executable(command, "#!/bin/sh\necho existing-command\n")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(command.read_text(), "#!/bin/sh\necho existing-command\n")

    def test_foreign_directory_is_not_modified(self):
        self.target.mkdir()
        sentinel = self.target / "important.txt"
        sentinel.write_text("keep")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.target.iterdir()), [sentinel])
        self.assertEqual(self.calls(), [])

    def test_download_failure_cleans_staging_and_can_retry(self):
        result = self.install(SHIRITAI_TEST_DOWNLOAD_FAILURE="source")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.target / "app").exists())
        self.assert_no_staging()
        retry = self.install()
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)

    def test_incomplete_source_is_rejected_without_publishing_app(self):
        (self.fixtures / "ShiritaiBot-main/src/shiritaibot/cli.py").unlink()
        self.pack(self.fixtures / "ShiritaiBot-main", self.fixtures / "source.tar.gz")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.target / "app").exists())
        self.assert_no_staging()

    def test_node_checksum_failure_stops_before_build(self):
        (self.fixtures / "SHASUMS256.txt").write_text("0" * 64 + "  node-v24.0.0-darwin-arm64.tar.gz\n")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[0] == "npm" for call in self.calls()))
        self.assertFalse((self.target / "runtime/node").exists())
        self.assert_no_staging()

    def test_dependency_failure_stops_before_build_and_start(self):
        result = self.install(SHIRITAI_TEST_UV_FAILURE="yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[0] == "npm" and '--prefix' in call for call in self.calls()))
        self.assertFalse((self.target / "start.sh").exists())
        self.assert_no_staging()

    def test_build_failure_never_starts_or_reports_success(self):
        result = self.install(no_start=False, SHIRITAI_TEST_NPM_FAILURE="build")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("部署完成", result.stdout)
        self.assertFalse(any(call[:2] == ["uv", "run"] for call in self.calls()))
        self.assert_no_staging()

    def test_compatible_existing_tools_skip_runtime_downloads(self):
        shutil.copy2(self.fixtures / "uv", self.bin / "uv")
        shutil.copy2(self.fixtures / "node-v24.0.0-darwin-arm64/bin/node", self.bin / "node")
        shutil.copy2(self.fixtures / "node-v24.0.0-darwin-arm64/bin/npm", self.bin / "npm")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len([call for call in self.calls() if call[0] == "curl"]), 1)

    def test_incompatible_path_node_gets_private_replacement(self):
        self.executable(self.bin / "node", "#!/bin/sh\nexit 1\n")
        self.executable(self.bin / "npm", "#!/bin/sh\nexit 1\n")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.target / "runtime/node/bin/node").is_file())

    def test_broken_private_node_does_not_hide_healthy_existing_node(self):
        self.target.mkdir()
        (self.target / '.shiritaibot-installer').write_text('shiritaibot-installer-v1')
        private = self.target / 'runtime/node/bin'
        private.mkdir(parents=True)
        self.executable(private / 'node', '#!/bin/sh\nexit 1\n')
        old = (private / 'node').read_bytes()
        shutil.copy2(self.fixtures / 'uv', self.bin / 'uv')
        shutil.copy2(self.fixtures / 'node-v24.0.0-darwin-arm64/bin/node', self.bin / 'node')
        shutil.copy2(self.fixtures / 'node-v24.0.0-darwin-arm64/bin/npm', self.bin / 'npm')
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((private / 'node').read_bytes(), old)
        self.assertEqual(len([call for call in self.calls() if call[0] == 'curl']), 1)
        self.assertIn(str(self.bin / 'node'), result.stdout)

    def test_missing_or_broken_npm_gets_private_pair_without_changing_existing_node(self):
        shutil.copy2(self.fixtures / 'node-v24.0.0-darwin-arm64/bin/node', self.bin / 'node')
        self.executable(self.bin / 'npm', '#!/bin/sh\nexit 1\n')
        original = (self.bin / 'node').read_bytes()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.target / 'runtime/node/bin/npm').is_file())
        self.assertEqual((self.bin / 'node').read_bytes(), original)
        self.assertEqual((self.bin / 'npm').read_text(), '#!/bin/sh\nexit 1\n')

    def test_corrupt_private_runtime_is_backed_up_and_repaired(self):
        self.target.mkdir()
        (self.target / '.shiritaibot-installer').write_text('shiritaibot-installer-v1')
        private = self.target / 'runtime/node/bin'
        private.mkdir(parents=True)
        self.executable(private / 'node', '#!/bin/sh\nexit 1\n')
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        backups = list((self.target / 'runtime').glob('.node-backup-*/node/bin/node'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), '#!/bin/sh\nexit 1\n')
        self.assert_no_staging()

    def test_invalid_port_and_help_do_not_install(self):
        for port in ("0", "65536", "-1", "oops"):
            result = self.install("--port", port)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.target.exists())
        result = self.install("--help")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
