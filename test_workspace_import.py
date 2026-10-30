import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import csv
import io
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from PyQt5.QtCore import QObject,QTimer,pyqtSignal,Qt
from PyQt5.QtWidgets import QDialog,QMessageBox,QApplication
from credential_vault import CredentialVault,VaultError
from console_gui import Window
from device_profiles import for_context,merge_profile
from network_core import SharedSSH,ssh_connect
from network_ui import Credentials,FileBrowser,NetworkConsole
from password_manager import ClipboardGuard,PasswordManager,generate_password
from profile_ui import ProfileBar
from session_import import parse_export,ImportDialog
from command_reference import COMMANDS
from test_network import APP,pump_until
from test_session_features import LocalSSHServer


def csv_bytes(headers,rows):
    out=io.StringIO();writer=csv.writer(out);writer.writerow(headers);writer.writerows(rows);return out.getvalue().encode('utf-8-sig')


class ImportTests(unittest.TestCase):
    def test_browser_csv_unicode_quoted_fields_and_optional_notes(self):
        for headers in [('name','url','username','password','note'),('Title','URL','Username','Password','Notes')]:
            items,warnings=parse_export(csv_bytes(headers,[['PBX, east','https://pbx.example/','tést','SECRET,"with quotes"','private note']]),'passwords')
            self.assertEqual(items[0]['kind'],'password');self.assertEqual(items[0]['data']['user'],'tést');self.assertEqual(items[0]['data']['password'],'SECRET,"with quotes"');self.assertEqual(items[0]['data']['notes'],'private note')
        items,_=parse_export(b'url,username,password,httpRealm,formActionOrigin\nhttps://device.example,admin,firefox-secret,,\n','passwords')
        self.assertEqual(items[0]['name'],'https://device.example')
    def test_session_csv_protocol_ports_and_unknown_entries(self):
        data=csv_bytes(['name','service type','address','port','username','password','private key path','group'],[
            ['PBX','ssh','pbx.example','2222','admin','secret',r'C:\Keys\pbx.ppk','Customers'],
            ['files','sftp','files.example','2022','user','pw','',''],['old','telnet','old.example','2323','','','',''],
            ['bad','rdp','bad.example','3389','','','',''],['badport','ssh','x','99999','','','','']])
        items,warnings=parse_export(data,'sessions');self.assertEqual(len(items),3);self.assertEqual(len(warnings),2)
        self.assertEqual(items[0]['data']['port'],2222);self.assertEqual(items[0]['data']['key'],r'C:\Keys\pbx.ppk');self.assertIn('PuTTYgen',items[0]['notes']);self.assertFalse(items[0]['data']['auto_connect'])
    def test_putty_utf16_registry_serial_and_key_paths(self):
        text=r'''Windows Registry Editor Version 5.00
[HKEY_CURRENT_USER\Software\SimonTatham\PuTTY\Sessions\Site%20A]
"HostName"="pbx.example"
"Protocol"="ssh"
"PortNumber"=dword:000008ae
"UserName"="admin"
"PublicKeyFile"="C:\\Keys\\pbx.ppk"
"RemoteCommand"="never execute this"
[HKEY_CURRENT_USER\Software\SimonTatham\PuTTY\Sessions\Serial]
"Protocol"="serial"
"SerialLine"="COM7"
"SerialSpeed"=dword:0001c200
"SerialParity"=dword:00000002
"SerialStopHalfbits"=dword:00000004
[HKEY_LOCAL_MACHINE\Software\Unrelated]
"HostName"="ignore.example"
'''
        items,warnings=parse_export(text.encode('utf-16'),'putty');self.assertEqual(len(items),2)
        self.assertEqual(items[0]['name'],'Site A');self.assertEqual(items[0]['data']['port'],2222);self.assertEqual(items[0]['data']['key'],r'C:\Keys\pbx.ppk');self.assertFalse(items[0]['data']['password']);self.assertNotIn('never execute',json.dumps(items))
        self.assertEqual(items[1]['data']['serial_settings']['baudrate'],115200);self.assertEqual(items[1]['data']['serial_settings']['parity'],'E');self.assertEqual(items[1]['data']['serial_settings']['stopbits'],2)
    def test_teraterm_ini_hosts_explicit_password_and_no_macros(self):
        text='''[Tera Term]
HostName=default.example
TCPPort=2200
[TTSSH]
Enabled=1
DefaultUserName=operator
DefaultRSAPrivateKeyFile=C:\\keys\\id_ed25519
[Hosts]
Host1=ssh.example:2222 /ssh /user=admin /passwd="source secret" /keyfile="C:\\My Keys\\key.pem" /M=never.ttl
Host2=legacy.example:2323 /telnet
Host3=oldssh.example /ssh1
'''
        items,warnings=parse_export(text.encode(),'teraterm');self.assertEqual(len(items),3)
        self.assertEqual(items[0]['data']['port'],2200);self.assertEqual(items[1]['data']['password'],'source secret');self.assertEqual(items[1]['data']['key'],r'C:\My Keys\key.pem');self.assertEqual(items[2]['data']['protocol'],'Telnet');self.assertEqual(items[2]['data']['port'],2323)
        self.assertNotIn('never.ttl',json.dumps(items));self.assertTrue(any('SSH1' in w for w in warnings))
    def test_moba_bookmark_type_not_icon_and_encrypted_secrets_omitted(self):
        fields=['0','pbx.example','2222','admin','','-1','0','do not execute','gateway.example','22','jumpuser','0','0','0',r'_CurrentDrive_:\keys\id.ppk']
        text='[Bookmarks]\nSubRep=Customers\\Site A\nImgNum=41\nPBX=#98#'+'%'.join(fields)+'#MobaFont%10\nFiles=#109#7%files.example%2022%user%\nOther=#109#4%desktop.example%3389%user%\n[Passwords]\nadmin@pbx.example=ENCRYPTED-CIPHERTEXT\n'
        items,warnings=parse_export(text.encode(),'moba');self.assertEqual(len(items),2);self.assertEqual(items[0]['data']['protocol'],'SSH');self.assertEqual(items[0]['data']['group'],'Customers/Site A');self.assertEqual(items[1]['data']['protocol'],'SFTP');self.assertEqual(items[1]['data']['port'],2022)
        self.assertNotIn('ENCRYPTED-CIPHERTEXT',json.dumps(items));self.assertNotIn('do not execute',json.dumps(items));self.assertTrue(any('unsupported' in w for w in warnings));self.assertIn('omitted',items[0]['notes'])
    def test_openssh_first_value_defaults_and_commands_never_executed(self):
        data=b'''User global-user
Host pbx alias
 HostName pbx.example
 Port 2222
 IdentityFile "C:\\My Keys\\id_ed25519"
 ProxyCommand touch /tmp/serial-desk-import-must-not-run
Host *
 Port 2200
Match exec "touch /tmp/serial-desk-import-must-not-run"
 User ignored
Host second
 HostName second.example
Include /tmp/serial-desk-import-must-not-read
'''
        with patch('subprocess.run') as execute,patch('builtins.open') as opened:
            items,warnings=parse_export(data,'sshconfig');execute.assert_not_called();opened.assert_not_called()
        self.assertEqual([i['name'] for i in items],['pbx','alias','second']);self.assertEqual(items[0]['data']['port'],2222);self.assertEqual(items[2]['data']['port'],2200);self.assertEqual(items[0]['data']['user'],'global-user');self.assertEqual(items[0]['data']['key'],r'C:\My Keys\id_ed25519')
    def test_urls_ipv6_ports_and_invalid_protocols(self):
        data=csv_bytes(['name','host'],[['FTP','ftp://user:pw@files.example:2121'],['IPv6','[::1]:2222'],['bad','http://example.com/']])
        items,warnings=parse_export(data,'sessions');self.assertEqual(len(items),2);self.assertEqual(items[0]['data']['protocol'],'FTP');self.assertEqual(items[0]['data']['port'],2121);self.assertEqual(items[0]['data']['password'],'pw');self.assertEqual(items[1]['data']['host'],'::1');self.assertEqual(items[1]['data']['port'],2222)
    def test_limits_and_empty_or_wrong_files(self):
        with self.assertRaises(ValueError):parse_export(b'x'*(10*1024*1024+1),'sessions')
        with self.assertRaises(ValueError):parse_export(b'host,user\nx,y\n','passwords')
        items,warnings=parse_export(b'not,a,session\n1,2,3\n','sessions');self.assertFalse(items);self.assertTrue(warnings)
    def test_atomic_duplicate_import_and_encryption(self):
        with tempfile.TemporaryDirectory() as d:
            v=CredentialVault(d);v.save_profile('PBX',dict(protocol='SSH',host='original',port=22,password='original'))
            item=dict(kind='profile',name='PBX',data=dict(protocol='SSH',host='new',port=2222,password='IMPORT_SECRET',auto_connect=True,open_files=True,file_auto_connect=True))
            v.import_records([item]);self.assertEqual(v.profiles['PBX']['host'],'original');self.assertEqual(v.profiles['PBX (imported 2)']['host'],'new')
            self.assertFalse(v.profiles['PBX (imported 2)']['auto_connect']);self.assertFalse(v.profiles['PBX (imported 2)']['open_files']);self.assertNotIn('IMPORT_SECRET',v.path.read_text())
            before=v.path.read_bytes()
            with self.assertRaises(VaultError):v.import_records([item,dict(kind='invalid',name='x',data={})])
            self.assertEqual(v.path.read_bytes(),before)
            self.assertEqual(v.import_records([item],'skip')['skipped'],1)
            v.import_records([item],'replace');self.assertEqual(v.profiles['PBX']['host'],'new')


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.directory=self.stack.enter_context(tempfile.TemporaryDirectory());self.root=Path(self.directory)
        self.stack.enter_context(patch.dict(os.environ,XDG_CONFIG_HOME=self.directory));self.stack.enter_context(patch('network_ui.CONFIG',self.root));self.stack.enter_context(patch('network_core.CONFIG',self.root/'ssh-config'))
        self.vault=CredentialVault(self.root/'vault');self.windows=[];self.w=self.new_window(self.vault)
    def new_window(self,vault):
        window=Window(vault,check_updates=False,restore_workspace=False);window.workspace.timer.stop();self.windows.append(window);return window
    def tearDown(self):
        for window in reversed(self.windows):
            window.workspace.options['save']=False;window.close()
        self.stack.close()
    def sample(self):
        return dict(version=1,selected=0,tabs=[dict(id='ssh-one',kind='network',title='PBX',profile='PBX',settings=dict(protocol='SSH',host='pbx.example',port=2222,user='admin',open_files=True),connected=True),
            dict(id='files-one',kind='files',title='PBX files',profile='PBX',source='ssh-one',settings=dict(protocol='SFTP',host='pbx.example',port=2222,user='admin',remote_path='/'),connected=True),dict(id='notes-one',kind='notes',title='Notepad',settings={})])
    def test_snapshot_excludes_unsaved_secrets_command_input_and_queue(self):
        self.w.network.credentials.apply(dict(protocol='SSH',host='pbx.example',port=2222,user='admin',password='UNSAVED_PASSWORD',passphrase='UNSAVED_KEY_PASSWORD',key_data='PRIVATE_KEY_MATERIAL'))
        self.w.network.input.setText('DO_NOT_REPLAY_COMMAND');self.w.files.pending_transfers=4
        snapshot=self.w.workspace.snapshot();self.w.files.pending_transfers=0;text=json.dumps(snapshot)
        for secret in ('UNSAVED_PASSWORD','UNSAVED_KEY_PASSWORD','PRIVATE_KEY_MATERIAL','DO_NOT_REPLAY_COMMAND','pending_transfers'):self.assertNotIn(secret,text)
        self.w.workspace.save(force=True);self.assertNotIn('pbx.example',self.vault.path.read_text())
    def test_restore_preserves_order_titles_selection_and_empty_serial_controls(self):
        self.w.serial_tab.port.setCurrentText('');self.w.serial_tab.parity.setCurrentText('Even');self.w.serial_tab.baud.setCurrentText('19200')
        self.w.tabs.tabBar().moveTab(1,0);self.w.tabs.setTabText(0,'Customer support');self.w.network.custom_tab_title=True;self.w.tabs.setCurrentIndex(0);self.w.new_tftp();self.w.tabs.setCurrentIndex(0)
        snapshot=self.w.workspace.snapshot();titles=[self.w.tabs.tabText(i) for i in range(self.w.tabs.count())]
        with patch('network_ui.Session') as connect:self.w.workspace.restore(snapshot,False);connect.assert_not_called()
        self.assertEqual([self.w.tabs.tabText(i) for i in range(self.w.tabs.count())],titles);self.assertEqual(self.w.tabs.currentIndex(),0);self.assertEqual(self.w.serial_tab.parity.currentText(),'Even');self.assertEqual(self.w.serial_tab.baud.currentText(),'19200')
        self.w.name_tab(self.w.network,'new title');self.assertEqual(self.w.tabs.tabText(0),'Customer support')
    def test_ask_default_decline_preserves_previous_snapshot(self):
        saved=self.sample();self.vault.save_section('workspace',saved)
        with patch('workspace.RestoreDialog') as factory,patch.object(self.w.workspace,'restore') as restore:
            dialog=factory.return_value;dialog.exec_.return_value=QDialog.Rejected;dialog.remember.isChecked.return_value=False
            self.w.workspace.startup();factory.assert_called_once();restore.assert_not_called()
        self.w.workspace.save();self.assertEqual(self.vault.section('workspace'),saved);self.assertEqual(self.w.workspace.options['policy'],'ask')
    def test_always_never_and_remember_choices(self):
        self.vault.save_section('workspace',self.sample())
        self.w.workspace.options['policy']='never'
        with patch('workspace.RestoreDialog') as ask:self.w.workspace.startup();ask.assert_not_called()
        self.w.workspace.options['policy']='always'
        with patch.object(self.w.workspace,'restore') as restore:self.w.workspace.startup();restore.assert_called_once_with(self.sample(),False)
        self.w.workspace.options['policy']='ask'
        with patch('workspace.RestoreDialog') as factory,patch.object(self.w.workspace,'restore'):
            dialog=factory.return_value;dialog.exec_.return_value=QDialog.Accepted;dialog.remember.isChecked.return_value=True;dialog.reconnect.isChecked.return_value=True
            self.w.workspace.startup()
        self.assertEqual(self.vault.section('workspace_options')['policy'],'always');self.assertTrue(self.vault.section('workspace_options')['reconnect'])
    def test_no_reconnection_from_profiles_auto_connect_without_restore_consent(self):
        self.vault.save_profile('PBX',dict(protocol='SSH',host='pbx.example',port=2222,user='admin',password='saved',auto_connect=True,open_files=True))
        with patch('network_ui.Session') as connect,patch.object(FileBrowser,'connect_remote') as files:
            self.w.workspace.restore(self.sample(),False);APP.processEvents();connect.assert_not_called();files.assert_not_called()
        self.assertEqual(self.w.network.credentials.password.text(),'saved');self.assertEqual(self.w.tabs.count(),3)
    def test_locked_vault_cannot_restore_or_open_window(self):
        snapshot=self.sample();self.vault.save_section('workspace',snapshot);self.vault.set_master_password('master-password');self.vault.lock()
        with self.assertRaises(VaultError):self.w.workspace.restore(snapshot,True)
        with self.assertRaises(VaultError):Window(self.vault,check_updates=False)
        self.vault.unlock('master-password')
        with patch('network_ui.Session') as connect:self.w.workspace.restore(self.vault.section('workspace'),False);connect.assert_not_called()
    def test_password_entry_links_refresh_and_ftp_isolation(self):
        entry=self.vault.save_password(dict(name='PBX account',user='admin',password='first',url='pbx.example'))
        self.vault.save_profile('PBX',dict(protocol='SSH',host='pbx.example',port=22,credential_ref=entry,file_protocol='FTP',file_port=21))
        bar=self.w.network.profile_bar;bar.refresh();bar.combo.setCurrentText('PBX');bar.load();self.assertEqual(self.w.network.credentials.password.text(),'first')
        self.vault.save_password(dict(name='PBX account',user='operator',password='updated'),entry);bar.load();self.assertEqual(self.w.network.credentials.password.text(),'updated')
        ftp=self.vault.resolve_login(for_context(self.vault.profiles['PBX'],['FTP']));self.assertFalse(ftp.get('credential_ref'));self.assertFalse(ftp.get('password'))
        with patch('profile_ui.QInputDialog.getText',return_value=('PBX',True)),patch('profile_ui.QMessageBox.question',return_value=QMessageBox.Yes):bar.save()
        self.assertEqual(self.vault.profiles['PBX']['credential_ref'],entry);self.assertFalse(self.vault.profiles['PBX'].get('password'))
        self.vault.delete_password(entry);bar.load();self.assertFalse(self.w.network.credentials.password.text())
    def test_password_manager_fill_links_but_never_connects(self):
        entry=self.vault.save_password(dict(name='PBX login',url='pbx.example',user='admin',password='saved-secret'))
        self.w.network.credentials.host.setText('pbx.example');self.w.tabs.setCurrentWidget(self.w.network);dialog=PasswordManager(self.w)
        with patch.object(self.w.network,'toggle') as connect:dialog.fill();connect.assert_not_called()
        self.assertEqual(self.w.network.credentials.password.text(),'saved-secret');self.assertEqual(self.w.network.credentials.credential_ref,entry)
        self.w.network.credentials.password.textEdited.emit('manual');self.assertFalse(self.w.network.credentials.credential_ref)
    def test_clipboard_clears_own_secret_only_and_generator(self):
        guard=self.w.clipboard_guard;guard.copy('temporary-password',10);self.assertTrue(pump_until(lambda:QApplication.clipboard().text()=='',timeout=1))
        guard.copy('temporary-password',10);QApplication.clipboard().setText('my unrelated clipboard');self.assertTrue(pump_until(lambda:guard.secret is None,timeout=1));self.assertEqual(QApplication.clipboard().text(),'my unrelated clipboard')
        passwords={generate_password() for _ in range(50)};self.assertEqual(len(passwords),50)
        for password in passwords:self.assertEqual(len(password),24);self.assertTrue(any(c.isupper() for c in password));self.assertTrue(any(c.islower() for c in password));self.assertTrue(any(c.isdigit() for c in password))
    def test_reference_filters_and_inserts_without_sending(self):
        ref=self.w.reference;self.assertFalse(ref.isVisible());self.assertGreater(len(COMMANDS),90)
        self.w.tabs.setCurrentWidget(self.w.network);ref.platform.setCurrentText('Cisco IOS / IOS XE');ref.search.setText('interface brief');self.assertEqual(ref.table.rowCount(),1)
        with patch.object(self.w.network,'send') as send:ref.insert();send.assert_not_called()
        self.assertEqual(self.w.network.input.text(),'show ip interface brief');ref.copy();self.assertEqual(QApplication.clipboard().text(),'show ip interface brief')
    def test_import_preview_masks_secrets_and_optional_exclusion(self):
        items,warnings=parse_export(b'name,host,username,password,passphrase\nPBX,pbx.example,admin,HIDDEN_PASSWORD,HIDDEN_PASSPHRASE\n','sessions')
        dialog=ImportDialog(self.vault,self.w);dialog.show_preview(items,warnings)
        visible=' '.join(dialog.table.item(r,c).text() for r in range(dialog.table.rowCount()) for c in range(dialog.table.columnCount()))
        self.assertNotIn('HIDDEN_PASSWORD',visible);self.assertNotIn('HIDDEN_PASSPHRASE',visible)
        dialog.secrets.setChecked(False)
        with patch('session_import.QMessageBox.information'):dialog.import_selected()
        self.assertFalse(self.vault.profiles['PBX'].get('password'));self.assertFalse(self.vault.profiles['PBX'].get('passphrase'));self.assertFalse(self.vault.profiles['PBX']['auto_connect'])
    def test_restored_ssh_companions_share_one_login_and_transfer(self):
        root=self.root/'server';root.mkdir();server=LocalSSHServer(root);self.addCleanup(server.close)
        snapshot=self.sample();snapshot['tabs'][0]['settings'].update(host='127.0.0.1',port=server.port)
        snapshot['tabs'][1]['settings'].update(host='127.0.0.1',port=server.port)
        self.vault.save_profile('PBX',dict(protocol='SSH',host='127.0.0.1',port=server.port,user='admin',password='saved',open_files=True))
        class Shell(QObject):
            received=pyqtSignal(bytes);sent=pyqtSignal(bytes);state=pyqtSignal(str);failed=pyqtSignal(str);question=pyqtSignal(str,str);finished=pyqtSignal()
            def __init__(self,settings,parent=None):super().__init__(parent);self.settings=settings;self.running=False;self.ssh_handle=None
            def start(self):
                self.ssh_handle=SharedSSH(ssh_connect(self.settings,lambda *_:True));self.running=True;QTimer.singleShot(0,lambda:self.state.emit('Connected'))
            def isRunning(self):return self.running
            def stop(self):
                if self.running:self.running=False;self.ssh_handle.release();self.state.emit('Disconnected');self.finished.emit()
            def wait(self,*args):return True
        with patch('network_ui.Session',Shell):
            self.w.workspace.restore(snapshot,True)
            self.assertTrue(pump_until(lambda:self.w.files.remote is not None and self.w.files.task and not self.w.files.task.isRunning()))
            self.assertEqual(server.logins,1);self.assertEqual(self.w.tabs.count(),3)
            source=self.root/'upload';source.write_bytes(b'config\0\xff');self.w.files.transfer(str(source),'/config',True)
            self.assertTrue(pump_until(lambda:(root/'config').exists() and not self.w.files.task.isRunning()))
            target=self.root/'download';self.w.files.transfer(str(target),'/config',False)
            self.assertTrue(pump_until(lambda:target.exists() and not self.w.files.task.isRunning()));self.assertEqual(target.read_bytes(),source.read_bytes())
            self.w.workspace.options['save']=False;self.w.close();self.windows.remove(self.w)


if __name__=='__main__':unittest.main()
