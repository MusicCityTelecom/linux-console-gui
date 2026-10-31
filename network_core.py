"""Thread-owned network and local PTY transports. No saved passwords."""
import io
import base64
import hashlib
import os
import select
import socket
import ssl
import struct
import subprocess
import threading
import queue
import time
from pathlib import Path
from platform_support import config_directory
import paramiko
from PyQt5.QtCore import QThread, pyqtSignal

CONFIG = config_directory()


class TelnetCodec:
    """Incremental IAC negotiation, terminal type and window size (RFC 854/1073/1091)."""
    def __init__(self):
        self.pending = bytearray()
        self.accepted = set()

    def feed(self, data):
        self.pending.extend(data)
        p = self.pending; out = bytearray(); reply = bytearray(); i = 0
        while i < len(p):
            if p[i] != 255:
                out.append(p[i]); i += 1; continue
            if i + 1 >= len(p): break
            cmd = p[i + 1]
            if cmd == 255:
                out.append(255); i += 2; continue
            if cmd in (251, 252, 253, 254):
                if i + 2 >= len(p): break
                opt = p[i + 2]; key = (cmd, opt)
                if key not in self.accepted:
                    if cmd == 251: reply.extend(bytes([255, 253 if opt in (0, 1, 3) else 254, opt]))
                    elif cmd == 253:
                        reply.extend(bytes([255, 251 if opt in (0, 3, 24, 31) else 252, opt]))
                        if opt == 31: reply.extend(b'\xff\xfa\x1f\x00\x78\x00\x28\xff\xf0')
                    self.accepted.add(key)
                i += 3; continue
            if cmd == 250:
                end = p.find(b'\xff\xf0', i + 2)
                if end < 0: break
                if p[i + 2:end] == b'\x18\x01':
                    reply.extend(b'\xff\xfa\x18\x00VT100\xff\xf0')
                i = end + 2; continue
            i += 2
        del p[:i]
        if len(p) > 65536: raise ValueError('Oversized Telnet negotiation')
        return bytes(out), bytes(reply)

    @staticmethod
    def encode(data):
        return data.replace(b'\xff', b'\xff\xff')


def fingerprint(key):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


class TrustPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, ask): self.ask = ask
    def missing_host_key(self, client, hostname, key):
        if not self.ask('Trust SSH host key?', f'{hostname}\n{key.get_name()}\n{fingerprint(key)}\n\nVerify this fingerprint with the administrator. Trust and save this key?'):
            raise paramiko.SSHException('Host key was not trusted')
        CONFIG.mkdir(parents=True, exist_ok=True)
        client.get_host_keys().add(hostname, key.get_name(), key)
        client.save_host_keys(str(CONFIG / 'known_hosts'))
        os.chmod(CONFIG / 'known_hosts', 0o600)


def private_key_from_text(text, passphrase=None):
    errors=[]
    for name in ('Ed25519Key','ECDSAKey','RSAKey','DSSKey'):
        key_class=getattr(paramiko,name,None)
        if key_class:
            try: return key_class.from_private_key(io.StringIO(text),password=passphrase or None)
            except (paramiko.SSHException,ValueError) as e: errors.append(e)
    raise ValueError('Could not unlock the saved private key. Check its format and passphrase.')


class SharedSSH:
    """Reference-count an authenticated client across independent shell/file channels."""
    def __init__(self,client):
        self.client=client; self._references=1; self._lock=threading.Lock()
    def acquire(self):
        with self._lock:
            transport=self.client.get_transport()
            if self._references<=0 or not transport or not transport.is_active(): raise RuntimeError('SSH connection is no longer active.')
            self._references+=1
        return self
    def release(self):
        with self._lock:
            if self._references<=0: return
            self._references-=1
            if self._references==0: self.client.close()


def ssh_connect(settings, ask):
    c = paramiko.SSHClient()
    c.load_system_host_keys()
    if (CONFIG / 'known_hosts').exists(): c.load_host_keys(str(CONFIG / 'known_hosts'))
    c.set_missing_host_key_policy(TrustPolicy(ask))
    try:
        pkey=private_key_from_text(settings['key_data'],settings.get('passphrase') or settings.get('password')) if settings.get('key_data') else None
        c.connect(settings['host'], port=int(settings.get('port', 22)), username=settings.get('user') or None,
                  password=settings.get('password') or None, key_filename=(settings.get('key') or None) if not pkey else None, pkey=pkey,
                  passphrase=settings.get('passphrase') or settings.get('password') or None, timeout=10, banner_timeout=10, auth_timeout=15,
                  allow_agent=True, look_for_keys=True)
        c.get_transport().set_keepalive(30)
        return c
    except Exception:
        c.close(); raise


class Task(QThread):
    completed = pyqtSignal(object, str)
    question = pyqtSignal(str, str)
    progress = pyqtSignal(int)
    def __init__(self, function, parent=None):
        super().__init__(parent); self.function = function; self.answer = False; self.event = threading.Event()
    def ask(self, title, text):
        self.event.clear(); self.question.emit(title, text)
        while not self.event.wait(.1):
            if self.isInterruptionRequested(): return False
        return self.answer
    def respond(self, answer): self.answer = answer; self.event.set()
    def run(self):
        try: self.completed.emit(self.function(self), '')
        except Exception as e: self.completed.emit(None, str(e))
        finally:self.function=None


