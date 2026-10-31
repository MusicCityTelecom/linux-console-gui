import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import ftplib
import io
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
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QWidget,QVBoxLayout,QLineEdit,QMessageBox
from console_gui import Terminal,Window
from credential_vault import CredentialVault
from file_editing import decode_text,encode_text,read_local,save_local,FileConflict,RemoteTextStore,TextEditor,MAX_TEXT
from local_browser import LocalBrowser,quoted_path
from network_core import Session,SharedSSH,ssh_connect
from network_ui import FileBrowser
from network_ui import NetworkConsole
from unittest.mock import Mock
from test_network import APP,pump_until
from test_session_features import LocalSSHServer


class TextFileTests(unittest.TestCase):
    def test_encodings_bom_and_line_endings_round_trip(self):
        for encoding in ('UTF-8','UTF-8 BOM','UTF-16 LE','UTF-16 BE','Windows-1252'):
            for newline in ('LF','CRLF','CR'):
                payload=encode_text('café\nlast line',encoding,newline)
                text,actual,ending=decode_text(payload,encoding)
                self.assertEqual(text,'café\nlast line');self.assertEqual(actual,encoding);self.assertEqual(ending,newline);self.assertEqual(encode_text(text,actual,ending),payload)
    def test_binary_size_mixed_and_invalid_encoding_are_rejected(self):
        for data in (b'abc\0def',b'\x01\x02',b'\xff\xff',b'x'*(MAX_TEXT+1)):
            with self.assertRaises(ValueError):decode_text(data)
        text,encoding,newline=decode_text(b'a\r\nb\nc\r');self.assertEqual(newline,'Mixed')
        with self.assertRaises(ValueError):encode_text(text,encoding,newline)
        with self.assertRaises(UnicodeError):encode_text('snowman \u2603','Windows-1252','LF')
    def test_local_atomic_save_and_conflicts(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'file.txt';path.write_bytes(b'old\r\n');path.chmod(0o640);snapshot=read_local(path)
            current=save_local(snapshot,b'new\r\n');self.assertEqual(path.read_bytes(),b'new\r\n')
            if os.name!='nt':self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o640)
            path.write_bytes(b'external change')
            with self.assertRaises(FileConflict):save_local(current,b'overwrite')
            self.assertEqual(path.read_bytes(),b'external change');self.assertEqual(list(Path(d).glob('.serial-desk-edit-*')),[])
    def test_local_failed_replace_preserves_original(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'file.txt';path.write_bytes(b'original');snapshot=read_local(path)
            with patch('file_editing.os.replace',side_effect=OSError('injected failure')):
                with self.assertRaises(OSError):save_local(snapshot,b'edited')
            self.assertEqual(path.read_bytes(),b'original');self.assertEqual(list(Path(d).glob('.serial-desk-edit-*')),[])
    @unittest.skipIf(os.name=='nt','POSIX symlinks and hard links')
    def test_symlink_preserved_and_retarget_detected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);target=root/'target';target.write_bytes(b'old');link=root/'link';link.symlink_to(target)
            snapshot=read_local(link);saved=save_local(snapshot,b'new');self.assertTrue(link.is_symlink());self.assertEqual(target.read_bytes(),b'new')
            other=root/'other';other.write_bytes(b'new');link.unlink();link.symlink_to(other)
            with self.assertRaises(FileConflict):save_local(saved,b'bad')
            hard=root/'hard';os.link(target,hard)
            with self.assertRaises(OSError):save_local(read_local(target),b'bad')
            self.assertEqual(target.read_bytes(),b'new')


