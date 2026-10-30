"""Versioned AES-GCM credential vault with optional scrypt master-password unlock."""
import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


class VaultError(Exception):
    pass


def b64(data): return base64.b64encode(data).decode('ascii')
def unb64(data): return base64.b64decode(data, validate=True)


class CredentialVault:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'serial-desk'
        self.path = self.directory/'credentials.vault'
        self.local_key_path = self.directory/'credentials.key'
        self._key = None; self._salt = b''; self._mode = 'local'; self._data = None; self._revision = None
        if self.path.exists():
            raw = self.path.read_bytes(); envelope = self._parse(raw)
            self._mode = envelope['mode']; self._salt = unb64(envelope['salt'])
            self._revision = hashlib.sha256(raw).digest()
            if self._mode == 'local': self.unlock()
        else:
            self._data = {'profiles':{}, 'migrated':False}

    @property
    def master_enabled(self): return self._mode == 'master'
    @property
    def locked(self): return self._data is None
    @property
    def profiles(self):
        if self.locked: raise VaultError('Unlock the credential vault first.')
        # Return copies: callers cannot accidentally mutate decrypted credentials.
        return json.loads(json.dumps(self._data['profiles']))

    def _parse(self, raw):
        try:
            value = json.loads(raw)
            if value['version'] != 1 or value['mode'] not in ('local','master'): raise ValueError()
            if len(unb64(value['nonce'])) != 12 or len(unb64(value['salt'])) != 16: raise ValueError()
            unb64(value['ciphertext'])
            return value
        except (KeyError, ValueError, TypeError): raise VaultError('The credential vault is damaged or has an unsupported format. It has not been overwritten.')

    @staticmethod
    def _derive(password, salt):
        return Scrypt(salt=salt,length=32,n=2**15,r=8,p=1).derive(password.encode('utf-8'))

    @staticmethod
    def _aad(mode, salt): return b'SerialDesk vault v1:' + mode.encode() + salt

    def _local_key(self, create=False):
        if not self.local_key_path.exists():
            if not create: raise VaultError('The local credential key is missing. Restore credentials.key from your backup.')
            self.directory.mkdir(parents=True,exist_ok=True); self.directory.chmod(0o700)
            fd = os.open(self.local_key_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f: f.write(os.urandom(32)); f.flush(); os.fsync(f.fileno())
        key = self.local_key_path.read_bytes()
        if len(key)!=32: raise VaultError('Invalid local credential key.')
        return key

    def unlock(self, password=''):
        if not self.path.exists(): return
        raw = self.path.read_bytes(); env = self._parse(raw); salt=unb64(env['salt'])
        key=self._derive(password,salt) if env['mode']=='master' else self._local_key()
        try:
            data=json.loads(AESGCM(key).decrypt(unb64(env['nonce']),unb64(env['ciphertext']),self._aad(env['mode'],salt)))
            if not isinstance(data,dict) or not isinstance(data.get('profiles'),dict): raise ValueError()
        except InvalidTag: raise VaultError('Incorrect master password or damaged credential vault.')
        except (ValueError,TypeError): raise VaultError('Invalid credential vault contents.')
        self._key=key; self._salt=salt; self._mode=env['mode']; self._data=data; self._revision=hashlib.sha256(raw).digest()

    def verify(self, password):
        if not self.master_enabled: return True
        return self._key == self._derive(password,self._salt)

    def lock(self): self._key=None; self._data=None

    def _write(self, data, mode=None, key=None, salt=None):
        if self.locked: raise VaultError('Unlock the credential vault first.')
        mode=mode or self._mode; salt=salt or self._salt or os.urandom(16)
        key=key or self._key or self._local_key(create=True)
        self.directory.mkdir(parents=True,exist_ok=True); self.directory.chmod(0o700)
        import fcntl
        lock_path=self.directory/'credentials.lock'
        lock_fd=os.open(lock_path,os.O_RDWR|os.O_CREAT,0o600)
        with os.fdopen(lock_fd,'a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            current=hashlib.sha256(self.path.read_bytes()).digest() if self.path.exists() else None
            if current != self._revision: raise VaultError('Credentials changed in another app window. Reopen this window before saving.')
            nonce=os.urandom(12)
            ciphertext=AESGCM(key).encrypt(nonce,json.dumps(data).encode(),self._aad(mode,salt))
            raw=json.dumps(dict(version=1,mode=mode,salt=b64(salt),nonce=b64(nonce),ciphertext=b64(ciphertext))).encode()
            fd,name=tempfile.mkstemp(prefix='.credentials-',dir=self.directory)
            try:
                with os.fdopen(fd,'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
                os.replace(name,self.path)
            finally:
                Path(name).unlink(missing_ok=True)
        self._key=key; self._salt=salt; self._mode=mode; self._data=data; self._revision=hashlib.sha256(raw).digest()

    def save_profile(self,name,settings):
        if self.locked: raise VaultError('Unlock the credential vault first.')
        if not name.strip(): raise VaultError('Choose a profile name.')
        data=json.loads(json.dumps(self._data)); data['profiles'][name]=dict(settings); self._write(data)

    def delete_profile(self,name):
        if self.locked: raise VaultError('Unlock the credential vault first.')
        data=json.loads(json.dumps(self._data)); data['profiles'].pop(name,None); self._write(data)

    def migrate_legacy(self,profiles):
        if self.locked or self._data.get('migrated'): return
        data=json.loads(json.dumps(self._data))
        for name,value in profiles.items():
            if isinstance(value,dict): data['profiles'].setdefault(name,dict(value,auto_connect=False))
        data['migrated']=True
        if profiles: self._write(data)
        else: self._data=data

    def set_master_password(self,password):
        if len(password)<8: raise VaultError('Use at least 8 characters for the master password.')
        salt=os.urandom(16); key=self._derive(password,salt)
        self._write(self._data,mode='master',key=key,salt=salt)
        self.local_key_path.unlink(missing_ok=True)

    def remove_master_password(self,password):
        if not self.verify(password): raise VaultError('Incorrect master password.')
        key=self._local_key(create=True)
        self._write(self._data,mode='local',key=key,salt=os.urandom(16))
