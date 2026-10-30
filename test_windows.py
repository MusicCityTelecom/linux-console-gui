import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from platform_support import config_directory,protect_local_key,unprotect_local_key,exclusive_file_lock
from updates import latest_release,version_tuple,extract_windows_source
import json
import zipfile
from test_network import APP,pump_until


class PlatformTests(unittest.TestCase):
    def test_config_override_and_lock(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,XDG_CONFIG_HOME=d):
            self.assertEqual(config_directory(),Path(d)/'serial-desk')
            with exclusive_file_lock(Path(d)/'test.lock'):pass
    def test_windows_update_channel(self):
        releases=[dict(tag_name='v9.0.0'),dict(tag_name='windows-v9.1.0'),dict(tag_name='windows-v9.2.0',prerelease=True)]
        with patch('updates.get_url',return_value=json.dumps(releases).encode()):
            self.assertEqual(latest_release(windows=True)['tag_name'],'windows-v9.1.0')
        self.assertEqual(version_tuple('windows-v1.2.0'),(1,2,0))
    def test_windows_archive_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'source.zip'
            with zipfile.ZipFile(path,'w') as z:z.writestr('../bad.txt','x')
            with self.assertRaises(ValueError):extract_windows_source(path,Path(d)/'out')
    @unittest.skipUnless(os.name=='nt','Actual Windows account protection')
    def test_dpapi_roundtrip(self):
        key=os.urandom(32);protected=protect_local_key(key)
        self.assertTrue(protected.startswith(b'DPAPI1'));self.assertNotEqual(protected,key)
        self.assertEqual(unprotect_local_key(protected),key)
    @unittest.skipUnless(os.name=='nt','Actual Windows pseudo-terminal')
    def test_windows_terminal(self):
        from network_core import Session
        worker=Session(dict(protocol='Local terminal'));received=[];errors=[]
        worker.received.connect(received.append);worker.failed.connect(errors.append);worker.start()
        try:
            worker.send(b'echo WINDOWS_TERMINAL_OK_7^39\r')
            self.assertTrue(pump_until(lambda:b'WINDOWS_TERMINAL_OK_739' in b''.join(received),timeout=20),msg=repr(b''.join(received))+repr(errors))
        finally:worker.stop();self.assertTrue(worker.wait(5000))
        self.assertFalse(errors)


if __name__=='__main__':unittest.main(verbosity=2)
