#!/usr/bin/env python3
"""Build Windows x64 portable/installer/source assets on an actual Windows runner."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parent.parent
scope={};exec((ROOT/'version.py').read_text(),scope);VERSION=scope['VERSION']
SOURCE_FILES=[
 'console_gui.py','serial_core.py','network_core.py','network_ui.py','updates.py','credential_vault.py','profile_ui.py','platform_support.py','version.py',
 'serial-desk.svg','README.md','LICENSE','CHANGELOG.md','requirements.txt','requirements-windows.txt','install-source.ps1','launch-windows.bat','install-source.sh','launch.sh','serial-desk.desktop',
 'test_serial_desk.py','test_network.py','test_session_features.py','test_windows.py','docs/WINDOWS.md','scripts/build-windows.py','scripts/build-release.py','scripts/windows-installer.iss',
 '.github/workflows/ci.yml','.github/workflows/release.yml','.github/workflows/windows-release.yml','.gitignore',
]


def build():
    if os.name!='nt': raise SystemExit('Run this builder on Windows; PyInstaller does not cross-compile.')
    output=ROOT/'dist-windows';output.mkdir(exist_ok=True)
    builddir=ROOT/'build-windows';builddir.mkdir(exist_ok=True)
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed','--name','SerialDesk',
        '--distpath',str(builddir/'dist'),'--workpath',str(builddir/'work'),'--specpath',str(builddir),
        '--collect-all','winpty','--add-data',str(ROOT/'serial-desk.svg')+';.',str(ROOT/'console_gui.py')],check=True,cwd=ROOT)
    appdir=builddir/'dist/SerialDesk'
    for name in ('README.md','LICENSE','CHANGELOG.md'):shutil.copy2(ROOT/name,appdir/name)
    shutil.copy2(ROOT/'docs/WINDOWS.md',appdir/'WINDOWS.md')
    (appdir/'INSTALL_MODE').write_text('windows\n')
    with tempfile.TemporaryDirectory() as tmp:
        report=Path(tmp)/'smoke.json'
        env=dict(os.environ,QT_QPA_PLATFORM='offscreen',XDG_CONFIG_HOME=str(Path(tmp)/'config'))
        subprocess.run([str(appdir/'SerialDesk.exe'),'--smoke-test',str(report)],env=env,check=True,timeout=60)
        result=json.loads(report.read_text())
        if result.get('version')!=VERSION or result.get('tabs')!=5 or not result.get('local_terminal'):raise RuntimeError('Bundled application smoke test failed: '+repr(result))
        print('Bundled Windows GUI/terminal smoke test passed',flush=True)
    portable=output/f'SerialDesk-{VERSION}-windows-x64-portable.zip'
    with zipfile.ZipFile(portable,'w',zipfile.ZIP_DEFLATED) as z:
        for path in appdir.rglob('*'):
            if path.is_file():z.write(path,arcname='SerialDesk/'+path.relative_to(appdir).as_posix())
    iscc=os.environ.get('ISCC') or shutil.which('ISCC.exe')
    if not iscc:raise SystemExit('Set ISCC to Inno Setup 6 ISCC.exe.')
    subprocess.run([iscc,f'/DAppVersion={VERSION}',str(ROOT/'scripts/windows-installer.iss')],check=True,cwd=ROOT)
    source=output/f'serial-desk-{VERSION}-windows-source.zip'
    with zipfile.ZipFile(source,'w',zipfile.ZIP_DEFLATED) as z:
        for name in SOURCE_FILES:z.write(ROOT/name,arcname=f'serial-desk-{VERSION}/'+name)
    with (output/'SHA256SUMS').open('w',encoding='ascii') as f:
        for path in (output/f'SerialDesk-{VERSION}-windows-x64-setup.exe',portable,source):
            f.write(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name+'\n')
    print('Windows release assets:',output)


if __name__=='__main__':build()
