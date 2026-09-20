"""Exercise deployment file ordering with Docker/curl isolated from the host."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'release' / 'scripts'


class DeploymentScriptsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = self.root / 'host'
        self.package = self.root / 'package'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for base in (self.host, self.package):
            for folder in ('app', 'frontend', 'config', 'scripts', 'backups'):
                (base / folder).mkdir(parents=True)
            (base / 'app' / 'version').write_text(base.name)
            (base / 'frontend' / 'version').write_text(base.name)
            (base / 'requirements.txt').write_text(base.name)
        (self.host / 'docker-compose.yml').write_text('environment:\n  - FINANCE_DB_PATH=/app/config/finance.db\n')
        self.db = self.host / 'config' / 'finance.db'
        self.db.write_text('running-db')
        for suffix in ('-wal', '-shm'):
            Path(str(self.db) + suffix).write_text('running' + suffix)
        for name in ('deploy.sh', 'rollback.sh'):
            shutil.copy2(SCRIPTS / name, self.package / 'scripts' / name)
        # Stopping changes the mock DB/WAL: the snapshot must capture this state,
        # proving it happens after Docker stop, not while the writer is live.
        (self.bin / 'docker').write_text('''#!/bin/bash
set -eu
case "$1" in
 stop)
   printf stopped-db > "$HOST_ROOT/config/finance.db"
   printf stopped-wal > "$HOST_ROOT/config/finance.db-wal"
   printf stopped-shm > "$HOST_ROOT/config/finance.db-shm" ;;
 cp) mkdir -p "$3" ;;
esac
''')
        (self.bin / 'curl').write_text('#!/bin/bash\nexit 0\n')
        for path in self.bin.iterdir():
            path.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'], HOST_ROOT=str(self.host))

    def run_script(self, name, *args):
        return subprocess.run(['bash', str(self.package / 'scripts' / name), *map(str, args)], env=self.env, capture_output=True, text=True, check=True)

    def test_deploy_snapshots_stopped_database_and_sidecars(self):
        self.run_script('deploy.sh')
        backup, = (self.host / 'backups').iterdir()
        self.assertEqual((backup / 'finance.db').read_text(), 'stopped-db')
        self.assertEqual((backup / 'finance.db-wal').read_text(), 'stopped-wal')
        self.assertEqual((backup / 'finance.db-shm').read_text(), 'stopped-shm')
        self.assertEqual((backup / 'build-context-app' / 'version').read_text(), 'host')
        self.assertEqual((self.host / 'app' / 'version').read_text(), 'package')

    def test_rollback_preserves_current_state_and_replaces_or_removes_sidecars(self):
        for has_sidecars in (False, True):
            with self.subTest(has_sidecars=has_sidecars):
                backup = self.host / 'backups' / ('old-' + str(has_sidecars))
                backup.mkdir()
                for name in ('app', 'frontend'):
                    shutil.copytree(self.package / name, backup / ('build-context-' + name))
                (backup / 'requirements.txt').write_text('old')
                (backup / 'finance.db').write_text('old-db')
                if has_sidecars:
                    (backup / 'finance.db-wal').write_text('old-wal')
                    (backup / 'finance.db-shm').write_text('old-shm')
                previous = set((self.host / 'backups').glob('pre-rollback-*'))
                self.run_script('rollback.sh', backup)
                recovery, = set((self.host / 'backups').glob('pre-rollback-*')) - previous
                self.assertEqual((recovery / 'finance.db').read_text(), 'stopped-db')
                self.assertEqual((recovery / 'finance.db-wal').read_text(), 'stopped-wal')
                self.assertEqual((recovery / 'finance.db-shm').read_text(), 'stopped-shm')
                self.assertEqual(self.db.read_text(), 'old-db')
                for suffix in ('-wal', '-shm'):
                    sidecar = Path(str(self.db) + suffix)
                    if has_sidecars:
                        self.assertEqual(sidecar.read_text(), 'old' + suffix)
                    else:
                        self.assertFalse(sidecar.exists())
