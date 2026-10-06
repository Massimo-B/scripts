# BEGIN SCRIPT VERSION
# Source version: 2026-10-06 (Git 2287820)
if __name__ == '__main__':
    import sys
    if sys.argv[1:2] == ['--version']:
        print('test_versions.py 2026-10-06 (Git 2287820)')
        sys.exit(0)
# END SCRIPT VERSION
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which('bash')


class VersionTests(unittest.TestCase):
    def test_standalone_versions_without_dependencies(self):
        scripts = [path for path in ROOT.iterdir() if path.is_file()
                   and (path.read_bytes().startswith(b'#!') or path.name.startswith('lib_'))]
        scripts.extend((ROOT / 'tests').glob('*.py'))
        with tempfile.TemporaryDirectory() as temp:
            for path in scripts:
                with self.subTest(script=path.name):
                    copy = Path(temp) / path.name
                    shutil.copy2(path, copy)
                    interpreter = sys.executable if path.suffix == '.py' or path.name == 'openwrt_pushconfig' else BASH
                    result = subprocess.run([interpreter, str(copy), '--version'], cwd=temp,
                                            env=dict(os.environ, PATH=''), capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, '')
                    self.assertRegex(result.stdout, rf'^{re.escape(path.name)} \d{{4}}-\d{{2}}-\d{{2}} '
                                     r'\(Git [0-9a-f]+(?:\+(?:dirty|uncommitted))?\)\n$')

    def test_sourced_library_does_not_handle_callers_version_option(self):
        result = subprocess.run([BASH, '-c', 'source "$1" --version; echo still-running',
                                 'test', str(ROOT / 'lib_colors')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'still-running\n')

    def test_updater_stamps_git_date_detects_edits_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            updater = repo / 'update_versions'
            shutil.copy2(ROOT / 'update_versions', updater)
            script = repo / 'example'
            script.write_text('#!/usr/bin/env bash\necho work\n')
            script.chmod(0o750)
            env = dict(os.environ, GIT_AUTHOR_NAME='Version Test', GIT_COMMITTER_NAME='Version Test',
                       GIT_AUTHOR_EMAIL='test@example.invalid', GIT_COMMITTER_EMAIL='test@example.invalid',
                       GIT_AUTHOR_DATE='2024-01-02T12:00:00+00:00', GIT_COMMITTER_DATE='2024-01-02T12:00:00+00:00')

            def run(*args):
                return subprocess.run(args, cwd=repo, env=env, check=True, capture_output=True, text=True)

            run('git', 'init', '-q')
            run('git', 'add', '.')
            run('git', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'Initial source')
            revision = run('git', 'rev-parse', '--short', 'HEAD').stdout.strip()
            run(BASH, str(updater))
            self.assertEqual(run(BASH, str(script), '--version').stdout,
                             f'example 2024-01-02 (Git {revision})\n')
            self.assertEqual(script.stat().st_mode & 0o777, 0o750)
            self.assertEqual(run(BASH, str(updater)).stdout, '')
            self.assertEqual(run(BASH, str(script)).stdout, 'work\n')
            script.write_text(script.read_text().replace('echo work', 'echo changed'))
            new = repo / 'new_script'
            new.write_text('#!/usr/bin/env bash\necho new\n')
            run(BASH, str(updater))
            self.assertIn(f'{revision}+dirty', run(BASH, str(script), '--version').stdout)
            self.assertIn(f'{revision}+uncommitted', run(BASH, str(new), '--version').stdout)
            self.assertEqual(run(BASH, str(updater)).stdout, '')
            run('git', 'add', '.')
            run('git', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'Changed source')
            revision = run('git', 'rev-parse', '--short', 'HEAD').stdout.strip()
            run(BASH, str(updater))
            self.assertEqual(run(BASH, str(script), '--version').stdout,
                             f'example 2024-01-02 (Git {revision})\n')


if __name__ == '__main__':
    unittest.main()
