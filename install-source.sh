#!/bin/sh
set -eu
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/serial-desk"
BIN="$HOME/.local/bin"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
SERIAL_DESK_CUSTOM_PREFIX=''
if [ "$#" -gt 0 ]; then
    if [ "$1" = '--help' ]; then
        printf '%s\n' 'Usage: sh install-source.sh [--prefix /absolute/directory]'
        exit 0
    fi
    if [ "$#" -ne 2 ] || [ "$1" != '--prefix' ]; then
        printf '%s\n' 'Usage: sh install-source.sh [--prefix /absolute/directory]' >&2
        exit 2
    fi
    case "$2" in /*) ;; *) printf '%s\n' 'Use an absolute installation prefix.' >&2; exit 2 ;; esac
    DEST="$2/share/serial-desk"
    BIN="$2/bin"
    APPS="$2/share/applications"
    SERIAL_DESK_CUSTOM_PREFIX="$2"
fi
export SOURCE DEST BIN APPS SERIAL_DESK_CUSTOM_PREFIX
python3 - <<'PY'
import importlib.util, os, pathlib, shutil, subprocess, sys
src=pathlib.Path(os.environ['SOURCE']); dest=pathlib.Path(os.environ['DEST']); bindir=pathlib.Path(os.environ['BIN'])
missing=[m for m in ('PyQt5','serial','paramiko','scp','cryptography') if importlib.util.find_spec(m) is None]
if missing:
    sys.exit('Missing dependencies: '+', '.join(missing)+'\nOn Debian/Ubuntu: sudo apt install python3-pyqt5 python3-serial python3-paramiko python3-scp python3-cryptography xterm')
# Versioned directories keep the current installation usable if a later copy fails.
version={}; exec((src/'version.py').read_text(),version)
release=dest/('version-'+version['VERSION']); stage=dest/('staging-'+str(os.getpid()))
stage.mkdir(parents=True)
for name in ('console_gui.py','serial_core.py','network_core.py','network_ui.py','ssh_support.py','updates.py','credential_vault.py','profile_ui.py','platform_support.py','device_profiles.py','ui_settings.py','tftp_transfer.py','workspace.py','password_manager.py','session_import.py','command_reference.py','file_editing.py','local_browser.py','local_terminal.py','version.py','serial-desk.svg','LICENSE','README.md'):
    shutil.copy2(src/name,stage/name)
(stage/'INSTALL_MODE').write_text('source\n')
# Only custom-prefix installs need this marker; default XDG installations retain existing behavior.
if os.environ.get('SERIAL_DESK_CUSTOM_PREFIX'):(stage/'INSTALL_PREFIX').write_text(os.environ['SERIAL_DESK_CUSTOM_PREFIX'])
if release.exists():
    # Avoid replacing modules while an existing process is using them.
    release=dest/(release.name+'-'+str(os.getpid()))
stage.rename(release)
link=dest/'current.new'; link.unlink(missing_ok=True); link.symlink_to(release); link.replace(dest/'current')
bindir.mkdir(parents=True,exist_ok=True)
launcher=bindir/'serial-desk'
import shlex
launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 '+shlex.quote(str(release/'console_gui.py'))+' "$@"\n'); launcher.chmod(0o755)
apps=pathlib.Path(os.environ['APPS']); apps.mkdir(parents=True,exist_ok=True)
# Desktop Entry Exec quoting is distinct from shell quoting.
def desktop_quote(value): return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$')+'"'
(apps/'serial-desk.desktop').write_text('[Desktop Entry]\nType=Application\nName=Serial Desk\nComment=Serial and network console toolkit\nExec='+desktop_quote(launcher)+'\nIcon='+str(release/'serial-desk.svg')+'\nTerminal=false\nCategories=Utility;Development;Network;\n')
print('Installed Serial Desk '+version['VERSION']+'\nLaunch: '+str(launcher))
PY
