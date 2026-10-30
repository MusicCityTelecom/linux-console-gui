import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import io
import json
import shlex
import socket
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import paramiko
from PyQt5.QtCore import QMimeData,QUrl,Qt
from PyQt5.QtGui import QDropEvent,QDragEnterEvent
from PyQt5.QtWidgets import QApplication,QMessageBox
from credential_vault import CredentialVault,VaultError
from network_core import SharedSSH,ssh_connect,private_key_from_text
from network_ui import FileBrowser,Credentials
from profile_ui import ProfileBar,unlock_vault
from test_network import pump_until,APP


class VaultTests(unittest.TestCase):
    def test_master_password_wrong_password_and_change(self):
        with tempfile.TemporaryDirectory() as d:
            v=CredentialVault(d); v.save_profile('router',dict(password='SECRET_LOGIN',key_data='SECRET_KEY'))
            self.assertNotIn('SECRET',v.path.read_text())
            if os.name!='nt': self.assertEqual(v.path.stat().st_mode&0o777,0o600)
            v.set_master_password('old-password'); self.assertFalse(v.local_key_path.exists())
            other=CredentialVault(d); self.assertTrue(other.locked)
            with self.assertRaises(VaultError): other.unlock('incorrect')
            self.assertTrue(other.locked); other.unlock('old-password'); self.assertEqual(other.profiles['router']['password'],'SECRET_LOGIN')
            other.set_master_password('new-password'); other.lock()
            with self.assertRaises(VaultError): other.unlock('old-password')
            other.unlock('new-password'); other.remove_master_password('new-password')
            self.assertEqual(CredentialVault(d).profiles['router']['key_data'],'SECRET_KEY')
    def test_tamper_and_conflicting_writers(self):
        with tempfile.TemporaryDirectory() as d:
            v=CredentialVault(d); v.save_profile('a',{})
            stale=CredentialVault(d); v.save_profile('b',{})
            with self.assertRaises(VaultError): stale.save_profile('c',{})
            data=json.loads(v.path.read_text()); data['ciphertext']=data['ciphertext'][::-1]; v.path.write_text(json.dumps(data))
            with self.assertRaises(VaultError): CredentialVault(d)
    def test_encrypted_private_key_roundtrip(self):
        key=paramiko.RSAKey.generate(2048); out=io.StringIO(); key.write_private_key(out,password='key-pass')
        self.assertEqual(private_key_from_text(out.getvalue(),'key-pass').asbytes(),key.asbytes())
        with self.assertRaises(ValueError): private_key_from_text(out.getvalue(),'wrong')
    def test_profile_selection_only_autoconnects_on_user_activation(self):
        with tempfile.TemporaryDirectory() as d:
            vault=CredentialVault(d); vault.save_profile('router',dict(protocol='SSH',host='router.test',port=2222,user='admin',password='saved',auto_connect=True,remember=True))
            fields=Credentials(['SSH']); calls=[]; bar=ProfileBar(fields,vault,lambda:calls.append(fields.values()))
            bar.refresh(); APP.processEvents(); self.assertFalse(calls)
            bar.combo.setCurrentText('router'); bar.combo.activated[str].emit('router')
            self.assertTrue(pump_until(lambda:len(calls)==1)); self.assertEqual(calls[0]['password'],'saved');self.assertEqual(calls[0]['port'],2222)
            self.assertNotIn('saved',vault.path.read_text())
    def test_startup_unlock_cancel_and_password(self):
        with tempfile.TemporaryDirectory() as d:
            vault=CredentialVault(d); vault.set_master_password('master-password');vault.lock()
            with patch('profile_ui.QInputDialog.getText',return_value=('',False)):
                self.assertFalse(unlock_vault(vault)); self.assertTrue(vault.locked)
            with patch('profile_ui.QInputDialog.getText',return_value=('master-password',True)):
                self.assertTrue(unlock_vault(vault));self.assertFalse(vault.locked)


