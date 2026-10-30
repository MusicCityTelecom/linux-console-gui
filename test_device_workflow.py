import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from PyQt5.QtWidgets import QMessageBox
from credential_vault import CredentialVault
from device_profiles import for_context,merge_profile
from network_ui import Credentials,FileBrowser,NetworkConsole
from profile_ui import ProfileBar
from console_gui import Window,Terminal
from test_network import APP,pump_until
from test_session_features import LocalSSHServer
from tftp_transfer import transfer,TransferCancelled


class DeviceWorkflowTests(unittest.TestCase):
    def test_shared_profile_edits_keep_ssh_and_file_ports(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,XDG_CONFIG_HOME=d),patch('network_ui.CONFIG',Path(d)):
            vault=CredentialVault(d);vault.save_profile('PBX',dict(protocol='SSH',host='pbx.example',port=2222,user='admin',password='ssh-password',remember=True,auto_connect=False,group='Site A'))
            w=Window(vault,check_updates=False)
            self.assertIn('PBX',w.network.profile_bar.available());self.assertIn('PBX',w.files.profile_bar.available())
            w.files.profile_bar.combo.setCurrentText('PBX');w.files.profile_bar.load()
            self.assertEqual(w.files.credentials.port.text(),'2222');self.assertEqual(w.files.credentials.password.text(),'ssh-password')
            w.files.credentials.port.setText('3333');w.files.profile_bar.group.setEditText('Site B')
            with patch('profile_ui.QInputDialog.getText',return_value=('PBX',True)),patch('profile_ui.QMessageBox.question',return_value=QMessageBox.Yes):w.files.profile_bar.save()
            w.network.profile_bar.combo.setCurrentText('PBX');w.network.profile_bar.load()
            self.assertEqual(w.network.credentials.port.text(),'2222');self.assertEqual(vault.profiles['PBX']['file_port'],3333)
            self.assertEqual(w.network.profile_bar.group.currentText(),'Site B')
            w.network.profile_bar.filter_group.setCurrentText('Site B');self.assertEqual(w.network.profile_bar.combo.count(),1)
            w.network.profile_bar.search.setText('missing');self.assertEqual(w.network.profile_bar.combo.count(),0)
            w.close()
    def test_unchecking_saved_secrets_removes_previous_values(self):
        with tempfile.TemporaryDirectory() as d:
            vault=CredentialVault(d);vault.save_profile('PBX',dict(protocol='SSH',host='pbx.example',port=22,password='old-secret',passphrase='old-passphrase',key_data='old-key-copy',remember=True))
            fields=Credentials(['SSH']);bar=ProfileBar(fields,vault,lambda:None);bar.combo.setCurrentText('PBX');bar.load();bar.remember.setChecked(False);bar.store_key.setChecked(False)
            with patch('profile_ui.QInputDialog.getText',return_value=('PBX',True)),patch('profile_ui.QMessageBox.question',return_value=QMessageBox.Yes):bar.save()
            for key in ('password','passphrase','key_data'):self.assertFalse(vault.profiles['PBX'].get(key))
    def test_ftp_login_does_not_replace_ssh_login(self):
        previous=dict(protocol='SSH',host='pbx.example',port=2222,ssh_port=2222,user='ssh-user',password='ssh-secret',file_protocol='FTP',file_port=21)
        ftp=for_context(previous,['SFTP','SCP','FTP','FTPS']);self.assertEqual(ftp['password'],'');self.assertEqual(ftp['user'],'')
        merged=merge_profile(previous,dict(protocol='FTP',host='pbx.example',port=2121,user='ftp-user',password='ftp-secret',remember=True,auto_connect=False,group='Office'))
        ssh=for_context(merged,['SSH']);ftp=for_context(merged,['SFTP','SCP','FTP','FTPS'])
        self.assertEqual((ssh['user'],ssh['password'],ssh['port']),('ssh-user','ssh-secret',2222))
        self.assertEqual((ftp['user'],ftp['password'],ftp['port']),('ftp-user','ftp-secret',2121))
    def test_switching_device_to_telnet_does_not_force_ssh(self):
        merged=merge_profile(dict(protocol='SSH',ssh_port=2222,ssh_enabled=True,file_protocol='SFTP'),dict(protocol='Telnet',port=23,host='lab.example'))
        self.assertEqual(for_context(merged,['SSH','Telnet','Mitel TLS Telnet'])['protocol'],'Telnet')
        self.assertNotIn('file_protocol',merged)
    def test_group_rename_preserves_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            vault=CredentialVault(d);vault.save_profile('Old',dict(protocol='SSH',group='PBX',password='secret'));vault.rename_profile('Old','New')
            self.assertNotIn('Old',vault.profiles);self.assertEqual(vault.profiles['New']['password'],'secret')
    def test_console_cursor_state_and_repaint(self):
        terminal=Terminal();terminal.show();terminal.setFocus();APP.processEvents();terminal.feed('router> ');terminal.set_session_active(True)
        self.assertTrue(terminal.session_active);self.assertEqual(terminal.saved_position,8)
        phase=terminal.caret_phase;terminal.blink_caret();self.assertNotEqual(terminal.caret_phase,phase)
        terminal.grab();terminal.set_session_active(False);self.assertFalse(terminal.caret_timer.isActive());terminal.close()
    def test_automatic_companion_reuses_ssh_and_keeps_console_selected(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,XDG_CONFIG_HOME=d),patch('network_ui.CONFIG',Path(d)),patch('network_core.CONFIG',Path(d)/'ssh'):
            root=Path(d)/'server';root.mkdir();server=LocalSSHServer(root);w=Window(CredentialVault(Path(d)/'vault'),check_updates=False)
            from network_core import ssh_connect,SharedSSH
            client=ssh_connect(dict(host='127.0.0.1',port=server.port,user='tester',password='secret'),lambda *a:True)
            shared=SharedSSH(client)
            class Worker:
                ssh_handle=shared
                def isRunning(self):return True
            w.network.worker=Worker();w.network.last_settings=dict(protocol='SSH',host='127.0.0.1',port=server.port,file_port=server.port,open_files=True,file_protocol='SFTP')
            w.tabs.setCurrentWidget(w.network);errors=[]
            try:
                with patch('network_ui.QMessageBox.warning',side_effect=lambda *a:errors.append(a[-1])):
                    w.network.set_state('Connected')
                    self.assertEqual(w.tabs.count(),6);files=w.tabs.widget(5)
                    self.assertTrue(pump_until(lambda:files.remote is not None and not files.task.isRunning()))
                    self.assertTrue(pump_until(lambda:not files.task.isRunning()))
                    self.assertIs(w.tabs.currentWidget(),w.network);self.assertIs(files.client,client);self.assertEqual(server.logins,1);self.assertFalse(errors)
            finally:
                w.network.worker=None;w.close();shared.release();server.close()
    def test_remote_controls_and_tftp_fit_small_window(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,XDG_CONFIG_HOME=d),patch('network_ui.CONFIG',Path(d)):
            w=Window(CredentialVault(d),check_updates=False);w.show();w.tabs.setCurrentWidget(w.files);w.resize(800,600);APP.processEvents()
            self.assertFalse(w.files.actions[0].isEnabled());self.assertFalse(w.files.go_button.isEnabled());self.assertTrue(w.files.connect_button.isVisible())
            w.new_tftp();w.resize(800,600);APP.processEvents();self.assertLessEqual(w.width(),800);self.assertLessEqual(w.height(),600);w.close()
    def test_cancelled_download_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            started=threading.Event();resume=threading.Event();target=Path(d)/'config';target.write_bytes(b'ORIGINAL')
            class Remote:
                def get(self,remote,local,callback):
                    Path(local).write_bytes(b'NEW PARTIAL');started.set();resume.wait(2);callback(11,11)
                def close(self):pass
            browser=FileBrowser(CredentialVault(Path(d)/'vault'));browser.remote=Remote();browser.client=type('C',(),{'close':lambda self:None})();browser.protocol='SFTP'
            browser.run_transfers([(str(target),'/config',False)])
            self.assertTrue(pump_until(started.is_set));browser.cancel_transfers();resume.set()
            self.assertTrue(pump_until(lambda:not browser.task.isRunning() and browser.pending_transfers==0))
            self.assertEqual(target.read_bytes(),b'ORIGINAL');self.assertFalse(list(Path(d).glob('*.part')));browser.shutdown()


