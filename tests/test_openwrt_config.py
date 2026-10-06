# BEGIN SCRIPT VERSION
# Source version: 2026-10-06 (Git ca1910e+dirty)
if __name__ == '__main__':
    import sys
    if sys.argv[1:2] == ['--version']:
        print('test_openwrt_config.py 2026-10-06 (Git ca1910e+dirty)')
        sys.exit(0)
# END SCRIPT VERSION
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIREWALL = b"config defaults\n option input 'REJECT'\n option password 'two\nsecret lines'\n"


class ConfigCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo with spaces'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        self.router = self.root / 'router'
        self.config = self.router / 'etc/config'
        self.config.mkdir(parents=True)
        (self.router / 'tmp').mkdir()
        (self.router / 'root').mkdir()
        (self.config / 'firewall').write_bytes(FIREWALL)
        self.local = self.repo / 'corewrt/etc/config'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.remote_bin = self.root / 'remote-bin'
        self.remote_bin.mkdir()
        # Model a minimal router: no Python, Bash, rsync, or nohup in PATH.
        for command in ('tar', 'gzip', 'cmp', 'mktemp', 'cp', 'mv', 'sleep',
                        'mkdir', 'chmod', 'rm', 'rmdir'):
            (self.remote_bin / command).symlink_to(shutil.which(command))
        self.remote_script('uci', '''if [ "$1" = changes ]; then
    printf '%s' "${PENDING:-}"
    exit 0
fi
exit "${INVALID:-0}"
''')
        self.remote_script('sleep', 'exit 0\n')
        self.remote_script('ubus', '''printf '%s\\n' "$4" >> "$EVENT_LOG"
exit "${UBUS_FAILURE:-0}"
''')
        fake_ssh = self.bin / 'ssh'
        fake_ssh.write_text(f'#!{sys.executable}\n' + '''import os, pathlib, re, subprocess, sys
root = pathlib.Path(os.environ['ROUTER'])
if os.environ.get('SSH_FAILURE'):
    sys.exit(1)
if sys.argv[-1].startswith('tar '):
    sys.exit(subprocess.run(['tar', '-czf', '-', '-C', str(root), 'etc/config']).returncode)
script = sys.argv[-1]
script = re.sub(r'/etc/config|/tmp/|/root/', lambda match: str(root) + match[0], script)
# Wait only in tests, so events and apply.log can be asserted deterministically.
if not os.environ.get('DETACHED'):
    script += '\\nwait\\n'
env = dict(os.environ, PATH=os.environ['REMOTE_BIN'])
sys.exit(subprocess.run(['/bin/sh', '-c', script], env=env).returncode)
''')
        fake_ssh.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
                        ROUTER=str(self.router), REMOTE_BIN=str(self.remote_bin),
                        EVENT_LOG=str(self.root / 'events'))

    def remote_script(self, name, body):
        path = self.remote_bin / name
        if path.is_symlink():
            path.unlink()
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o700)

    def run_script(self, name):
        return subprocess.run([str(ROOT / name), '--name', 'corewrt', 'root@router', str(self.repo)],
                              env=self.env, capture_output=True, timeout=10)

    def pull(self):
        result = self.run_script('openwrt_pullconfig')
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def events(self):
        return [json.loads(line)['data']['package']
                for line in (self.root / 'events').read_text().splitlines()]

    def test_pull_edit_push_preserves_bytes_and_secrets_without_nohup(self):
        result = self.pull()
        self.assertEqual((self.local / 'firewall').read_bytes(), FIREWALL)
        edited = FIREWALL.replace(b'REJECT', b'ACCEPT')
        (self.local / 'firewall').write_bytes(edited)
        (self.local / 'system').write_bytes(b"config system\n option hostname 'corewrt'\n")
        (self.config / 'unrelated').write_bytes(b'keep this')
        result = self.run_script('openwrt_pushconfig')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'Configuration installed and verified (2 packages)', result.stdout)
        self.assertEqual((self.config / 'firewall').read_bytes(), edited)
        self.assertEqual((self.config / 'system').read_bytes(), (self.local / 'system').read_bytes())
        self.assertEqual((self.config / 'unrelated').read_bytes(), b'keep this')
        backup, = (self.router / 'root').glob('*/config/firewall')
        self.assertEqual(backup.read_bytes(), FIREWALL)
        self.assertEqual(self.events(), ['firewall', 'system'])
        self.assertNotIn(b'secret lines', result.stdout + result.stderr)
        self.assertFalse((self.remote_bin / 'nohup').exists())
        self.assertEqual((self.config / 'firewall').stat().st_mode & 0o777, 0o600)

    def test_pull_mirrors_deletions(self):
        self.pull()
        (self.local / 'obsolete').write_text('old')
        (self.local / 'firewall').write_text('local edit')
        self.pull()
        self.assertFalse((self.local / 'obsolete').exists())
        self.assertEqual((self.local / 'firewall').read_bytes(), FIREWALL)

    def test_failed_download_or_symlink_leaves_local_copy_unchanged(self):
        self.pull()
        self.env['SSH_FAILURE'] = '1'
        self.assertNotEqual(self.run_script('openwrt_pullconfig').returncode, 0)
        del self.env['SSH_FAILURE']
        (self.config / 'link').symlink_to('/etc/passwd')
        self.assertNotEqual(self.run_script('openwrt_pullconfig').returncode, 0)
        self.assertEqual((self.local / 'firewall').read_bytes(), FIREWALL)
        self.assertFalse((self.local / 'link').exists())

    def test_invalid_or_pending_uci_changes_leave_router_unchanged(self):
        self.pull()
        (self.local / 'firewall').write_bytes(FIREWALL.replace(b'REJECT', b'ACCEPT'))
        for setting in ('INVALID', 'PENDING'):
            with self.subTest(setting=setting):
                self.env[setting] = '1'
                result = self.run_script('openwrt_pushconfig')
                del self.env[setting]
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.config / 'firewall').read_bytes(), FIREWALL)
                self.assertFalse(list((self.router / 'root').iterdir()))
                self.assertFalse((self.root / 'events').exists())

    def test_old_placeholders_are_rejected_before_upload(self):
        self.pull()
        (self.local / 'firewall').write_bytes(FIREWALL.replace(b'two\nsecret lines', b'[REDACTED]'))
        result = self.run_script('openwrt_pushconfig')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'old [REDACTED] placeholders', result.stderr)
        self.assertEqual((self.config / 'firewall').read_bytes(), FIREWALL)

    def test_partial_install_or_silent_move_failure_rolls_back(self):
        self.pull()
        (self.local / 'aaa').write_bytes(b'config new\n')
        (self.local / 'firewall').write_bytes(FIREWALL.replace(b'REJECT', b'ACCEPT'))
        for status in (1, 0):
            with self.subTest(status=status):
                self.remote_script('mv', f'case "$3" in */firewall) exit {status};; esac\nexec /bin/mv "$@"\n')
                result = self.run_script('openwrt_pushconfig')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.config / 'firewall').read_bytes(), FIREWALL)
                self.assertFalse((self.config / 'aaa').exists())
                self.assertFalse((self.root / 'events').exists())
                self.assertNotIn(b'Configuration installed and verified', result.stdout)
                self.assertIn(b'attempted restore', result.stderr)

    def test_reload_failure_is_logged(self):
        self.pull()
        self.env['UBUS_FAILURE'] = '1'
        result = self.run_script('openwrt_pushconfig')
        self.assertEqual(result.returncode, 0, result.stderr)
        log, = (self.router / 'root').glob('*/apply.log')
        self.assertIn('Reload request failed: firewall', log.read_text())
        self.assertIn('Service reload requests exit status: 1', log.read_text())

    def test_detached_reload_survives_hangup_after_ssh_returns(self):
        self.pull()
        self.env['DETACHED'] = '1'
        # Signal the background shell after the SSH shell has exited.
        self.remote_script('sleep', '/bin/sleep 0.2\nkill -HUP "$PPID"\n')
        result = self.run_script('openwrt_pushconfig')
        self.assertEqual(result.returncode, 0, result.stderr)
        log, = (self.router / 'root').glob('*/apply.log')
        deadline = time.monotonic() + 5
        while 'Service reload requests exit status: 0' not in log.read_text() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIn('Service reload requests exit status: 0', log.read_text())
        self.assertEqual(self.events(), ['firewall'])


if __name__ == '__main__':
    unittest.main()