class LocalSSHServer:
    """Real SSH/SFTP/SCP test peer rooted in a temporary directory."""
    def __init__(self,root):
        self.root=Path(root); self.key=paramiko.RSAKey.generate(2048);self.stop=threading.Event();self.transport=None;self.logins=0
        self.listener=socket.socket();self.listener.bind(('127.0.0.1',0));self.listener.listen();self.port=self.listener.getsockname()[1]
        self.thread=threading.Thread(target=self.serve,daemon=True);self.thread.start()
    def serve(self):
        outer=self
        class SFTP(paramiko.SFTPServerInterface):
            def local(self,path):
                p=(outer.root/path.lstrip('/')).resolve()
                if not p.is_relative_to(outer.root.resolve()):raise OSError('Outside test root')
                return p
            def canonicalize(self,path): return '/'+str(self.local(path).relative_to(outer.root)) if path!='.' else '/'
            def list_folder(self,path):
                result=[]
                for p in self.local(path).iterdir():
                    a=paramiko.SFTPAttributes.from_stat(p.stat());a.filename=p.name;result.append(a)
                return result
            def stat(self,path):
                try:return paramiko.SFTPAttributes.from_stat(self.local(path).stat())
                except OSError as e:return paramiko.SFTPServer.convert_errno(e.errno or 2)
            lstat=stat
            def open(self,path,flags,attr):
                try:
                    fd=os.open(self.local(path),flags|getattr(os,'O_BINARY',0),0o600); mode='r+b' if flags&os.O_RDWR else 'wb' if flags&os.O_WRONLY else 'rb'
                    f=os.fdopen(fd,mode);handle=paramiko.SFTPHandle(flags);handle.readfile=f;handle.writefile=f;return handle
                except OSError as e:return paramiko.SFTPServer.convert_errno(e.errno or 2)
        class Server(paramiko.ServerInterface):
            def check_auth_password(self,user,password):outer.logins+=1;return paramiko.AUTH_SUCCESSFUL
            def get_allowed_auths(self,user):return 'password'
            def check_channel_request(self,kind,channel):return paramiko.OPEN_SUCCEEDED
            def check_channel_exec_request(self,channel,command):
                threading.Thread(target=outer.scp,args=(channel,command),daemon=True).start();return True
        try:
            sock=self.listener.accept()[0];self.transport=paramiko.Transport(sock);self.transport.add_server_key(self.key)
            self.transport.set_subsystem_handler('sftp',paramiko.SFTPServer,SFTP)
            self.transport.start_server(server=Server())
            while not self.stop.wait(.02) and self.transport.is_active(): pass
        finally:
            if self.transport:self.transport.close()
            self.listener.close()
    def scp(self,channel,command):
        def line():
            data=b''
            while not data.endswith(b'\n'):
                b=channel.recv(1)
                if not b: raise EOFError()
                data+=b
            return data
        try:
            args=shlex.split(command.decode()); target=(self.root/args[-1].lstrip('/')).resolve()
            if not target.is_relative_to(self.root.resolve()): raise ValueError()
            channel.settimeout(5)
            if '-t' in args:
                channel.sendall(b'\0'); header=line(); _,size,name=header.decode().strip().split(' ',2);channel.sendall(b'\0')
                size=int(size);data=b''
                while len(data)<size:data+=channel.recv(size-len(data))
                channel.recv(1);target.write_bytes(data);channel.sendall(b'\0')
            else:
                channel.recv(1);data=target.read_bytes();channel.sendall(f'C0644 {len(data)} {target.name}\n'.encode());channel.recv(1)
                channel.sendall(data+b'\0');channel.recv(1)
            channel.send_exit_status(0)
        finally: channel.close()
    def close(self):
        self.stop.set()
        if self.transport:self.transport.close()
        self.thread.join(timeout=3)


