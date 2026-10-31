import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
import paramiko
from serial_core import DEFAULTS,scan_rates
from console_gui import DetectionDialog
from network_core import ssh_connect
from network_ui import Credentials
from test_network import APP,pump_until


class PromptSerial:
    def __init__(self,rate=9600,needed=3,echo=False,banner=False):
        self.baudrate=115200;self.rate=rate;self.needed=needed;self.echo=echo;self.banner=banner;self.buffer=bytearray();self.writes=[];self.count=0;self.closed=False;self.on_write=None
    @property
    def in_waiting(self):return len(self.buffer)
    def reset_input_buffer(self):self.buffer=bytearray(b'console ready\r\n' if self.banner and self.baudrate==self.rate else b'');self.count=0
    def reset_output_buffer(self):pass
    def read(self,count):data=bytes(self.buffer[:count]);del self.buffer[:count];return data
    def write(self,data):
        self.writes.append((self.baudrate,data));self.count+=1
        if self.echo:self.buffer.extend(data)
        elif self.baudrate==self.rate and self.count>=self.needed:self.buffer.extend(b'\r\nlogin:')
        if self.on_write:self.on_write()
        return len(data)
    def close(self):self.closed=True


class BaudPromptTests(unittest.TestCase):
    def scan(self,peer,**kwargs):
        with patch('serial_core.open_port',return_value=peer),patch('serial_core.BAUDS',[4800,9600]):return scan_rates(DEFAULTS,lambda:False,lambda result:None,seconds=.03,probe_interval=.02,**kwargs)
    def test_three_enters_wake_quiet_console_and_short_prompt_ranks(self):
        peer=PromptSerial();results=self.scan(peer,probe_count=3)
        self.assertEqual(results[0]['baud'],9600);self.assertTrue(results[0]['candidate']);self.assertEqual(results[0]['probes'],3);self.assertIn('login:',results[0]['preview']);self.assertTrue(peer.closed);self.assertTrue(all(data==b'\r' for baud,data in peer.writes))
    def test_listens_first_and_avoids_probing_an_active_console(self):
        peer=PromptSerial(banner=True);results=self.scan(peer,probe_count=3)
        self.assertEqual(results[0]['baud'],9600);self.assertEqual(results[0]['probes'],0);self.assertFalse(any(baud==9600 for baud,data in peer.writes))
    def test_passive_mode_never_writes(self):
        peer=PromptSerial();self.scan(peer);self.assertFalse(peer.writes);self.assertTrue(peer.closed)
    def test_enter_echo_is_not_a_candidate(self):
        peer=PromptSerial(echo=True);results=self.scan(peer,probe_count=5,probe=b'\r\n')
        self.assertTrue(all(not r['candidate'] and r['content_count']==0 for r in results));self.assertTrue(all(data==b'\r\n' for baud,data in peer.writes))
    def test_cancellation_stops_further_probes_and_closes(self):
        peer=PromptSerial();cancelled=[False];peer.on_write=lambda:cancelled.__setitem__(0,True)
        with patch('serial_core.open_port',return_value=peer),patch('serial_core.BAUDS',[9600,19200]):scan_rates(DEFAULTS,lambda:cancelled[0],lambda result:None,seconds=.03,probe_count=3,probe_interval=.02)
        self.assertEqual(len(peer.writes),1);self.assertTrue(peer.closed)
    def test_failed_probe_closes_port_and_invalid_probe_never_opens(self):
        peer=PromptSerial()
        with patch.object(peer,'write',return_value=0):
            with self.assertRaises(OSError):self.scan(peer,probe_count=3)
        self.assertTrue(peer.closed)
        with patch('serial_core.open_port') as opened:
            with self.assertRaises(ValueError):scan_rates(DEFAULTS,lambda:False,lambda result:None,probe_count=3,probe=b'command\r')
            opened.assert_not_called()
    def test_dialog_waits_for_start_and_enables_result_after_finish(self):
        peer=PromptSerial();dialog=DetectionDialog(DEFAULTS,None);self.assertIsNone(dialog.worker);self.assertTrue(dialog.probe.isChecked());dialog.wait.setValue(.3);dialog.interval.setValue(.1)
        with patch('serial_core.open_port',return_value=peer),patch('serial_core.BAUDS',[9600]):
            dialog.start_scan();self.assertTrue(pump_until(lambda:dialog.use.isEnabled(),timeout=4));self.assertEqual(dialog.table.item(0,3).text(),'3');dialog.choose()
        self.assertEqual(dialog.selected,'9600');self.assertTrue(peer.closed);dialog.deleteLater();APP.processEvents()