class TFTPTests(unittest.TestCase):
    def serve(self,payload,upload,drop_ack=False,error=False):
        listen=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);listen.bind(('127.0.0.1',0));listen.settimeout(3);port=listen.getsockname()[1];received=[];errors=[]
        def run():
            try:
                request,address=listen.recvfrom(2048)
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as peer:
                    peer.bind(('127.0.0.1',0));peer.settimeout(.5)
                    if error:peer.sendto(b'\0\5\0\1Not found\0',address);return
                    if upload:
                        peer.sendto(b'\0\4\0\0',address);expected=1;data=bytearray();dropped=False
                        while True:
                            packet,client=peer.recvfrom(2048);op,block=struct.unpack('!HH',packet[:4])
                            if op!=3:continue
                            if block==expected:data.extend(packet[4:]);expected+=1
                            if drop_ack and block==1 and not dropped:dropped=True;continue
                            peer.sendto(struct.pack('!HH',4,block),client)
                            if block==expected-1 and len(packet)<516:received.append(bytes(data));return
                    else:
                        block=1;offset=0
                        while True:
                            chunk=payload[offset:offset+512];packet=struct.pack('!HH',3,block)+chunk
                            for attempt in range(10):
                                peer.sendto(packet,address)
                                try:ack,_=peer.recvfrom(2048)
                                except socket.timeout:continue
                                if ack==struct.pack('!HH',4,block):break
                            else:raise TimeoutError('No client ACK')
                            if len(chunk)<512:return
                            offset+=512;block+=1
            except Exception as e:errors.append(str(e))
            finally:listen.close()
        thread=threading.Thread(target=run,daemon=True);thread.start();return port,received,errors,thread
    def test_upload_download_binary_and_block_boundaries(self):
        with tempfile.TemporaryDirectory() as d:
            for payload in (b'',bytes(range(256))*2,bytes(range(256))*4+b'END'):
                source=Path(d)/'upload';source.write_bytes(payload);port,received,errors,thread=self.serve(payload,True,drop_ack=True)
                transfer('127.0.0.1',port,'config.bin',source,True,timeout=.2,retries=5);thread.join(2)
                self.assertEqual(received,[payload]);self.assertFalse(errors)
                target=Path(d)/'download';port,received,errors,thread=self.serve(payload,False)
                transfer('127.0.0.1',port,'config.bin',target,False,timeout=.2,retries=5);thread.join(2)
                self.assertEqual(target.read_bytes(),payload);self.assertFalse(errors)
    def test_error_and_cancellation_preserve_destination(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'config';target.write_bytes(b'EXISTING');port,_,_,thread=self.serve(b'',False,error=True)
            with self.assertRaises(OSError):transfer('127.0.0.1',port,'missing',target,False,timeout=.2)
            thread.join(2);self.assertEqual(target.read_bytes(),b'EXISTING')
            with self.assertRaises(TransferCancelled):transfer('127.0.0.1',port,'config',target,False,cancelled=lambda:True,timeout=.1)
            self.assertEqual(target.read_bytes(),b'EXISTING')


if __name__=='__main__':unittest.main(verbosity=2)
