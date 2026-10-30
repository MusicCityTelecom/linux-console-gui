# Serial Desk

A native Linux desktop toolkit for serial consoles, network maintenance and file transfers. Built with Python and Qt. Licensed under MIT.

## Features

- **Serial console:** detected ports (USB adapters first), editable device paths, custom baud, data bits, parity, stop bits, software/hardware flow control, initial DTR/RTS states, text and hexadecimal sending, Ctrl+C, break, copy/paste, RX/TX counters, profiles and session logging.
- **Baud scan:** passive sampling of common rates from 300 through 921600. Needs a device transmitting repeated readable text; candidates must be verified. Binary or silent devices cannot be reliably detected.
- **SSH / Telnet:** interactive SSH, Telnet, and Mitel SSL/TLS Telnet on port **2000**. SSH keys/agent/passwords are supported. Unknown SSH fingerprints require confirmation; changed known keys are rejected. Network profiles never save passwords.
- **Files:** browse SFTP, SCP, FTP and explicit FTPS servers; upload/download individual files, create folders and rename. SCP mode browses using SFTP and transfers through the legacy SCP protocol, so browsing requires SFTP support on the server.
- **Notepad:** local scratchpad saved automatically, with open and save-copy actions.
- **Local terminal:** interactive shell for ping, SSH commands and other installed tools. No elevated privileges unless you explicitly run sudo.
- **Resizable UI:** maximize button, F11 maximize/restore, high-DPI scaling, scrollable settings, Ctrl+B to hide serial settings, and console font zoom from the View menu. Tested at 800×600.
- **Updates:** background GitHub release check at startup; accept installation or decline. Manual checks and a disable-startup-check option are in the Updates menu. Installers are checked against the release SHA256SUMS file.

The terminal handles basic cursor/erase sequences and UTF-8 text. It is a maintenance console, not a full VT100 emulator: ANSI colors and full-screen editors are not supported. Use a dedicated terminal emulator for those applications.

## Install the Debian package

For **Ubuntu 24.04+ / Debian 13+** with Python 3.12 or newer:

```sh
sudo apt install ./serial-desk_1.0.0_all.deb
serial-desk
```

Download the `.deb` from [Releases](https://github.com/MusicCityTelecom/linux-console-gui/releases/latest). Dependencies are resolved by apt. The architecture-independent package installs under `/usr/share/serial-desk`, with an applications-menu entry and `/usr/bin/serial-desk` launcher. Updates open a terminal to request sudo authentication; restart the app after successful installation.

## Install from source

```sh
sudo apt install python3-pyqt5 python3-serial python3-paramiko python3-scp xterm
# Extract the source release, or clone the repository:
git clone https://github.com/MusicCityTelecom/linux-console-gui.git
cd linux-console-gui
sh install-source.sh
~/.local/bin/serial-desk
```

The source installer does not require root. It installs a versioned copy under `$XDG_DATA_HOME/serial-desk` (default `~/.local/share/serial-desk`) and a launcher in `~/.local/bin`. Previous versions are retained. Updates from source use the same installer. The source tree can also be run directly with `python3 console_gui.py` after dependencies are installed. `requirements.txt` is provided for virtual environments; do not override your distribution's managed-Python protections.

## Connect and capture

1. Select a serial port, or choose SSH / Telnet and enter a host. Set the connection options, then connect.
2. Type directly into the console or use the command bar. Direct Enter sends CR. Serial send-bar endings can be None, CR, LF or CRLF. Hex sends exact byte pairs without an added ending.
3. Ctrl+C sends interrupt unless text is selected, in which case it copies. Ctrl+Shift+C always copies; Ctrl+Shift+V pastes. Backspace sends DEL.
4. Enable logging to append timestamped RX/TX JSONL records with exact hex bytes and readable text. Serial **Export view** saves the bounded scrollback to plain text. Logs can include passwords and other sensitive session contents: store them accordingly.
5. For loopback testing, enter `loop://` in the serial port field. It echoes transmitted bytes without hardware.

If a physical port is inaccessible, close other console programs and check group membership. On Debian/Ubuntu, `sudo usermod -aG dialout "$USER"` followed by logout/login is commonly needed. The app itself should run as your normal user.

## Mitel secure Telnet compatibility

The [Mitel SX-200 handbook](https://web-dev.mitel.com/sites/default/files/s3_imports/Business%20Phone%20Systems/SX-200%20IP%20Communication%20Platform/5.0/EN/SX200ICP_CX_AX_THB_R5.0.pdf) documents an SSL/TLS Telnet client using port 2000. A supplied Windows client identified itself as Mitel's MN3x00 TLS/SSL client (2001), with OpenSSL 0.9.6a. This project implements an immediate TLS handshake followed by standard Telnet negotiation; it does not incorporate or redistribute that Windows client or its source.

Certificate verification is on by default. Supply a CA certificate for privately signed servers. If necessary for old equipment, you can explicitly allow an unverified certificate and enable TLS 1.0 / older ciphers for that connection. These options require a confirmation. **SSLv2, SSLv3 and RC4-only controllers are not supported by modern OpenSSL.** Compatibility must be tested with the actual controller; TLS 1.0 support alone does not guarantee success.

Plain Telnet and FTP are unencrypted. Prefer SSH/SFTP or FTPS where supported. FTPS here uses explicit TLS on port 21, not implicit FTPS on 990.

## Local data and privacy

- Serial profiles: `$XDG_CONFIG_HOME/serial-desk/profiles.json` (default `~/.config/serial-desk/`).
- Scratchpad: the same directory, `notes.txt`.
- Explicitly trusted SSH keys: the same directory, `known_hosts`; standard OpenSSH known_hosts is also read.
- Network profiles and update preference: Qt settings under `~/.config/MusicCityTelecom/`.
- Passwords are not saved in profiles. Logs and manually saved notes may contain whatever you put in them.
- Update checks contact the public GitHub API. Installation only occurs after acceptance. SHA-256 checks detect download corruption; they are not a separate code-signing system.

## Build, test and release

```sh
QT_QPA_PLATFORM=offscreen python3 -m unittest -v
python3 scripts/build-release.py
```

Output in `dist/`: `.deb`, source `.tar.gz`, and `SHA256SUMS`. The builder uses an explicit file list, excluding profiles, logs, notes, reference binaries and local configuration.

Tests exercise serial loopback and Linux pseudo-terminal RX/TX/unplug handling; Telnet negotiation and a real loopback server; TLS Telnet with a local certificate; a local SSH server and fingerprint trust; a local shell; simulated SFTP browser actions; notes; small-screen sizing; and update version, checksum and archive-path validation. Physical serial devices, real Mitel controllers and external file servers still require equipment-specific validation.

GitHub Actions runs tests on pushes and pull requests. Changing `version.py` on `main` publishes a matching GitHub release, with installers and checksums, after tests pass. Releases are immutable through the workflow: increment the version for subsequent changes.

## Remove

Debian install: `sudo apt remove serial-desk`.

Source install: remove `~/.local/share/serial-desk`, `~/.local/bin/serial-desk`, and `~/.local/share/applications/serial-desk.desktop`. Keep or remove your local notes/profiles separately as desired.
