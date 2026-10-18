import tempfile
import subprocess
import sys
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from docker_start import database_path, prepare_database, verify_write_access

class DockerBootstrapTests(unittest.TestCase):
    def test_default_path(self):
        self.assertEqual(str(database_path('')),'/app/data/team.sqlite3')

    def test_creates_missing_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'data'/'team.sqlite3'
            prepare_database(path,root=False)
            verify_write_access(path)
            self.assertTrue(path.parent.is_dir())
            self.assertFalse(path.exists())

    def test_existing_database_not_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'data'/'team.sqlite3'
            path.parent.mkdir()
            path.write_bytes(b'existing database content')
            prepare_database(path,root=False)
            verify_write_access(path)
            self.assertEqual(path.read_bytes(),b'existing database content')

    def test_repairs_only_database_and_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'data'/'team.sqlite3'
            path.parent.mkdir()
            for name in (str(path),str(path)+'-wal',str(path)+'-shm'):
                Path(name).write_bytes(b'data')
            with patch('docker_start.os.chown') as chown:
                prepare_database(path,root=True)
                self.assertEqual(chown.call_count,4)
                self.assertEqual({str(c.args[0]) for c in chown.call_args_list},
                                 {str(path.parent),str(path),str(path)+'-wal',str(path)+'-shm'})

    def test_rejects_directory_instead_of_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'team.sqlite3'
            path.mkdir()
            with self.assertRaises(ValueError):
                prepare_database(path,root=False)

    def test_bootstrap_executes_command_with_database_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'data'/'team.sqlite3'
            env=dict(os.environ, BOT_TOKEN='TEST_ONLY', OWNER_ID='123', DB_PATH=str(path))
            script=Path(__file__).resolve().parents[1]/'docker_start.py'
            command=[sys.executable,str(script),sys.executable,'-c',
                     "import os,sqlite3; c=sqlite3.connect(os.environ['DB_PATH']); c.execute('CREATE TABLE ok(id INTEGER)'); c.close()"]
            result=subprocess.run(command,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(path.is_file())

    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'team.sqlite3'
            path.symlink_to(Path(tmp)/'other')
            with self.assertRaises(ValueError):
                prepare_database(path,root=False)

if __name__=='__main__':
    unittest.main()
