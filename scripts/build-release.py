#!/usr/bin/env python3
"""Build an architecture-independent Debian package and a source release."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parent.parent
scope={}; exec((ROOT/'version.py').read_text(),scope); version=scope['VERSION']
DIST=ROOT/'dist'; DIST.mkdir(exist_ok=True)
FILES=['console_gui.py','serial_core.py','network_core.py','network_ui.py','updates.py','credential_vault.py','profile_ui.py','version.py','serial-desk.svg','README.md','LICENSE']
with tempfile.TemporaryDirectory() as tmp:
    base=Path(tmp); package=base/'package'; app=package/'usr/share/serial-desk'; app.mkdir(parents=True)
    for name in FILES: shutil.copy2(ROOT/name,app/name)
    (app/'INSTALL_MODE').write_text('deb\n')
    for d in ('usr/bin','usr/share/applications','usr/share/icons/hicolor/scalable/apps','usr/share/doc/serial-desk','DEBIAN'): (package/d).mkdir(parents=True,exist_ok=True)
    launcher=package/'usr/bin/serial-desk'; launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 /usr/share/serial-desk/console_gui.py "$@"\n'); launcher.chmod(0o755)
    (package/'usr/share/applications/serial-desk.desktop').write_text('[Desktop Entry]\nType=Application\nName=Serial Desk\nComment=Serial and network console toolkit\nExec=serial-desk\nIcon=serial-desk\nTerminal=false\nCategories=Utility;Development;Network;\n')
    shutil.copy2(ROOT/'serial-desk.svg',package/'usr/share/icons/hicolor/scalable/apps/serial-desk.svg')
    shutil.copy2(ROOT/'LICENSE',package/'usr/share/doc/serial-desk/copyright')
    (package/'DEBIAN/control').write_text(f'''Package: serial-desk
Version: {version}
Section: net
Priority: optional
Architecture: all
Maintainer: MusicCityTelecom <42516199+MusicCityTelecom@users.noreply.github.com>
Depends: python3 (>= 3.12), python3-pyqt5, python3-serial, python3-paramiko, python3-scp, python3-cryptography, xterm, sudo
Homepage: https://github.com/MusicCityTelecom/linux-console-gui
Description: Serial and network console toolkit
 Serial, SSH, Telnet, Mitel TLS Telnet, remote file transfers,
 local terminal, notes, profiles and session logging.
''')
    subprocess.run(['dpkg-deb','--root-owner-group','--build',str(package),str(DIST/f'serial-desk_{version}_all.deb')],check=True)
archive=DIST/f'serial-desk-{version}.tar.gz'
# Explicit allowlist excludes local notes, credentials, build output and reference software.
with tarfile.open(archive,'w:gz') as tar:
    for name in FILES+['install-source.sh','requirements.txt','CHANGELOG.md','test_serial_desk.py','test_network.py','test_session_features.py','scripts/build-release.py','.github/workflows/ci.yml','.github/workflows/release.yml','.gitignore']:
        tar.add(ROOT/name,arcname=f'serial-desk-{version}/{name}')
with (DIST/'SHA256SUMS').open('w') as f:
    for path in (DIST/f'serial-desk_{version}_all.deb',archive): f.write(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name+'\n')
print('Release assets:',DIST)
