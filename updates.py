"""Opt-in installation of checksummed GitHub release assets."""
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from PyQt5.QtCore import QSettings, QTimer
from PyQt5.QtWidgets import QMessageBox
from network_core import Task
from version import VERSION, REPOSITORY
from platform_support import application_directory


def version_tuple(value):
    m = re.fullmatch(r'(?:windows-)?v?(\d+)\.(\d+)\.(\d+)',value)
    if not m: raise ValueError('Unsupported release version')
    return tuple(map(int,m.groups()))


def get_url(url, limit=250_000_000):
    if not (url.startswith(f'https://github.com/{REPOSITORY}/releases/download/') or
            url in (f'https://api.github.com/repos/{REPOSITORY}/releases/latest',f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=30')):
        raise ValueError('Unexpected update URL')
    with urlopen(Request(url,headers={'User-Agent':f'SerialDesk/{VERSION}'}),timeout=20) as response:
        data = response.read(limit+1)
    if len(data)>limit: raise ValueError('Release download exceeds size limit')
    return data


def latest_release(windows=None):
    windows=os.name=='nt' if windows is None else windows
    try:
        if windows:
            releases=json.loads(get_url(f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=30',2_000_000))
            candidates=[]
            for r in releases:
                if r.get('draft') or r.get('prerelease') or not r.get('tag_name','').startswith('windows-v'): continue
                try: candidates.append((version_tuple(r['tag_name']),r))
                except ValueError: continue
            if not candidates: return None
            release=max(candidates,key=lambda item:item[0])[1]
        else:
            release=json.loads(get_url(f'https://api.github.com/repos/{REPOSITORY}/releases/latest',1_000_000))
    except HTTPError as e:
        if e.code==404: return None
        raise
    if release.get('draft') or release.get('prerelease'): return None
    if version_tuple(release['tag_name'])<=version_tuple(VERSION): return None
    return release


def download_release(release, mode, directory):
    version = '.'.join(map(str,version_tuple(release['tag_name'])))
    name = (f'SerialDesk-{version}-windows-x64-setup.exe' if mode=='windows' else
            f'serial-desk-{version}-windows-source.zip' if mode=='windows-source' else
            f'serial-desk_{version}_all.deb' if mode=='deb' else f'serial-desk-{version}.tar.gz')
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


def extract_windows_source(archive,destination):
    destination=Path(destination).resolve()
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            path=(destination/info.filename).resolve()
            if not path.is_relative_to(destination) or (info.external_attr>>16)&0o170000==0o120000:
                raise ValueError('Unsafe Windows source archive')
        z.extractall(destination)
    candidates=list(destination.glob('*/install-source.ps1'))
    if len(candidates)!=1: raise ValueError('Release is missing its Windows source installer')
    return candidates[0]


class UpdateController:
    def __init__(self,window,startup=True):
        self.window=window; self.task=None; self.settings=QSettings('MusicCityTelecom','SerialDesk')
        self.action=window.menuBar().addMenu('Updates')
        self.action.addAction('Check for updates…',lambda:self.check(True))
        toggle=self.action.addAction('Check when opening'); toggle.setCheckable(True)
        toggle.setChecked(self.settings.value('check_updates',True,type=bool))
        toggle.toggled.connect(lambda value:self.settings.setValue('check_updates',value))
        if startup and toggle.isChecked(): QTimer.singleShot(1500,lambda:self.check(False))
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
        marker = application_directory()/'INSTALL_MODE'
        installed_mode=marker.read_text().strip() if marker.exists() else ''
        mode=('windows-source' if installed_mode=='windows-source' else 'windows') if os.name=='nt' else ('deb' if installed_mode=='deb' else 'source')
        self.folder=Path(tempfile.mkdtemp(prefix='serial-desk-update-'))
        self.task=Task(lambda task:download_release(release,mode,self.folder),self.window)
        self.task.completed.connect(lambda path,error:self.downloaded(path,error,mode)); self.task.start()
    def downloaded(self,path,error,mode):
        if error: QMessageBox.warning(self.window,'Update failed',error); return
        if mode=='windows':
            try:
                subprocess.Popen([str(path)])
                QMessageBox.information(self.window,'Windows installer opened','Finish your active sessions and close Serial Desk when the installer asks. Your profiles and notes are kept in your Windows account.')
            except OSError as e: QMessageBox.warning(self.window,'Installer',str(e))
        elif mode=='deb':
            try:
                subprocess.Popen(['xterm','-T','Install Serial Desk update','-hold','-e','sudo','apt','install',str(path)])
                QMessageBox.information(self.window,'Installer opened','Approve the package installation in the terminal, then close and reopen Serial Desk after it succeeds.')
            except OSError as e: QMessageBox.warning(self.window,'Installer',str(e))
        else:
            def install(task):
                if mode=='windows-source':
                    installer=extract_windows_source(path,self.folder/'source')
                    command=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(installer)]
                else:
                    installer=extract_source(path,self.folder/'source');command=['sh',str(installer)]
                    prefix_file=application_directory()/'INSTALL_PREFIX'
                    if prefix_file.exists():
                        prefix=prefix_file.read_text()
                        if not Path(prefix).is_absolute():raise ValueError('Invalid source installation prefix.')
                        command.extend(['--prefix',prefix])
                result=subprocess.run(command,capture_output=True,text=True,timeout=180)
                if result.returncode: raise RuntimeError(result.stderr[-3000:] or result.stdout[-3000:])
                return result.stdout
            self.task=Task(install,self.window); self.task.completed.connect(self.installed); self.task.start()
    def installed(self,result,error):
        if error: QMessageBox.warning(self.window,'Update failed',error)
        else: QMessageBox.information(self.window,'Updated','The new version is installed for your account. Close Serial Desk and reopen it from the applications menu.')
    def shutdown(self):
        if self.task and self.task.isRunning(): self.task.requestInterruption(); self.task.wait(210000)
