import io
import os
from pathlib import Path
import runpy
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PUSH = runpy.run_path(str(ROOT / 'openwrt_pushconfig'))
merge = PUSH['merge']


class SecretMergeTests(unittest.TestCase):
    def test_named_sections_and_multiline_secrets(self):
        current = b"config wifi-iface 'main'\n option ssid 'Old'\n option key 'two\nlines'\n"
        local = b"config wifi-iface 'main'\n option ssid 'New'\n option key '[REDACTED]'\n"
        result = merge(local, current)
        self.assertIn(b"option ssid 'New'", result)
        self.assertIn(b"'two\nlines'", result)
        self.assertNotIn(b'[REDACTED]', result)

    def test_list_and_explicit_secret_changes(self):
        current = b"config test 'main'\n list keys 'one'\n list keys 'two'\n option password 'old'\n"
        local = b"config test 'main'\n list keys '[REDACTED]'\n list keys '[REDACTED]'\n option password 'new'\n"
        result = merge(local, current)
        self.assertIn(b'list keys one', result)
        self.assertIn(b'list keys two', result)
        self.assertIn(b"option password 'new'", result)

    def test_unresolved_and_mismatched_secrets_fail(self):
        local = b"config test 'main'\n option key '[REDACTED]'\n"
        for current in (b'', b"config other 'main'\n option key 'value'\n",
                        b"config test 'main'\n", local,
                        b"config test 'main'\n list key 'value'\n"):
            with self.subTest(current=current), self.assertRaises(ValueError):
                merge(local, current)

    def test_anonymous_layout_change_fails(self):
        current = b"config test\n option key 'secret'\n"
        local = b"config test\n option key '[REDACTED]'\nconfig test\n"
        with self.assertRaises(ValueError):
            merge(local, current)

    def test_named_section_reordering(self):
        current = b"config test 'a'\n option key 'first'\nconfig test 'b'\n option key 'second'\n"
        local = b"config test 'b'\n option key '[REDACTED]'\nconfig test 'a'\n option key '[REDACTED]'\n"
        result = merge(local, current)
        self.assertLess(result.index(b'second'), result.index(b'first'))

    def test_malformed_files_fail(self):
        for data in (b"config test\n option key 'unfinished", b'option key value\n',
                     b'config test\n option key one\n option key two\n'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                merge(data, b'')


class RemoteInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('config', 'root', 'tmp', 'bin'):
            (self.root / name).mkdir()
        self.config = self.root / 'config'
        (self.config / 'wireless').write_bytes(b'old config')
        (self.config / 'unrelated').write_bytes(b'keep this')
        self.env = dict(os.environ, PATH=str(self.root / 'bin') + ':' + os.environ['PATH'])
        self.executable('uci', '#!/bin/sh\nif [ "$1" = changes ]; then printf "%s" "${PENDING:-}"; exit 0; fi\nexit "${INVALID:-0}"\n')
        self.executable('nohup', '#!/bin/sh\necho scheduled\n')
        reload = self.executable('reload_config', '#!/bin/sh\nexit 0\n')
        self.script = PUSH['REMOTE_INSTALL'].replace('/tmp/', str(self.root / 'tmp') + '/')
        self.script = self.script.replace('/etc/config', str(self.config))
        self.script = self.script.replace('/root/', str(self.root / 'root') + '/')
        self.script = self.script.replace('/sbin/reload_config', str(reload))

    def executable(self, name, content):
        path = self.root / 'bin' / name
        path.write_text(content)
        path.chmod(0o700)
        return path

    def install(self, old=b'old config'):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode='w:gz') as archive:
            for name, data in [('old/wireless', old), ('new/wireless', b'new config'),
                               ('new/system', b'new package')]:
                member = tarfile.TarInfo(name)
                member.mode = 0o600
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return subprocess.run(['sh', '-c', self.script], input=payload.getvalue(),
                              env=self.env, capture_output=True)

    def test_install_backup_and_keep_unrelated_files(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.config / 'wireless').read_bytes(), b'new config')
        self.assertEqual((self.config / 'system').read_bytes(), b'new package')
        self.assertEqual((self.config / 'unrelated').read_bytes(), b'keep this')
        backups = list((self.root / 'root').glob('*/config/wireless'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b'old config')
        self.assertIn(b'Service reload scheduled', result.stdout)

    def test_validation_pending_changes_and_concurrent_edits(self):
        for env, old in [({'INVALID': '1'}, b'old config'),
                         ({'PENDING': 'wireless.main.ssid=new'}, b'old config'),
                         ({}, b'outdated config')]:
            with self.subTest(env=env, old=old):
                self.env.pop('INVALID', None)
                self.env.pop('PENDING', None)
                self.env.update(env)
                result = self.install(old)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.config / 'wireless').read_bytes(), b'old config')
                self.assertFalse((self.config / 'system').exists())
                self.assertFalse(list((self.root / 'root').iterdir()))

    def test_partial_install_rolls_back(self):
        self.executable('mv', '#!/bin/sh\ncase "$3" in */wireless) exit 1;; esac\nexec /bin/mv "$@"\n')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.config / 'wireless').read_bytes(), b'old config')
        self.assertFalse((self.config / 'system').exists())
        self.assertNotIn(b'Service reload scheduled', result.stdout)