class SessionFeatureTests(unittest.TestCase):
    def test_one_authenticated_connection_sftp_and_scp_upload_download(self):
        with tempfile.TemporaryDirectory() as d,patch('network_core.CONFIG',Path(d)/'config'):
            root=Path(d)/'server';root.mkdir();server=LocalSSHServer(root);browsers=[]
            client=ssh_connect(dict(host='127.0.0.1',port=server.port,user='tester',password='password'),lambda *a:True)
            shared=SharedSSH(client);settings=dict(host='127.0.0.1',port=server.port,user='tester')
            errors=[]
            try:
                with patch('network_ui.QMessageBox.warning',side_effect=lambda *args:errors.append(args[-1])):
                    for protocol in ('SFTP','SCP'):
                        browser=FileBrowser(CredentialVault(Path(d)/protocol));browsers.append(browser);browser.attach_ssh(shared,protocol,settings)
                        self.assertTrue(pump_until(lambda:browser.remote is not None and not browser.task.isRunning()))
                        self.assertTrue(pump_until(lambda:browser.path.text()=='/' and not browser.task.isRunning()))
                        payload=b'config\x00\xff\r\n'*64;source=Path(d)/f'{protocol}-upload.bin';source.write_bytes(payload)
                        remote=f'/{protocol}.bin';browser.transfer(str(source),remote,True)
                        self.assertTrue(pump_until(lambda:(root/f'{protocol}.bin').exists() and not browser.task.isRunning()))
                        self.assertEqual((root/f'{protocol}.bin').read_bytes(),payload)
                        dest=Path(d)/f'{protocol}-download.bin';browser.transfer(str(dest),remote,False)
                        self.assertTrue(pump_until(lambda:dest.exists() and not browser.task.isRunning()));self.assertEqual(dest.read_bytes(),payload)
                    a=Path(d)/'batch-a';b=Path(d)/'batch-b';a.write_bytes(b'A');b.write_bytes(b'B')
                    browsers[0].run_transfers([(str(a),'/a',True),(str(b),'/b',True)])
                    self.assertTrue(pump_until(lambda:(root/'b').exists() and not browsers[0].pending_transfers and not browsers[0].task.isRunning()))
                    self.assertEqual((root/'a').read_bytes(),b'A');self.assertEqual((root/'b').read_bytes(),b'B')
                    self.assertEqual(server.logins,1)
                    shared.release() # Shell tab can close while file tabs keep the transport alive.
                    self.assertTrue(client.get_transport().is_active())
                    browsers[0].shutdown();self.assertTrue(client.get_transport().is_active())
                    browsers[1].shutdown();self.assertFalse(client.get_transport())
                    self.assertFalse(errors)
            finally:
                for browser in browsers:browser.shutdown()
                shared.release();server.close()
    def test_drop_upload_accepts_only_local_files(self):
        from PyQt5.QtCore import QPoint,QPointF
        with tempfile.TemporaryDirectory() as d:
            browser=FileBrowser(CredentialVault(d));browser.remote=object();calls=[]
            browser.table.files_dropped.connect(calls.append)
            mime=QMimeData();mime.setUrls([QUrl.fromLocalFile('/tmp/test-upload.txt')])
            enter=QDragEnterEvent(QPoint(1,1),Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier)
            browser.table.dragEnterEvent(enter);self.assertTrue(enter.isAccepted())
            drop=QDropEvent(QPointF(1,1),Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier)
            with patch.object(browser,'upload_paths') as upload:
                # Replace the original slot to avoid a dialog while checking routing.
                browser.table.files_dropped.disconnect();browser.table.files_dropped.connect(upload)
                browser.table.dropEvent(drop);upload.assert_called_once_with(['/tmp/test-upload.txt'])
            self.assertTrue(drop.isAccepted())
            mime.setUrls([QUrl('https://example.com/file')])
            remote=QDragEnterEvent(QPoint(1,1),Qt.CopyAction,mime,Qt.LeftButton,Qt.NoModifier)
            browser.table.dragEnterEvent(remote);self.assertFalse(remote.isAccepted())
            browser.remote=None;browser.shutdown()

    def test_independent_tabs_duplicate_rename_and_close(self):
        from console_gui import Window
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,XDG_CONFIG_HOME=d),patch('network_ui.CONFIG',Path(d)),patch('console_gui.UpdateController'):
            w=Window();w.updates.task=None
            self.assertTrue(w.tabs.tabsClosable());self.assertTrue(w.tabs.isMovable())
            new=w.new_network();self.assertEqual(w.tabs.count(),6)
            new.credentials.host.setText('router.example');w.duplicate_session(new);self.assertEqual(w.tabs.count(),7)
            self.assertEqual(w.tabs.currentWidget().credentials.host.text(),'router.example')
            w.close_tab(w.tabs.currentIndex());self.assertEqual(w.tabs.count(),6)
            w.new_serial();self.assertEqual(w.tabs.count(),7)
            w.close()


if __name__=='__main__':unittest.main(verbosity=2)