class FTPPeer:
    def __init__(self):self.files={'/config.txt':dict(data=b'original\r\n',mode=0o640)};self.fail_promotion=False;self.support_chmod=True;self.expose_mode=True;self.conflicting_target=False
    def sendcmd(self,command):
        if command.startswith('MLST '):
            path=command[5:]
            if path not in self.files:raise ftplib.error_perm('550 Not found')
            entry=self.files[path];mode=f'unix.mode={entry["mode"]:o};' if self.expose_mode else ''
            return f'250-Listing\n type=file;size={len(entry["data"])};{mode}unix.uid=1000;unix.gid=1000; {path}\n250 End'
        if command.startswith('SITE CHMOD '):
            if not self.support_chmod:raise ftplib.error_perm('502 CHMOD unsupported')
            _,_,mode,path=command.split(' ',3);self.files[path]['mode']=int(mode,8);return '200 OK'
        raise ValueError(command)
    def retrbinary(self,command,callback):callback(self.files[command[5:]]['data'])
    def storbinary(self,command,source):
        path=command[5:];self.files.setdefault(path,dict(mode=0o644));self.files[path]['data']=source.read()
    def rename(self,old,new):
        if self.fail_promotion and '.serial-desk-edit-' in old:
            if self.conflicting_target:self.files[new]=dict(data=b'other writer',mode=0o640)
            raise ftplib.error_perm('550 injected promotion failure')
        self.files[new]=self.files.pop(old)
    def delete(self,path):del self.files[path]
    def nlst(self,folder):return list(self.files)
    def close(self):pass


class FTPTextTests(unittest.TestCase):
    def test_staged_save_and_permission_preservation(self):
        peer=FTPPeer();store=RemoteTextStore(peer,False);snapshot=store.read('/config.txt');saved=store.save(snapshot,b'edited\r\n')
        self.assertEqual(peer.files['/config.txt']['data'],b'edited\r\n');self.assertEqual(peer.files['/config.txt']['mode'],0o640);self.assertEqual(list(peer.files),['/config.txt']);self.assertEqual(saved['data'],b'edited\r\n')
    def test_failed_promotion_rolls_back_and_keeps_other_writers(self):
        for conflict in (False,True):
            peer=FTPPeer();store=RemoteTextStore(peer,False);snapshot=store.read('/config.txt');peer.fail_promotion=True;peer.conflicting_target=conflict
            with self.assertRaises((OSError,ftplib.error_perm)):store.save(snapshot,b'edited')
            self.assertEqual(peer.files['/config.txt']['data'],b'other writer' if conflict else b'original\r\n')
            if conflict:self.assertTrue(any(v['data']==b'original\r\n' for k,v in peer.files.items() if 'backup' in k))
            self.assertFalse(any('edit-' in key for key in peer.files))
    def test_unsupported_permissions_never_upload_secret_content(self):
        peer=FTPPeer();store=RemoteTextStore(peer,False);snapshot=store.read('/config.txt');peer.support_chmod=False
        with self.assertRaises(ftplib.error_perm):store.save(snapshot,b'SECRET_NEW_CONTENT')
        self.assertEqual(peer.files['/config.txt']['data'],b'original\r\n');self.assertEqual(len(peer.files),1)
        peer.expose_mode=False;snapshot=store.read('/config.txt');self.assertTrue(snapshot['readonly_reason'])
        with self.assertRaises(OSError):store.save(snapshot,b'edited')