class CommandTests(unittest.TestCase):
    def test_pull_edit_push_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / 'repo'
            repo.mkdir()
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            archive_path = root / 'router.tar.gz'
            current = b"config wifi-iface 'main'\n option ssid 'Old'\n option key 'router-secret'\n"
            with tarfile.open(archive_path, 'w:gz') as archive:
                directory = tarfile.TarInfo('etc/config')
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o700
                archive.addfile(directory)
                member = tarfile.TarInfo('etc/config/wireless')
                member.mode = 0o600
                member.size = len(current)
                archive.addfile(member, io.BytesIO(current))
            binary = root / 'bin'
            binary.mkdir()
            fake_ssh = binary / 'ssh'
            fake_ssh.write_text('''#!/usr/bin/env python3
import os, pathlib, sys
if sys.argv[-1].startswith('tar '):
    sys.stdout.buffer.write(pathlib.Path(os.environ['ROUTER_ARCHIVE']).read_bytes())
else:
    pathlib.Path(os.environ['UPLOAD_ARCHIVE']).write_bytes(sys.stdin.buffer.read())
''')
            fake_ssh.chmod(0o700)
            upload = root / 'upload.tar.gz'
            env = dict(os.environ, PATH=str(binary) + ':' + os.environ['PATH'],
                       ROUTER_ARCHIVE=str(archive_path), UPLOAD_ARCHIVE=str(upload))
            def run(script):
                return subprocess.run([str(ROOT / script), '--name', 'upstairs', 'router', str(repo)],
                                      env=env, capture_output=True)
            result = run('openwrt_pullconfig')
            self.assertEqual(result.returncode, 0, result.stderr)
            local = repo / 'upstairs/etc/config/wireless'
            redacted = local.read_bytes()
            self.assertIn(b'[REDACTED]', redacted)
            self.assertNotIn(b'router-secret', redacted)
            edited = redacted.replace(b"ssid 'Old'", b"ssid 'New'")
            local.write_bytes(edited)
            result = run('openwrt_pushconfig')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(local.read_bytes(), edited)
            with tarfile.open(upload) as archive:
                self.assertEqual(archive.extractfile('old/wireless').read(), current)
                new = archive.extractfile('new/wireless').read()
                self.assertIn(b'router-secret', new)
                self.assertIn(b"ssid 'New'", new)
                self.assertNotIn(b'[REDACTED]', new)
            upload.unlink()
            local.write_bytes(edited.replace(b"'main'", b"'missing'"))
            result = run('openwrt_pushconfig')
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(upload.exists())
            self.assertNotIn(b'router-secret', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
