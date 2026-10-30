#!/bin/sh
set -eu
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/serial-desk"
BIN="$HOME/.local/bin"
export SOURCE DEST BIN
python3 - <<'PY'
import importlib.util, os, pathlib, shutil, subprocess, sys
src=pathlib.Path(os.environ['SOURCE']); dest=pathlib.Path(os.environ['DEST']); bindir=pathlib.Path(os.environ['BIN'])
missing=[m for m in ('PyQt5','serial','paramiko','scp') if importlib.util.find_spec(m) is None]
if missing:
    sys.exit('Missing dependencies: '+', '.join(missing)+'\nOn Debian/Ubuntu: sudo apt install python3-pyqt5 python3-serial python3-paramiko python3-scp xterm')
# Versioned directories keep the current installation usable if a later copy fails.
version={}; exec((src/'version.py').read_text(),version)
release=dest/('version-'+version['VERSION']); stage=dest/('staging-'+str(os.getpid()))
stage.mkdir(parents=True)
for name in ('console_gui.py','serial_core.py','network_core.py','network_ui.py','updates.py','version.py','serial-desk.svg','LICENSE','README.md'):
    shutil.copy2(src/name,stage/name)
(stage/'INSTALL_MODE').write_text('source\n')
if release.exists():
    # Avoid replacing modules while an existing process is using them.
    release=dest/(release.name+'-'+str(os.getpid()))
stage.rename(release)
link=dest/'current.new'; link.unlink(missing_ok=True); link.symlink_to(release); link.replace(dest/'current')
bindir.mkdir(parents=True,exist_ok=True)
launcher=bindir/'serial-desk'
import shlex
launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 '+shlex.quote(str(release/'console_gui.py'))+' "$@"\n'); launcher.chmod(0o755)
apps=pathlib.Path(os.environ.get('XDG_DATA_HOME',pathlib.Path.home()/'.local/share'))/'applications'; apps.mkdir(parents=True,exist_ok=True)
# Desktop Entry Exec quoting is distinct from shell quoting.
def desktop_quote(value): return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$')+'"'
(apps/'serial-desk.desktop').write_text('[Desktop Entry]\nType=Application\nName=Serial Desk\nComment=Serial and network console toolkit\nExec='+desktop_quote(launcher)+'\nIcon='+str(release/'serial-desk.svg')+'\nTerminal=false\nCategories=Utility;Development;Network;\n')
print('Installed Serial Desk '+version['VERSION']+'\nLaunch: '+str(launcher))
PY
