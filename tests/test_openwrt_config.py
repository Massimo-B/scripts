# BEGIN SCRIPT VERSION
# Source version: 2026-10-16 (Git 238ae2b)
if __name__ == '__main__':
    import sys
    if sys.argv[1:2] == ['--version']:
        print('test_openwrt_config.py 2026-10-16 (Git 238ae2b)')
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
FIREWALL = b"config defaults\n option input 'REJECT'\n"
UCI_BINARY = os.environ.get('UCI_TEST_BINARY') or shutil.which('uci')


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
        self.hostname = self.router / 'proc/sys/kernel/hostname'
        self.hostname.parent.mkdir(parents=True)
        self.hostname.write_text('corewrt\n')
        (self.config / 'firewall').write_bytes(FIREWALL)
        self.local = self.repo / 'corewrt/etc/config'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.remote_bin = self.root / 'remote-bin'
        self.remote_bin.mkdir()
        # Model a minimal router: no Python, Bash, rsync, or nohup in PATH.
        for command in ('tar', 'gzip', 'cmp', 'mktemp', 'cp', 'mv', 'sleep',
                        'mkdir', 'chmod', 'rm', 'rmdir', 'awk', 'cat'):
            (self.remote_bin / command).symlink_to(shutil.which(command))
        self.remote_script('uci', '''if [ "$1" = changes ]; then
    printf '%s' "${PENDING:-}"
    exit 0
fi
exit "${INVALID:-0}"
''')
        if UCI_BINARY:
            wrapper = self.remote_bin / 'uci'
            wrapper.write_text(f'#!{sys.executable}\n' + '''import os, subprocess, sys
args = sys.argv[1:]
if args == ['changes'] and os.environ.get('PENDING'):
    print('pending')
    sys.exit(0)
if os.environ.get('INVALID') and 'export' in args:
    sys.exit(1)
root = os.environ['ROUTER']
command = [os.environ['UCI_TEST_BINARY'], '-c', root + '/etc/config', '-t', root + '/delta', *args]
os.makedirs(root + '/delta', exist_ok=True)
sys.exit(subprocess.run(command).returncode)
''')
            wrapper.chmod(0o700)
        self.remote_script('sleep', 'exit 0\n')
        self.remote_script('ubus', '''printf '%s\\n' "$4" >> "$EVENT_LOG"
exit "${UBUS_FAILURE:-0}"
''')
        fake_ssh = self.bin / 'ssh'
        fake_ssh.write_text(f'#!{sys.executable}\n' + '''import os, pathlib, re, subprocess, sys
root = pathlib.Path(os.environ['ROUTER'])
if os.environ.get('SSH_FAILURE'):
    sys.exit(1)
script = sys.argv[-1]
script = re.sub(r'(?<![a-zA-Z0-9])/etc/config|(?<![a-zA-Z0-9])/etc/apk|/proc/sys/kernel/hostname|/tmp/|/root/', lambda match: str(root) + match[0], script)
script = script.replace('-C / ', '-C ' + str(root) + ' ')
# Wait only in tests, so events and apply.log can be asserted deterministically.
if not os.environ.get('DETACHED'):
    script += '\\nstatus=$?\\nwait\\nexit $status\\n'
env = dict(os.environ, PATH=os.environ['REMOTE_BIN'])
sys.exit(subprocess.run(['/bin/sh', '-c', script], env=env).returncode)
''')
        fake_ssh.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
                        ROUTER=str(self.router), REMOTE_BIN=str(self.remote_bin),
                        EVENT_LOG=str(self.root / 'events'))
        if UCI_BINARY:
            self.env['UCI_TEST_BINARY'] = UCI_BINARY

    def remote_script(self, name, body):
        path = self.remote_bin / name
        if path.is_symlink():
            path.unlink()
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o700)

    def run_script(self, name, *options, host='root@router'):
        return subprocess.run([str(ROOT / name), '--name', 'corewrt', *options, host, str(self.repo)],
                              env=self.env, capture_output=True, timeout=10)

    def pull(self):
        result = self.run_script('openwrt_pullconfig')
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def events(self):
        return [json.loads(line)['data']['package']
                for line in (self.root / 'events').read_text().splitlines()]

    def test_pull_edit_push_preserves_public_bytes_without_nohup(self):
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

    def test_wrong_hostname_pull_preserves_local_snapshot(self):
        self.pull()
        (self.local / 'firewall').write_text('local edits')
        (self.local / 'obsolete').write_text('keep')
        self.hostname.write_text('otherwrt\n')
        result = self.run_script('openwrt_pullconfig')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'expected corewrt, got otherwrt', result.stderr)
        self.assertIn(b'--force', result.stderr)
        self.assertEqual((self.local / 'firewall').read_text(), 'local edits')
        self.assertEqual((self.local / 'obsolete').read_text(), 'keep')

    def test_wrong_hostname_first_pull_does_not_create_snapshot(self):
        self.hostname.write_text('otherwrt\n')
        result = self.run_script('openwrt_pullconfig')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.repo / 'corewrt').exists())

    def test_wrong_hostname_push_has_no_router_side_effects(self):
        apk = self.setup_apk()
        self.pull()
        (self.local / 'firewall').write_text('local edits')
        self.hostname.write_text('otherwrt\n')
        result = self.run_script('openwrt_pushconfig')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'expected corewrt, got otherwrt', result.stderr)
        self.assertEqual((self.config / 'firewall').read_bytes(), FIREWALL)
        self.assertEqual((apk / 'world').read_text(), 'extra@custom\nother>=2\n')
        self.assertFalse(list((self.router / 'root').iterdir()))
        self.assertFalse(list((self.router / 'tmp').iterdir()))
        self.assertFalse((self.router / 'apk-events').exists())
        self.assertFalse((self.root / 'events').exists())

    def test_force_allows_pull_and_push_with_different_hostname(self):
        self.hostname.write_text('otherwrt\n')
        result = self.run_script('openwrt_pullconfig', '--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.local / 'firewall').read_bytes(), FIREWALL)
        edited = FIREWALL.replace(b'REJECT', b'ACCEPT')
        (self.local / 'firewall').write_bytes(edited)
        result = self.run_script('openwrt_pushconfig', '--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.config / 'firewall').read_bytes(), edited)

    def test_missing_hostname_fails_unless_forced(self):
        self.pull()
        self.hostname.unlink()
        for command in ('openwrt_pullconfig', 'openwrt_pushconfig'):
            result = self.run_script(command)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'Cannot read router hostname', result.stderr)
        self.assertFalse(list((self.router / 'root').iterdir()))
        for command in ('openwrt_pullconfig', 'openwrt_pushconfig'):
            result = self.run_script(command, '--force')
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_hostname_matches_name_independently_of_ssh_alias(self):
        result = self.run_script('openwrt_pullconfig', host='root@corewrt.mgmt')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_script('openwrt_pushconfig', host='different-alias')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.repo / 'corewrt/router-identity.pub').exists())

    def wireless(self, anonymous=False):
        section = b"config wifi-iface\n" if anonymous else b"config wifi-iface 'guest'\n"
        content = section + b" option ssid 'Guest'\n option encryption 'psk2'\n option key 'one\nsecret\n\n'\n list sae_password 'first'\n list sae_password 'second'\n"
        (self.config / 'wireless').write_bytes(content)
        return content

    def uci_value(self, path, directory=None, command='get'):
        return subprocess.check_output([UCI_BINARY, '-c', str(directory or self.config),
                                        '-t', str(self.router / 'delta'), command, path])

    @unittest.skipUnless(UCI_BINARY, 'Set UCI_TEST_BINARY for real UCI integration tests')
    def test_secrets_filtered_on_router_and_preserved_with_multiline_and_lists(self):
        self.wireless()
        old_key = self.uci_value('wireless.guest.key')
        old_list = self.uci_value('wireless.guest.sae_password', command='show')
        self.pull()
        local = self.local / 'wireless'
        self.assertNotIn(b'secret', local.read_bytes())
        self.assertNotIn(b'option key', local.read_bytes())
        self.assertNotIn(b'list sae_password', local.read_bytes())
        required = (self.repo / 'corewrt/secret-paths').read_text()
        self.assertIn('wireless.guest.key wifi-iface', required)
        self.assertIn('wireless.guest.sae_password wifi-iface', required)
        local.write_bytes(local.read_bytes().replace(b'Guest', b'Changed'))
        result = self.run_script('openwrt_pushconfig')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.uci_value('wireless.guest.key'), old_key)
        self.assertEqual(self.uci_value('wireless.guest.sae_password', command='show'), old_list)
        self.assertEqual(self.uci_value('wireless.guest.ssid'), b'Changed\n')
        self.assertNotIn(b'one\nsecret', result.stdout + result.stderr)

    @unittest.skipUnless(UCI_BINARY, 'Set UCI_TEST_BINARY for real UCI integration tests')
    def test_private_overrides_replace_lists_and_support_quotes(self):
        self.wireless()
        self.pull()
        private = self.repo / 'corewrt/secrets'
        private.mkdir()
        override = b"config wifi-iface 'guest'\n option key 'new'\\''quoted\n\n'\n list sae_password 'replacement; $(touch /tmp/injected-secrets-test)'\n"
        (private / 'wireless').write_bytes(override)
        result = self.run_script('openwrt_pushconfig')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.uci_value('wireless.guest.key'), b"new'quoted\n\n\n")
        self.assertEqual(self.uci_value('wireless.guest.sae_password'), b"'replacement; $(touch /tmp/injected-secrets-test)'\n")
        self.assertFalse((self.router / 'tmp/injected-secrets-test').exists())
        self.assertEqual((private / 'wireless').stat().st_mode & 0o777, 0o600)
        self.assertNotIn(b"new'quoted", result.stdout + result.stderr)
        result = subprocess.run(['git', '-C', str(self.repo), 'check-ignore', '-q', 'corewrt/secrets/wireless'])
        self.assertEqual(result.returncode, 0)
        self.pull()
        self.assertEqual((private / 'wireless').read_bytes(), override)

    @unittest.skipUnless(UCI_BINARY, 'Set UCI_TEST_BINARY for real UCI integration tests')
    def test_inline_password_is_rejected_even_with_force_without_leaking_value(self):
        self.pull()
        (self.local / 'wireless').write_bytes(b"config wifi-iface 'guest'\n option key 'do-not-print-me'\n")
        result = self.run_script('openwrt_pushconfig', '--force')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Move inline secrets', result.stderr)
        self.assertNotIn(b'do-not-print-me', result.stdout + result.stderr)
        self.assertFalse((self.config / 'wireless').exists())
        self.assertFalse(list((self.router / 'root').iterdir()))

    @unittest.skipUnless(UCI_BINARY, 'Set UCI_TEST_BINARY for real UCI integration tests')
    def test_missing_required_secret_and_unknown_override_section_abort(self):
        self.wireless()
        self.pull()
        (self.config / 'wireless').write_bytes(b"config wifi-iface 'guest'\n option ssid 'Guest'\n option encryption 'psk2'\n")
        result = self.run_script('openwrt_pushconfig')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Missing required secret', result.stderr)
        private = self.repo / 'corewrt/secrets'
        private.mkdir()
        (private / 'wireless').write_bytes(b"config wifi-iface 'wrong'\n option key 'do-not-print-me'\n")
        result = self.run_script('openwrt_pushconfig')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Secret override section missing', result.stderr)
        self.assertFalse(list((self.router / 'root').iterdir()))
        self.assertFalse((self.root / 'events').exists())

    @unittest.skipUnless(UCI_BINARY, 'Set UCI_TEST_BINARY for real UCI integration tests')
    def test_new_wifi_needs_private_password_and_custom_secret_rules_work(self):
        self.pull()
        (self.repo / 'corewrt/secret-options').write_text('credentials\n')
        public = b"config wifi-iface 'new_guest'\n option encryption 'psk2'\n option ssid 'New'\n"
        (self.local / 'wireless').write_bytes(public)
        result = self.run_script('openwrt_pushconfig')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Missing required secret: wireless.new_guest.key', result.stderr)
        private = self.repo / 'corewrt/secrets'
        private.mkdir()
        (private / 'wireless').write_bytes(b"config wifi-iface 'new_guest'\n option key 'new-pass'\n list credentials 'first'\n list credentials 'second'\n")
        result = self.run_script('openwrt_pushconfig')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.uci_value('wireless.new_guest.key'), b'new-pass\n')
        self.assertEqual(self.uci_value('wireless.new_guest.credentials'), b'first second\n')
        self.pull()
        self.assertNotIn(b'new-pass', (self.local / 'wireless').read_bytes())
        self.assertNotIn(b'credentials', (self.local / 'wireless').read_bytes())

    @unittest.skipUnless(UCI_BINARY, 'Set UCI_TEST_BINARY for real UCI integration tests')
    def test_anonymous_secrets_roundtrip_and_overrides(self):
        self.wireless(anonymous=True)
        # Named sections of the same type also count in UCI positional indices.
        radius = (b"config server 'named'\n option host 'named'\n"
                  b"config server\n option host 'first'\n option key 'first-secret'\n"
                  b"config server\n option host 'second'\n option key 'second-secret'\n")
        (self.config / 'radius').write_bytes(radius)
        old_key = self.uci_value('wireless.@wifi-iface[0].key')
        old_list = self.uci_value('wireless.@wifi-iface[0].sae_password')
        self.pull()
        self.assertNotIn(b'secret', (self.local / 'radius').read_bytes())
        self.assertNotIn(b'option key', (self.local / 'wireless').read_bytes())
        self.assertIn('radius.@server[1].key server',
                      (self.repo / 'corewrt/secret-paths').read_text())
        for _ in range(2):
            result = self.run_script('openwrt_pushconfig')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.uci_value('radius.@server[1].key'), b'first-secret\n')
            self.assertEqual(self.uci_value('radius.@server[2].key'), b'second-secret\n')
            self.assertEqual(self.uci_value('wireless.@wifi-iface[0].key'), old_key)
            self.assertEqual(self.uci_value('wireless.@wifi-iface[0].sae_password'), old_list)
        private = self.repo / 'corewrt/secrets'
        private.mkdir()
        (private / 'radius').write_bytes(
            b"config server 'named'\nconfig server\nconfig server\n option key 'replacement'\n")
        result = self.run_script('openwrt_pushconfig')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.uci_value('radius.@server[1].key'), b'first-secret\n')
        self.assertEqual(self.uci_value('radius.@server[2].key'), b'replacement\n')

    @unittest.skipUnless(UCI_BINARY, 'Set UCI_TEST_BINARY for real UCI integration tests')
    def test_anonymous_section_reorder_requires_explicit_secrets(self):
        (self.config / 'radius').write_bytes(
            b"config server\n option host 'first'\n option key 'first-secret'\n"
            b"config server\n option host 'second'\n option key 'second-secret'\n")
        original = (self.config / 'radius').read_bytes()
        self.pull()
        local = self.local / 'radius'
        local.write_bytes(local.read_bytes().replace(b'first', b'temp').replace(b'second', b'first').replace(b'temp', b'second'))
        result = self.run_script('openwrt_pushconfig')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Missing required secret', result.stderr)
        self.assertEqual((self.config / 'radius').read_bytes(), original)
        self.assertFalse(list((self.router / 'root').iterdir()))
        private = self.repo / 'corewrt/secrets'
        private.mkdir()
        (private / 'radius').write_bytes(
            b"config server\n option key 'second-secret'\n"
            b"config server\n option key 'first-secret'\n")
        result = self.run_script('openwrt_pushconfig')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.uci_value('radius.@server[0].key'), b'second-secret\n')
        self.assertEqual(self.uci_value('radius.@server[1].key'), b'first-secret\n')

    def test_precommit_scans_staged_content_and_blocks_forced_secret_files(self):
        self.pull()
        helper = str(ROOT / 'openwrt_secrets')
        result = subprocess.run([helper, '--install-hook', str(self.repo)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.local / 'wireless').write_text("config wifi-iface 'guest'; option 'key' 'do-not-print-me'\n")
        subprocess.run(['git', '-C', str(self.repo), 'add', 'corewrt'], check=True)
        (self.local / 'wireless').write_text("config wifi-iface 'guest'\n")
        identity = dict(os.environ, GIT_AUTHOR_NAME='Codex', GIT_AUTHOR_EMAIL='codex@openai.com',
                        GIT_COMMITTER_NAME='Codex', GIT_COMMITTER_EMAIL='codex@openai.com')
        result = subprocess.run(['git', '-C', str(self.repo), 'commit', '-m', 'test'], env=identity, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Secret check failed', result.stderr)
        self.assertNotIn(b'do-not-print-me', result.stdout + result.stderr)
        subprocess.run(['git', '-C', str(self.repo), 'add', 'corewrt/etc/config/wireless'], check=True)
        result = subprocess.run(['git', '-C', str(self.repo), 'commit', '-m', 'test'], env=identity, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        private = self.repo / 'corewrt/secrets'
        private.mkdir()
        (private / 'wireless').write_text("config wifi-iface 'guest'\n option key 'do-not-print-me'\n")
        subprocess.run(['git', '-C', str(self.repo), 'add', '-f', 'corewrt/secrets/wireless'], check=True)
        result = subprocess.run(['git', '-C', str(self.repo), 'commit', '-m', 'test'], env=identity, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Secret file must not be staged/tracked', result.stderr)
        self.assertNotIn(b'do-not-print-me', result.stdout + result.stderr)

    def test_checker_handles_quotes_multiline_values_and_custom_policy(self):
        self.pull()
        package = self.local / 'network'
        package.write_text("config interface 'wan'\n option note 'line one\n option password fake\nline three'\n")
        helper = str(ROOT / 'openwrt_secrets')
        result = subprocess.run([helper, '--check', str(self.repo)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.repo / 'corewrt/secret-options').write_text('credentials\n')
        package.write_text("config interface 'wan'\n list 'credentials' 'do-not-print-me\nline two'\n")
        result = subprocess.run([helper, '--check', str(self.repo)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'network.wan.credentials', result.stderr)
        self.assertNotIn(b'do-not-print-me', result.stdout + result.stderr)
        subprocess.run(['git', '-C', str(self.repo), 'add', 'corewrt/etc/config/network'], check=True)
        result = subprocess.run([helper, '--check-staged', str(self.repo)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'network.wan.credentials', result.stderr)
        package.write_text("config interface 'wan'\n option note 'unterminated\n")
        result = subprocess.run([helper, '--check', str(self.repo)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'unterminated UCI statement', result.stderr)

    def test_hook_installer_preserves_existing_hook(self):
        hook = self.repo / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nexit 0\n')
        result = subprocess.run([str(ROOT / 'openwrt_secrets'), '--install-hook', str(self.repo)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(hook.read_text(), '#!/bin/sh\nexit 0\n')

    @unittest.skipUnless(UCI_BINARY, 'Set UCI_TEST_BINARY for real UCI integration tests')
    def test_secret_override_rejects_nonsecret_options_and_wrong_section_types(self):
        original = self.wireless()
        self.pull()
        private = self.repo / 'corewrt/secrets'
        private.mkdir()
        for override, expected in (
                (b"config wifi-iface 'guest'\n option ssid 'Changed'\n", b'non-secret option'),
                (b"config interface 'guest'\n option key 'do-not-print-me'\n", b'Secret section type mismatch'),
                (b"config wifi-iface 'guest'\n option key ''\n", b'empty secret override')):
            with self.subTest(expected=expected):
                (private / 'wireless').write_bytes(override)
                result = self.run_script('openwrt_pushconfig')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertNotIn(b'do-not-print-me', result.stdout + result.stderr)
                self.assertEqual((self.config / 'wireless').read_bytes(), original)
                self.assertFalse(list((self.router / 'root').iterdir()))

    @unittest.skipUnless(UCI_BINARY, 'Set UCI_TEST_BINARY for real UCI integration tests')
    def test_new_anonymous_wifi_without_key_is_rejected(self):
        self.pull()
        (self.local / 'wireless').write_bytes(b"config wifi-iface\n option encryption 'psk2'\n")
        result = self.run_script('openwrt_pushconfig')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Missing required secret', result.stderr)
        self.assertFalse((self.config / 'wireless').exists())

    def setup_apk(self):
        apk = self.router / 'etc/apk'
        (apk / 'repositories.d').mkdir(parents=True)
        (apk / 'keys').mkdir()
        (apk / 'world').write_text('extra@custom\nother>=2\n')
        (apk / 'repositories.d/custom.list').write_text('https://example.org/packages.adb\n')
        (apk / 'keys/custom.pem').write_text('public key')
        self.remote_script('apk', '''printf '%s\\n' "$*" >> "$ROUTER/apk-events"
[ "${APK_FAILURE:-}" != "$1" ] || exit 1
if [ "$1" = add ]; then
    [ "$#" = 2 ] && [ "$2" = -- ] || exit 2
    # Model the solver applying world, keeping already satisfied requirements.
    if ! cmp -s "$ROUTER/etc/apk/world" "$ROUTER/installed-world"; then
        printf '%s\\n' synchronized >> "$ROUTER/package-changes"
        cp "$ROUTER/etc/apk/world" "$ROUTER/installed-world"
    fi
fi
''')
        return apk

    def test_apk_snapshot_and_push_install_saved_requirements(self):
        apk = self.setup_apk()
        self.pull()
        local = self.repo / 'corewrt/etc/apk'
        self.assertEqual((local / 'world').read_bytes(), (apk / 'world').read_bytes())
        self.assertEqual((local / 'keys/custom.pem').read_text(), 'public key')
        self.assertEqual((local / 'world').stat().st_mode & 0o777, 0o600)
        (apk / 'world').write_text('base\n')
        (apk / 'repositories.d/custom.list').write_text('old')
        result = self.run_script('openwrt_pushconfig')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.router / 'apk-events').read_text(), 'update\nadd --\n')
        self.assertEqual((apk / 'world').read_text(), 'extra@custom\nother>=2\n')
        self.assertEqual((apk / 'repositories.d/custom.list').read_bytes(), (local / 'repositories.d/custom.list').read_bytes())

    def test_repeated_push_keeps_packages_and_deleted_requirement_is_removed(self):
        self.setup_apk()
        self.pull()
        installed = self.router / 'installed-world'
        installed.write_text('obsolete\nextra@custom\n')
        result = self.run_script('openwrt_pushconfig')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(installed.read_text(), 'extra@custom\nother>=2\n')
        changes = self.router / 'package-changes'
        self.assertEqual(changes.read_text(), 'synchronized\n')
        result = self.run_script('openwrt_pushconfig')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(changes.read_text(), 'synchronized\n')
        (self.repo / 'corewrt/etc/apk/world').write_text('extra@custom\n')
        result = self.run_script('openwrt_pushconfig')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(installed.read_text(), 'extra@custom\n')
        self.assertEqual(changes.read_text(), 'synchronized\nsynchronized\n')
        # An empty world must still invoke the solver to remove remaining packages.
        (self.repo / 'corewrt/etc/apk/world').write_text('')
        result = self.run_script('openwrt_pushconfig')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(installed.read_text(), '')

    def test_failed_apk_install_restores_apk_and_keeps_uci(self):
        apk = self.setup_apk()
        self.pull()
        (apk / 'world').write_text('base\n')
        (self.local / 'firewall').write_text('changed')
        for command in ('update', 'add'):
            with self.subTest(command=command):
                self.env['APK_FAILURE'] = command
                result = self.run_script('openwrt_pushconfig')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((apk / 'world').read_text(), 'base\n')
                self.assertEqual((self.config / 'firewall').read_bytes(), FIREWALL)
                self.assertFalse((self.root / 'events').exists())

    def test_apk_snapshot_requires_apk_on_target(self):
        apk = self.setup_apk()
        self.pull()
        (self.remote_bin / 'apk').unlink()
        original = (apk / 'world').read_bytes()
        result = self.run_script('openwrt_pushconfig')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Router has no apk', result.stderr)
        self.assertEqual((apk / 'world').read_bytes(), original)

    def test_missing_world_aborts_pull_before_local_changes(self):
        apk = self.setup_apk()
        self.pull()
        (apk / 'world').unlink()
        (self.config / 'firewall').write_text('changed')
        self.assertNotEqual(self.run_script('openwrt_pullconfig').returncode, 0)
        self.assertEqual((self.local / 'firewall').read_bytes(), FIREWALL)

    def test_apk_symlink_rejected_on_pull_and_push(self):
        apk = self.setup_apk()
        self.pull()
        (apk / 'keys/link').symlink_to('/etc/passwd')
        self.assertNotEqual(self.run_script('openwrt_pullconfig').returncode, 0)
        (self.repo / 'corewrt/etc/apk/keys/link').symlink_to('/etc/passwd')
        self.assertNotEqual(self.run_script('openwrt_pushconfig').returncode, 0)

    def test_apk_mirrors_deleted_files(self):
        apk = self.setup_apk()
        self.pull()
        (apk / 'keys/custom.pem').unlink()
        self.pull()
        self.assertFalse((self.repo / 'corewrt/etc/apk/keys/custom.pem').exists())

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
        (self.local / 'firewall').write_bytes(FIREWALL + b" option description '[REDACTED]'\n")
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