class Session(QThread):
    received = pyqtSignal(bytes)
    sent = pyqtSignal(bytes)
    state = pyqtSignal(str)
    failed = pyqtSignal(str)
    question = pyqtSignal(str, str)
    directory_changed = pyqtSignal(str)
    secret_sent = pyqtSignal(int)
    def __init__(self, settings, parent=None):
        super().__init__(parent); self.settings = settings; self.output = queue.Queue(maxsize=1024)
        self.event = threading.Event(); self.answer = False; self.dimensions = (120, 40); self.ssh_handle = None
    def ask(self, title, text):
        self.event.clear(); self.question.emit(title, text)
        while not self.event.wait(.1):
            if self.isInterruptionRequested(): return False
        return self.answer
    def respond(self, answer): self.answer = answer; self.event.set()
    def send(self, data): self.output.put_nowait(data)
    def send_secret(self,data):self.output.put_nowait((data,True))
    def stop(self): self.requestInterruption(); self.event.set()
    def run(self):
        s = self.settings; sock = client = channel = process = None; master = None; winproc = None
        last_directory=None;directory_poll=0
        try:
            protocol = s['protocol']; codec = TelnetCodec()
            local_cwd=s.get('local_cwd') or str(Path.home())
            if protocol=='Local terminal' and not Path(local_cwd).is_dir():local_cwd=str(Path.home())
            if protocol == 'Local terminal' and os.name=='nt':
                from winpty import PtyProcess
                winproc=PtyProcess.spawn([os.environ.get('COMSPEC','cmd.exe'),'/Q'],cwd=local_cwd,dimensions=(40,120))
            elif protocol == 'Local terminal':
                import pty, fcntl, termios
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
                shell = os.environ.get('SHELL', '/bin/bash')
                helper = 'import fcntl,termios,os,sys; fcntl.ioctl(0,termios.TIOCSCTTY,0); os.execv(sys.argv[1],[sys.argv[1],"-i"])'
                process = subprocess.Popen(['/usr/bin/python3', '-c', helper, shell], stdin=slave, stdout=slave, stderr=slave,
                    start_new_session=True, env=dict(os.environ, TERM='vt100'), cwd=local_cwd)
                os.close(slave)
            elif protocol == 'SSH':
                client = ssh_connect(s, self.ask)
                self.ssh_handle = SharedSSH(client)
                channel = client.invoke_shell(term='vt100', width=120, height=40); channel.settimeout(.2)
            else:
                sock = socket.create_connection((s['host'], int(s['port'])), timeout=10)
                if protocol == 'Mitel TLS Telnet':
                    context = ssl.create_default_context(cafile=s.get('ca') or None)
                    if s.get('legacy'):
                        context.minimum_version = ssl.TLSVersion.TLSv1
                        context.set_ciphers('DEFAULT:@SECLEVEL=0')
                    if s.get('unverified'):
                        context.check_hostname = False; context.verify_mode = ssl.CERT_NONE
                    sock = context.wrap_socket(sock, server_hostname=s['host'])
                    self.state.emit('TLS: ' + sock.version())
                sock.settimeout(.1)

            for secret in ('password','passphrase','key_data'): self.settings.pop(secret, None)
            self.state.emit('Connected')
            while not self.isInterruptionRequested():
                if (process is not None or winproc is not None) and time.monotonic()>=directory_poll:
                    directory_poll=time.monotonic()+.4
                    try:
                        from local_terminal import process_directory
                        directory=process_directory(process.pid if process is not None else winproc.pid)
                        if directory!=last_directory:last_directory=directory;self.directory_changed.emit(directory)
                    except (OSError,ValueError,UnicodeError,AttributeError,ImportError):pass
                try: data = self.output.get_nowait()
                except queue.Empty: data = None
                sensitive=isinstance(data,tuple)
                if sensitive:data=data[0]
                if data:
                    if winproc is not None:
                        winproc.write(data.decode('utf-8',errors='replace').replace('\x7f','\b'))
                    elif master is not None:
                        view = memoryview(data)
                        while view: view = view[os.write(master, view):]
                    elif channel: channel.sendall(data)
                    else: sock.sendall(codec.encode(data))
                    if sensitive:self.secret_sent.emit(len(data))
                    else:self.sent.emit(data)
                chunk = None
                if winproc is not None:
                    if select.select([winproc],[],[],.03)[0]:
                        try: chunk=winproc.read(32768).encode('utf-8')
                        except EOFError: break
                    elif not winproc.isalive(): break
                elif master is not None:
                    if select.select([master], [], [], .03)[0]:
                        try: chunk = os.read(master, 32768)
                        except OSError: break
                    if process.poll() is not None and chunk is None: break
                elif channel:
                    if channel.recv_ready(): chunk = channel.recv(32768)
                    elif channel.closed or channel.exit_status_ready(): break
                    else: self.msleep(20)
                else:
                    try: chunk = sock.recv(32768)
                    except (socket.timeout, ssl.SSLWantReadError): pass
                if chunk == b'': break
                if chunk:
                    if sock:
                        chunk, reply = codec.feed(chunk)
                        if reply: sock.sendall(reply)
                    if chunk: self.received.emit(chunk)
        except Exception as e:
            if not self.isInterruptionRequested(): self.failed.emit(str(e))
        finally:
            while True:
                try:self.output.get_nowait()
                except queue.Empty:break
            for secret in ('password','passphrase','key_data'): self.settings.pop(secret, None)
            for resource in (channel, sock):
                if resource:
                    try: resource.close()
                    except Exception: pass
            if self.ssh_handle:
                self.ssh_handle.release(); self.ssh_handle=None
            elif client: client.close()
            if winproc is not None:
                try: winproc.close(force=True)
                except OSError: pass
            if master is not None: os.close(master)
            if process and process.poll() is None:
                import signal
                try: os.killpg(process.pid, signal.SIGHUP); process.wait(timeout=2)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    if process.poll() is None:
                        try: os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                        process.wait()
            self.state.emit('Disconnected')
