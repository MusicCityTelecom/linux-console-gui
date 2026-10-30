import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import io
import json
import socket
import ssl
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QSettings
from network_core import TelnetCodec, Session, Task, fingerprint, ssh_connect
from network_ui import FileBrowser, Notepad
from updates import version_tuple, latest_release, download_release, extract_source
import paramiko
APP=QApplication.instance() or QApplication([])


def pump_until(predicate,timeout=8):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        APP.processEvents()
        if predicate(): return True
        time.sleep(.01)
    return False


class NetworkTests(unittest.TestCase):
    def test_telnet_fragmented_negotiation_and_iac(self):
        c=TelnetCodec()
        self.assertEqual(c.feed(b'hello\xff'),(b'hello',b''))
        self.assertEqual(c.feed(b'\xfb\x01'),(b'',b'\xff\xfd\x01'))
        self.assertEqual(c.feed(b'\xff\xfd\x18'),(b'',b'\xff\xfb\x18'))
        self.assertEqual(c.feed(b'\xff\xfa\x18\x01\xff\xf0'),(b'',b'\xff\xfa\x18\x00VT100\xff\xf0'))
        self.assertEqual(c.feed(b'\xff\xff'),(b'\xff',b''))
        self.assertEqual(c.encode(b'\x01\xff'),b'\x01\xff\xff')
        self.assertEqual(c.feed(b'\xff\xfb\x63'),(b'',b'\xff\xfe\x63'))

    def test_telnet_live_connection(self):
        listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen()
        captured=[]
        def serve():
            with listener.accept()[0] as c:
                c.settimeout(5); c.sendall(b'\xff\xfb\x01Welcome\r\n')
                data=b''
                while b'test\r' not in data: data+=c.recv(100)
                captured.append(data); c.sendall(b'OK\r\n')
            listener.close()
        server=threading.Thread(target=serve,daemon=True); server.start()
        w=Session(dict(protocol='Telnet',host='127.0.0.1',port=listener.getsockname()[1])); received=[]; errors=[]
        w.received.connect(received.append); w.failed.connect(errors.append); w.start()
        self.assertTrue(pump_until(lambda:b'Welcome' in b''.join(received)))
        w.send(b'test\r'); self.assertTrue(pump_until(lambda:b'OK' in b''.join(received)))
        self.assertTrue(w.wait(3000)); self.assertFalse(errors)
        self.assertIn(b'\xff\xfd\x01',captured[0])

    def test_tls_telnet_connection(self):
        with tempfile.TemporaryDirectory() as d:
            cert=Path(d)/'cert.pem'; key=Path(d)/'key.pem'
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes,serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from datetime import datetime,timedelta,timezone
            private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
            certificate=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1)).not_valid_after(datetime.now(timezone.utc)+timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False)
                .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(private,hashes.SHA256()))
            key.write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption()))
            cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(cert,key)
            listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen()
            def serve():
                with listener.accept()[0] as c:
                    with ctx.wrap_socket(c,server_side=True) as s: s.sendall(b'TLS controller prompt\r\n'); time.sleep(.3)
                listener.close()
            threading.Thread(target=serve,daemon=True).start()
            w=Session(dict(protocol='Mitel TLS Telnet',host='localhost',port=listener.getsockname()[1],ca=str(cert)))
            received=[]; errors=[]; w.received.connect(received.append); w.failed.connect(errors.append); w.start()
            self.assertTrue(pump_until(lambda:b'TLS controller' in b''.join(received))); w.stop(); self.assertTrue(w.wait(3000)); self.assertFalse(errors)

    @unittest.skipIf(os.name=='nt','Windows shell has its own ConPTY test')
    def test_local_shell_and_interrupt(self):
        w=Session(dict(protocol='Local terminal')); data=[]; errors=[]
        w.received.connect(data.append); w.failed.connect(errors.append); w.start()
        try:
            w.send(b"printf 'LOCAL_OK_739\\n'\r")
            self.assertTrue(pump_until(lambda:b'LOCAL_OK_739\r\n' in b''.join(data)))
            self.assertNotIn(b'no job control',b''.join(data))
        finally: w.stop(); self.assertTrue(w.wait(4000))
        self.assertFalse(errors)

    def test_ssh_host_verification_and_shell(self):
        key=paramiko.RSAKey.generate(2048)
        listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen()
        port=listener.getsockname()[1]
        class Server(paramiko.ServerInterface):
            def check_auth_password(self,user,password): return paramiko.AUTH_SUCCESSFUL if (user,password)==('tester','secret') else paramiko.AUTH_FAILED
            def get_allowed_auths(self,user): return 'password'
            def check_channel_request(self,kind,channel): return paramiko.OPEN_SUCCEEDED
            def check_channel_pty_request(self,*args): return True
            def check_channel_shell_request(self,*args): return True
        def serve():
            try:
                with listener.accept()[0] as sock:
                    transport=paramiko.Transport(sock); transport.add_server_key(key); transport.start_server(server=Server())
                    channel=transport.accept(5)
                    if channel:
                        channel.send(b'SSH_READY\r\n')
                        channel.settimeout(5)
                        if channel.recv(100)==b'hello\r': channel.send(b'SSH_REPLY\r\n')
                        time.sleep(.2); channel.close()
                    transport.close()
            finally: listener.close()
        threading.Thread(target=serve,daemon=True).start()
        with tempfile.TemporaryDirectory() as d, patch('network_core.CONFIG',Path(d)):
            worker=Session(dict(protocol='SSH',host='127.0.0.1',port=port,user='tester',password='secret'))
            received=[]; errors=[]; questions=[]
            worker.received.connect(received.append); worker.failed.connect(errors.append)
            def trust(title,text): questions.append(text); worker.respond(True)
            worker.question.connect(trust); worker.start()
            try:
                self.assertTrue(pump_until(lambda:b'SSH_READY' in b''.join(received)))
                worker.send(b'hello\r'); self.assertTrue(pump_until(lambda:b'SSH_REPLY' in b''.join(received)))
                self.assertTrue((Path(d)/'known_hosts').exists()); self.assertIn(fingerprint(key),questions[0])
            finally: worker.stop(); worker.wait(5000)
            self.assertFalse(errors)

    def test_source_archive_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            archive=Path(d)/'bad.tar.gz'
            with tarfile.open(archive,'w:gz') as t:
                member=tarfile.TarInfo('../outside'); member.size=1; t.addfile(member,io.BytesIO(b'x'))
            with self.assertRaises(ValueError): extract_source(archive,Path(d)/'out')

    def test_update_version_and_checksum(self):
        self.assertGreater(version_tuple('v1.10.0'),version_tuple('1.2.0'))
        with self.assertRaises(ValueError): version_tuple('v1.2.0-rc1')
        with patch('updates.get_url',return_value=json.dumps(dict(tag_name='v0.9.0')).encode()): self.assertIsNone(latest_release())
        release=dict(tag_name='v1.0.1',assets=[dict(name='SHA256SUMS',browser_download_url='sums'),dict(name='serial-desk_1.0.1_all.deb',browser_download_url='deb')])
        with tempfile.TemporaryDirectory() as d, patch('updates.get_url',side_effect=[('0'*64+'  serial-desk_1.0.1_all.deb\n').encode(),b'tampered']):
            with self.assertRaises(ValueError): download_release(release,'deb',d)

    def test_file_browser_sftp_listing_and_transfer(self):
        class FakeSFTP:
            files={'/config.txt':b'configuration'}
            def normalize(self,p): return '/'
            def listdir_attr(self,p): return [type('Attr',(),dict(filename='config.txt',st_size=13,st_mode=stat_mode))()]
            def get(self,remote,local,callback): Path(local).write_bytes(self.files[remote]); callback(13,13)
            def put(self,local,remote,callback): self.files[remote]=Path(local).read_bytes(); callback(13,13)
            def close(self): pass
        stat_mode=0o100644
        w=FileBrowser(); w.remote=FakeSFTP(); w.client=type('Client',(),{'close':lambda s:None})(); w.protocol='SFTP'
        w.refresh(); self.assertTrue(pump_until(lambda:w.table.rowCount()==1)); self.assertEqual(w.table.item(0,0).text(),'config.txt')
        self.assertTrue(pump_until(lambda:not w.task.isRunning()))
        with tempfile.TemporaryDirectory() as d:
            local=Path(d)/'download'; w.transfer(str(local),'/config.txt',False)
            self.assertTrue(pump_until(lambda:local.exists() and not w.task.isRunning())); self.assertEqual(local.read_bytes(),b'configuration')
        w.shutdown()

    def test_notepad_persistence(self):
        with tempfile.TemporaryDirectory() as d, patch('network_ui.CONFIG',Path(d)):
            w=Notepad(); w.text.setPlainText('site notes • 東京'); w.shutdown()
            other=Notepad(); self.assertEqual(other.text.toPlainText(),'site notes • 東京'); other.shutdown()

    def test_responsive_window(self):
        from console_gui import Window
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,XDG_CONFIG_HOME=d),patch('network_ui.CONFIG',Path(d)),patch('console_gui.UpdateController'):
            w=Window(); w.updates.task=None; w.show(); w.resize(800,600); APP.processEvents()
            self.assertLessEqual(w.width(),800); self.assertLessEqual(w.height(),600)
            self.assertEqual(w.tabs.count(),5)
            w.showMaximized(); APP.processEvents(); self.assertTrue(w.isMaximized()); w.close()


if __name__=='__main__': unittest.main(verbosity=2)
