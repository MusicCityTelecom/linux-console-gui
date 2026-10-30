"""Opt-in installation of checksummed GitHub release assets."""
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from PyQt5.QtCore import QSettings, QTimer
from PyQt5.QtWidgets import QMessageBox
from network_core import Task
from version import VERSION, REPOSITORY


def version_tuple(value):
    m = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)',value)
    if not m: raise ValueError('Unsupported release version')
    return tuple(map(int,m.groups()))


def get_url(url, limit=50_000_000):
    if not (url.startswith(f'https://github.com/{REPOSITORY}/releases/download/') or
            url == f'https://api.github.com/repos/{REPOSITORY}/releases/latest'):
        raise ValueError('Unexpected update URL')
    with urlopen(Request(url,headers={'User-Agent':f'SerialDesk/{VERSION}'}),timeout=20) as response:
        data = response.read(limit+1)
    if len(data)>limit: raise ValueError('Release download exceeds size limit')
    return data


def latest_release():
    try: release = json.loads(get_url(f'https://api.github.com/repos/{REPOSITORY}/releases/latest',1_000_000))
    except HTTPError as e:
        if e.code == 404: return None
        raise
    if release.get('draft') or release.get('prerelease'): return None
    if version_tuple(release['tag_name']) <= version_tuple(VERSION): return None
    return release


def download_release(release, mode, directory):
    version = '.'.join(map(str,version_tuple(release['tag_name'])))
    name = f'serial-desk_{version}_all.deb' if mode == 'deb' else f'serial-desk-{version}.tar.gz'
    assets = {a['name']:a['browser_download_url'] for a in release['assets']}
    if name not in assets or 'SHA256SUMS' not in assets: raise ValueError('Release is missing its installer or checksums')
    sums = get_url(assets['SHA256SUMS'],65536).decode()
    expected = None
    for line in sums.splitlines():
        parts=line.split()
        if len(parts)==2 and parts[1].lstrip('*')==name: expected=parts[0]
    if not expected or not re.fullmatch('[0-9a-f]{64}',expected): raise ValueError('Missing SHA-256 checksum')
    data = get_url(assets[name])
    if hashlib.sha256(data).hexdigest()!=expected: raise ValueError('Update checksum does not match')
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    target=directory/name; target.write_bytes(data)
    return target


def extract_source(archive, destination):
    destination=Path(destination).resolve()
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            path=(destination/member.name).resolve()
            if not path.is_relative_to(destination) or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe release archive')
        tar.extractall(destination,filter='data')
    candidates=list(destination.glob('*/install-source.sh'))
    if len(candidates)!=1: raise ValueError('Release is missing its source installer')
    return candidates[0]


class UpdateController:
    def __init__(self,window):
        self.window=window; self.task=None; self.settings=QSettings('MusicCityTelecom','SerialDesk')
        self.action=window.menuBar().addMenu('Updates')
        self.action.addAction('Check for updates…',lambda:self.check(True))
        toggle=self.action.addAction('Check when opening'); toggle.setCheckable(True)
        toggle.setChecked(self.settings.value('check_updates',True,type=bool))
        toggle.toggled.connect(lambda value:self.settings.setValue('check_updates',value))
        if toggle.isChecked(): QTimer.singleShot(1500,lambda:self.check(False))
    def check(self,manual):
        if self.task and self.task.isRunning(): return
        self.task=Task(lambda task:latest_release(),self.window)
        self.task.completed.connect(lambda release,error:self.checked(release,error,manual)); self.task.start()
    def checked(self,release,error,manual):
        if error:
            if manual: QMessageBox.warning(self.window,'Update check failed',error)
            return
        if not release:
            if manual: QMessageBox.information(self.window,'Updates',f'Serial Desk {VERSION} is current. No newer published release is available.')
            return
        if QMessageBox.question(self.window,'Update available',f"Serial Desk {release['tag_name']} is available (installed: {VERSION}).\n\nDownload and install it now? Choose No to keep using this version.") != QMessageBox.Yes: return
        # Package installs use the system package manager. Source installs use the per-user installer.
        marker = Path(__file__).parent/'INSTALL_MODE'
        mode = 'deb' if marker.exists() and marker.read_text().strip() == 'deb' else 'source'
        self.folder=Path(tempfile.mkdtemp(prefix='serial-desk-update-'))
        self.task=Task(lambda task:download_release(release,mode,self.folder),self.window)
        self.task.completed.connect(lambda path,error:self.downloaded(path,error,mode)); self.task.start()
    def downloaded(self,path,error,mode):
        if error: QMessageBox.warning(self.window,'Update failed',error); return
        if mode=='deb':
            try:
                subprocess.Popen(['xterm','-T','Install Serial Desk update','-hold','-e','sudo','apt','install',str(path)])
                QMessageBox.information(self.window,'Installer opened','Approve the package installation in the terminal, then close and reopen Serial Desk after it succeeds.')
            except OSError as e: QMessageBox.warning(self.window,'Installer',str(e))
        else:
            def install(task):
                installer=extract_source(path,self.folder/'source')
                result=subprocess.run(['sh',str(installer)],capture_output=True,text=True,timeout=180)
                if result.returncode: raise RuntimeError(result.stderr[-3000:] or result.stdout[-3000:])
                return result.stdout
            self.task=Task(install,self.window); self.task.completed.connect(self.installed); self.task.start()
    def installed(self,result,error):
        if error: QMessageBox.warning(self.window,'Update failed',error)
        else: QMessageBox.information(self.window,'Updated','The new version is installed for your account. Close Serial Desk and reopen it from the applications menu.')
    def shutdown(self):
        if self.task and self.task.isRunning(): self.task.requestInterruption(); self.task.wait(210000)