class LocalPaneTests(unittest.TestCase):
    def test_follow_pause_browse_and_settings(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);a=root/'a';b=root/'b';a.mkdir();b.mkdir();pane=LocalBrowser();pane.terminal_changed(str(a));self.assertEqual(pane.directory,str(a))
            pane.navigate(str(b));self.assertFalse(pane.follow.isChecked());pane.terminal_changed(str(root));self.assertEqual(pane.directory,str(b))
            pane.follow.setChecked(True);self.assertEqual(pane.directory,str(root));pane.hidden.setChecked(True)
            other=LocalBrowser();other.apply(pane.settings());self.assertEqual(other.directory,pane.directory);self.assertTrue(other.hidden.isChecked());pane.deleteLater();other.deleteLater();APP.processEvents()
    def test_path_insertion_is_quoted_and_never_sent(self):
        with tempfile.TemporaryDirectory(prefix="console space's ") as d:
            pane=LocalBrowser();pane.navigate(d);commands=[];pane.command_requested.connect(commands.append);pane.insert_path(True)
            self.assertEqual(commands,[('cd /d ' if os.name=='nt' else 'cd -- ')+quoted_path(d)])
            with self.assertRaises(ValueError):quoted_path('bad\npath')
            pane.deleteLater();APP.processEvents()
    def test_unsaved_editor_close_cancel_and_save(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config';path.write_bytes(b'old');pane=LocalBrowser();editor=pane.edit_path(path);editor.text.selectAll();editor.text.insertPlainText('new')
            with patch('file_editing.QMessageBox.warning',return_value=QMessageBox.Cancel):self.assertFalse(pane.prepare_close())
            self.assertEqual(path.read_bytes(),b'old');self.assertTrue(editor.text.document().isModified());editor.save();self.assertEqual(path.read_bytes(),b'new');self.assertTrue(pane.prepare_close());pane.deleteLater();APP.processEvents()
    def test_workspace_restores_local_folders_without_starting_shell(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,XDG_CONFIG_HOME=d),patch('network_ui.CONFIG',Path(d)):
            root=Path(d);folder=root/'browse';folder.mkdir();vault=CredentialVault(root/'vault');w=Window(vault,check_updates=False,restore_workspace=False)
            try:
                w.local.local_browser.terminal_changed(str(root));w.local.local_browser.navigate(str(folder));w.local.local_browser.hidden.setChecked(True);snapshot=w.workspace.snapshot()
                with patch('network_ui.Session') as connect:w.workspace.restore(snapshot,False);connect.assert_not_called()
                self.assertEqual(w.local.local_browser.directory,str(folder));self.assertEqual(w.local.local_browser.terminal_directory,str(root));self.assertFalse(w.local.local_browser.follow.isChecked());self.assertTrue(w.local.local_browser.hidden.isChecked())
            finally:w.workspace.options['save']=False;w.close();w.deleteLater();APP.processEvents()
    def test_live_shell_directory_changes_with_spaces_and_unicode(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'folder café with spaces';folder.mkdir();worker=Session(dict(protocol='Local terminal',local_cwd=d));directories=[];errors=[]
            worker.directory_changed.connect(directories.append);worker.failed.connect(errors.append);worker.start()
            try:
                self.assertTrue(pump_until(lambda:bool(directories)),repr(errors))
                command=('cd /d "'+str(folder)+'"' if os.name=='nt' else 'cd -- '+shlex.quote(str(folder)))+'\r';worker.send(command.encode())
                self.assertTrue(pump_until(lambda:any(os.path.normcase(os.path.normpath(p))==os.path.normcase(str(folder)) for p in directories),timeout=10),repr(directories)+repr(errors))
            finally:worker.stop();self.assertTrue(worker.wait(5000))


class SSHKeyboardTests(unittest.TestCase):
    def test_enter_returns_scrolled_console_to_live_cursor(self):
        window=QWidget();layout=QVBoxLayout(window);terminal=Terminal();layout.addWidget(terminal);window.resize(500,220);window.show();terminal.feed(''.join(f'line {i}\r\n' for i in range(200))+'prompt$ ');terminal.setFocus();APP.processEvents();sent=[];terminal.outgoing.connect(sent.append)
        terminal.verticalScrollBar().setValue(0);self.assertGreater(terminal.verticalScrollBar().maximum(),0);QTest.keyClick(terminal,Qt.Key_Return);APP.processEvents()
        self.assertEqual(sent,[b'\r']);self.assertEqual(terminal.verticalScrollBar().value(),terminal.verticalScrollBar().maximum());self.assertTrue(terminal.hasFocus());window.close();window.deleteLater();APP.processEvents()
    def test_password_button_resolves_link_and_rejects_unrelated_profile(self):
        with tempfile.TemporaryDirectory() as d:
            vault=CredentialVault(d);entry=vault.save_password(dict(name='Device login',user='operator',password='FIRST_PRIVATE_VALUE'))
            vault.save_profile('PBX',dict(protocol='SSH',host='pbx.example',port=2222,user='operator',credential_ref=entry))
            console=NetworkConsole(Terminal,vault=vault);console.profile_bar.combo.setCurrentText('PBX');console.profile_bar.load();console.last_settings=dict(protocol='SSH',host='pbx.example',port=2222,user='operator');worker=Mock();worker.isRunning.return_value=True;console.worker=worker;console.set_state('Connected')
            self.assertTrue(console.password_button.isEnabled());console.paste_saved_password();worker.send_secret.assert_called_once_with(b'FIRST_PRIVATE_VALUE');worker.send.assert_not_called()
            vault.save_password(dict(name='Device login',user='operator',password='UPDATED_PRIVATE_VALUE'),entry);console.paste_saved_password();worker.send_secret.assert_called_with(b'UPDATED_PRIVATE_VALUE')
            console.last_settings['host']='other.example';console.update_password_button();self.assertFalse(console.password_button.isEnabled());worker.send_secret.reset_mock();console.paste_saved_password();worker.send_secret.assert_not_called()
            console.worker=None;console.shutdown();console.deleteLater();APP.processEvents()
    def test_secret_send_on_real_ssh_does_not_submit_or_log_password(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1];key=paramiko.RSAKey.generate(2048);captured=[];errors=[];secret='LONG_PRIVATE_ESCALATION_47!'
        class Server(paramiko.ServerInterface):
            def check_auth_password(self,*args):return paramiko.AUTH_SUCCESSFUL
            def get_allowed_auths(self,*args):return 'password'
            def check_channel_request(self,*args):return paramiko.OPEN_SUCCEEDED
            def check_channel_pty_request(self,*args):return True
            def check_channel_shell_request(self,*args):return True
        def serve():
            try:
                with listener.accept()[0] as sock:
                    transport=paramiko.Transport(sock);transport.add_server_key(key);transport.start_server(server=Server());channel=transport.accept(5);channel.settimeout(5);channel.send(b'PASSWORD_PROMPT');data=b''
                    while len(data)<len(secret):data+=channel.recv(100)
                    captured.append(data);channel.send(b'PASTE_ACK');time.sleep(.2);channel.close();transport.close()
            except Exception as error:errors.append(str(error))
            finally:listener.close()
        thread=threading.Thread(target=serve,daemon=True);thread.start()
        with tempfile.TemporaryDirectory() as d,patch('network_core.CONFIG',Path(d)/'known-hosts'),patch('network_ui.QMessageBox.question',return_value=QMessageBox.Yes):
            vault=CredentialVault(Path(d)/'vault');vault.save_profile('PBX',dict(protocol='SSH',host='127.0.0.1',port=port,user='operator',password=secret))
            console=NetworkConsole(Terminal,vault=vault);console.profile_bar.combo.setCurrentText('PBX');console.profile_bar.load();log=io.StringIO();console.log=log;console.toggle()
            try:
                self.assertTrue(pump_until(lambda:'PASSWORD_PROMPT' in console.terminal.toPlainText()));self.assertTrue(console.password_button.isEnabled());console.paste_saved_password();self.assertTrue(pump_until(lambda:'PASTE_ACK' in console.terminal.toPlainText()))
                self.assertEqual(captured,[secret.encode()]);self.assertNotIn(secret,log.getvalue());self.assertNotIn(secret.encode().hex(' '),log.getvalue());self.assertIn('redacted',log.getvalue())
            finally:console.shutdown();console.deleteLater();thread.join(2);APP.processEvents()
        self.assertFalse(errors)
    def test_tab_and_shift_tab_reach_real_ssh_and_retain_focus(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1];key=paramiko.RSAKey.generate(2048);captured=[];errors=[]
        class Server(paramiko.ServerInterface):
            def check_auth_password(self,*args):return paramiko.AUTH_SUCCESSFUL
            def get_allowed_auths(self,*args):return 'password'
            def check_channel_request(self,*args):return paramiko.OPEN_SUCCEEDED
            def check_channel_pty_request(self,*args):return True
            def check_channel_shell_request(self,*args):return True
        def serve():
            try:
                with listener.accept()[0] as sock:
                    transport=paramiko.Transport(sock);transport.add_server_key(key);transport.start_server(server=Server());channel=transport.accept(5);channel.settimeout(5);channel.send(b'READY\r\n');data=b''
                    while b'\t\x1b[Z' not in data:data+=channel.recv(100)
                    captured.append(data);channel.send(b'TAB_RECEIVED\r\n');time.sleep(.2);channel.close();transport.close()
            except Exception as error:errors.append(str(error))
            finally:listener.close()
        thread=threading.Thread(target=serve,daemon=True);thread.start()
        with tempfile.TemporaryDirectory() as d,patch('network_core.CONFIG',Path(d)):
            worker=Session(dict(protocol='SSH',host='127.0.0.1',port=port,user='test',password='test'));received=[];worker.received.connect(received.append);worker.question.connect(lambda *_:worker.respond(True))
            window=QWidget();layout=QVBoxLayout(window);terminal=Terminal();layout.addWidget(terminal);layout.addWidget(QLineEdit());terminal.outgoing.connect(worker.send);window.show();window.activateWindow();terminal.setFocus();APP.processEvents();worker.start()
            try:
                self.assertTrue(pump_until(lambda:b'READY' in b''.join(received)));QTest.keyClick(terminal,Qt.Key_Tab);QTest.keyClick(terminal,Qt.Key_Backtab,Qt.ShiftModifier)
                self.assertTrue(pump_until(lambda:b'TAB_RECEIVED' in b''.join(received)));self.assertTrue(terminal.hasFocus());self.assertIn(b'\t\x1b[Z',captured[0])
            finally:worker.stop();worker.wait(5000);window.close();window.deleteLater();thread.join(2);APP.processEvents()
        self.assertFalse(errors)


class SFTPTextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.server=LocalSSHServer(self.root);self.patch=patch('network_core.CONFIG',self.root/'known-host-config');self.patch.start()
        self.client=ssh_connect(dict(host='127.0.0.1',port=self.server.port,user='test',password='test'),lambda *_:True);self.sftp=self.client.open_sftp();self.store=RemoteTextStore(self.sftp)
    def tearDown(self):self.sftp.close();self.client.close();self.server.close();self.patch.stop();self.temp.cleanup()
    def test_real_sftp_atomic_save_and_conflict(self):
        path=self.root/'config';path.write_bytes(b'original');path.chmod(0o640);snapshot=self.store.read('/config');saved=self.store.save(snapshot,b'edited')
        self.assertEqual(path.read_bytes(),b'edited')
        if os.name!='nt':self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o640)
        path.write_bytes(b'external')
        with self.assertRaises(FileConflict):self.store.save(saved,b'bad')
        self.assertEqual(path.read_bytes(),b'external');self.assertFalse(list(self.root.glob('.serial-desk-*')))
    def test_real_sftp_fallback_and_rollback(self):
        path=self.root/'config';path.write_bytes(b'original');self.server.no_posix_rename=True;snapshot=self.store.read('/config');saved=self.store.save(snapshot,b'fallback');self.assertEqual(path.read_bytes(),b'fallback')
        self.server.fail_edit_promotion=True
        with self.assertRaises(OSError):self.store.save(saved,b'failed edit')
        self.assertEqual(path.read_bytes(),b'fallback');self.assertFalse(list(self.root.glob('.serial-desk-*')))
    def test_remote_editor_gui_preserves_bom_and_crlf(self):
        path=self.root/'config.txt';path.write_bytes(b'\xef\xbb\xbforiginal\r\n');shared=SharedSSH(self.client);browser=FileBrowser(CredentialVault(self.root/'vault'));errors=[]
        with patch('file_editing.QMessageBox.warning',side_effect=lambda *a:errors.append(a[-1])):
            try:
                browser.attach_ssh(shared,'SCP',dict(host='127.0.0.1',port=self.server.port,user='test',remote_path='/'))
                self.assertTrue(pump_until(lambda:browser.table.rowCount()>0 and browser.task and not browser.task.isRunning()))
                row=next(r for r in range(browser.table.rowCount()) if browser.table.item(r,0).text()=='config.txt');browser.table.selectRow(row);browser.edit_text()
                self.assertTrue(pump_until(lambda:len(browser.editors)==1 and not browser.task.isRunning()));editor=browser.editors[0];editor.text.selectAll();editor.text.insertPlainText('edited\n');editor.save()
                self.assertTrue(pump_until(lambda:not editor.busy and not browser.task.isRunning()));self.assertEqual(path.read_bytes(),b'\xef\xbb\xbfedited\r\n');self.assertFalse(editor.text.document().isModified());editor.close();self.assertTrue(browser.prepare_close());self.assertEqual(self.server.logins,1)
            finally:browser.shutdown();shared.release()
        self.assertFalse(errors)


if __name__=='__main__':unittest.main()