class AuthServer:
    def __init__(self,mode='password',legacy=False,client_key=None):
        self.mode=mode;self.legacy=legacy;self.client_key=client_key;self.key=paramiko.RSAKey.generate(2048);self.stop=threading.Event();self.transport=None;self.passwords=[];self.responses=[]
        self.listener=socket.socket();self.listener.bind(('127.0.0.1',0));self.listener.listen();self.port=self.listener.getsockname()[1];self.thread=threading.Thread(target=self.serve,daemon=True);self.thread.start()
    def serve(self):
        owner=self
        class Server(paramiko.ServerInterface):
            def get_allowed_auths(self,user):return {'password':'password','interactive':'keyboard-interactive','publickey':'publickey','none':'none'}[owner.mode]
            def check_auth_none(self,user):return paramiko.AUTH_SUCCESSFUL if owner.mode=='none' else paramiko.AUTH_FAILED
            def check_auth_password(self,user,password):owner.passwords.append(password);return paramiko.AUTH_SUCCESSFUL if owner.mode=='password' and password=='secret-login' else paramiko.AUTH_FAILED
            def check_auth_publickey(self,user,key):return paramiko.AUTH_SUCCESSFUL if owner.mode=='publickey' and owner.client_key and key.asbytes()==owner.client_key.asbytes() else paramiko.AUTH_FAILED
            def check_auth_interactive(self,user,submethods):return paramiko.InteractiveQuery('Device login','Two login responses',('Password:',False),('Verification code:',False))
            def check_auth_interactive_response(self,answers):owner.responses.append(answers);return paramiko.AUTH_SUCCESSFUL if answers==['secret-login','123456'] else paramiko.AUTH_FAILED
        try:
            sock=self.listener.accept()[0];self.transport=paramiko.Transport(sock);self.transport.add_server_key(self.key)
            if self.legacy:
                options=self.transport.get_security_options();options.kex=('diffie-hellman-group1-sha1',);options.ciphers=('aes128-cbc',);options.key_types=('ssh-rsa',);options.digests=('hmac-sha1',)
            self.transport.start_server(server=Server())
            while not self.stop.wait(.02) and self.transport.is_active():pass
        except (OSError,EOFError,paramiko.SSHException):pass
        finally:
            if self.transport:self.transport.close()
            self.listener.close()
    def close(self):
        self.stop.set()
        if self.transport:self.transport.close()
        self.thread.join(timeout=3)


class SSHCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.patch=patch('network_core.CONFIG',self.root);self.patch.start();self.servers=[];self.clients=[]
        self.agent_patch=patch('paramiko.client.Agent');agent=self.agent_patch.start().return_value;agent.get_keys.return_value=[]
        self.key_patch=patch('paramiko.client.os.path.isfile',return_value=False);self.key_patch.start()
    def tearDown(self):
        for client in self.clients:client.close()
        for server in self.servers:server.close()
        self.key_patch.stop();self.agent_patch.stop();self.patch.stop();self.temp.cleanup()
    def connect(self,mode='password',request=None,legacy=False,client_key=None,**settings):
        server=AuthServer(mode,legacy,client_key);self.servers.append(server);self.server=server
        values=dict(host='127.0.0.1',port=server.port,user='operator',ssh_legacy=legacy);values.update(settings)
        client=ssh_connect(values,lambda *_:True,request);self.clients.append(client);return client
    def test_missing_password_is_requested_instead_of_no_methods_error(self):
        request=Mock(return_value='secret-login');client=self.connect(request=request)
        self.assertTrue(client.get_transport().is_authenticated());request.assert_called_once();self.assertTrue(request.call_args.args[2]);self.assertEqual(self.server.passwords,['secret-login']);self.assertIsNone(client.get_transport().login_password)
    def test_keyboard_interactive_reuses_password_but_prompts_for_otp(self):
        request=Mock(return_value='123456');client=self.connect(mode='interactive',password='secret-login',request=request)
        self.assertTrue(client.get_transport().is_authenticated());self.assertEqual(self.server.responses,[['secret-login','123456']]);request.assert_called_once();self.assertIn('Verification',request.call_args.args[1])
    def test_explicit_interactive_prompts_without_stdin(self):
        request=Mock(side_effect=['secret-login','123456']);client=self.connect(mode='interactive',ssh_auth='interactive',request=request)
        self.assertTrue(client.get_transport().is_authenticated());self.assertEqual(request.call_count,2)
    def test_cancel_and_wrong_password_do_not_retry_login(self):
        request=Mock(return_value=None)
        with self.assertRaises(paramiko.AuthenticationException):self.connect(request=request)
        self.assertFalse(self.server.passwords)
        request.reset_mock()
        with self.assertRaises(paramiko.AuthenticationException):self.connect(password='wrong',request=request)
        self.assertEqual(self.server.passwords,['wrong']);request.assert_not_called()
    def test_legacy_only_kex_cipher_hostkey_and_rsa_auth(self):
        import io
        key=paramiko.RSAKey.generate(2048);out=io.StringIO();key.write_private_key(out)
        client=self.connect(mode='publickey',legacy=True,client_key=key,key_data=out.getvalue(),ssh_auth='keys')
        transport=client.get_transport();self.assertTrue(transport.is_authenticated());self.assertEqual(transport.local_cipher,'aes128-cbc');self.assertIn('rsa-sha2-512',transport.disabled_algorithms['pubkeys'])
    def test_legacy_mode_still_rejects_a_changed_host_key(self):
        server=AuthServer(legacy=True);self.servers.append(server);known=paramiko.HostKeys();known.add(f'[127.0.0.1]:{server.port}','ssh-rsa',paramiko.RSAKey.generate(2048));known.save(str(self.root/'known_hosts'));ask=Mock(return_value=True);request=Mock()
        with self.assertRaises(paramiko.BadHostKeyException):ssh_connect(dict(host='127.0.0.1',port=server.port,user='operator',password='secret-login',ssh_legacy=True),ask,request)
        ask.assert_not_called();request.assert_not_called()
    def test_profile_fields_and_ftp_isolation(self):
        fields=Credentials(['SSH','SFTP','FTP']);fields.apply(dict(protocol='SSH',host='old.example',port=22,ssh_legacy=True,ssh_auth='interactive'))
        self.assertTrue(fields.values()['ssh_legacy']);self.assertEqual(fields.values()['ssh_auth'],'interactive');fields.protocol.setCurrentText('FTP');fields.host.setText('files.example');self.assertFalse(fields.values()['ssh_legacy']);self.assertEqual(fields.values()['ssh_auth'],'auto')


if __name__=='__main__':unittest.main()
