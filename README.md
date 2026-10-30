# Serial Desk

A native Linux and Windows desktop toolkit for serial consoles, network maintenance and file transfers. Built with Python and Qt. Licensed under MIT.

## Features

- **Shared devices and groups:** SSH, SFTP and SCP use the same saved device entries. Group and search filters, atomic device renaming, and **Devices → Saved devices and groups** make large connection lists easier to manage. Shell and file ports are kept separately; editing one view updates the same record in the other.
- **Companion services:** enable **Open remote files with SSH** and choose SFTP, SCP, FTP, FTPS or TFTP. SFTP/SCP on the same port reuse the authenticated SSH connection. Separate FTP/FTPS logins are stored separately from SSH; without a saved file-service login the tab asks you to enter one. Automatic companion tabs keep the console selected.
- **Visible console focus:** blinking block/bar cursor at the remote insertion point, mint focus borders, keyboard-focus hints and configurable interface/console fonts, blink and scrollback under **Settings → Console appearance**.

- **Serial console:** detected ports (USB adapters first), editable device paths, custom baud, data bits, parity, stop bits, software/hardware flow control, initial DTR/RTS states, text and hexadecimal sending, Ctrl+C, break, copy/paste, RX/TX counters, profiles and session logging.
- **Baud scan:** passive sampling of common rates from 300 through 921600. Needs a device transmitting repeated readable text; candidates must be verified. Binary or silent devices cannot be reliably detected.
- **SSH / Telnet:** interactive SSH, Telnet, and Mitel SSL/TLS Telnet on port **2000**. SSH keys/agent/passwords are supported. Unknown SSH fingerprints require confirmation; changed known keys are rejected. Saved profiles can optionally retain passwords, key passphrases and encrypted private-key copies in the credential vault.
- **Session tabs:** open multiple serial, SSH/Telnet, local-terminal and file-browser sessions. Drag tabs to reorder; double-click a title to rename; close sessions independently. Ctrl+T opens a new network tab; the + menu offers every session type. Duplicate a connection tab and search its console output.
- **Remote File Manager:** browse SFTP, SCP, FTP and explicit FTPS servers; upload/download individual or multiple files, drag local files into the browser to upload, create folders and rename. On an authenticated SSH tab, click **SFTP ↗** or **SCP ↗** to open a new browser tab using the existing connection without another login. File tabs retain their own channel and can continue after the shell tab closes. Folder transfers are not supported. SCP mode browses using SFTP and transfers through the legacy SCP protocol, so browsing requires SFTP support on the server.
- **Notepad:** local scratchpad saved automatically, with open and save-copy actions.
- **Local terminal:** interactive shell for ping, SSH commands and other installed tools. No elevated privileges unless you explicitly run sudo.
- **Resizable UI:** maximize button, F11 maximize/restore, high-DPI scaling, scrollable settings, Ctrl+B to hide serial settings, and console font zoom from the View menu. Tested at 800×600.
- **Updates:** background GitHub release check at startup; accept installation or decline. Manual checks and a disable-startup-check option are in the Updates menu. Installers are checked against the release SHA256SUMS file.

The terminal handles basic cursor/erase sequences and UTF-8 text. It is a maintenance console, not a full VT100 emulator: ANSI colors and full-screen editors are not supported. Use a dedicated terminal emulator for those applications.

## Windows version

Download the per-user installer or portable ZIP from the separate [Windows release](https://github.com/MusicCityTelecom/linux-console-gui/releases/tag/windows-v1.3.1). Windows 10 1809+ / Windows 11 x64 are supported. Python is bundled. A Windows source ZIP is included in that release; it uses the same application source plus Windows-specific installation and build scripts.

See [Windows installation, source and build instructions](docs/WINDOWS.md). The Windows local terminal runs Command Prompt, COM ports use Windows names, and local credential keys use Windows DPAPI. Startup update checks select the matching Windows release channel.

## Install the Debian package

For **Ubuntu 24.04+ / Debian 13+** with Python 3.12 or newer:

```sh
sudo apt install ./serial-desk_1.3.1_all.deb
serial-desk
```

Download the `.deb` from [Releases](https://github.com/MusicCityTelecom/linux-console-gui/releases/latest). Dependencies are resolved by apt. The architecture-independent package installs under `/usr/share/serial-desk`, with an applications-menu entry and `/usr/bin/serial-desk` launcher. Updates open a terminal to request sudo authentication; restart the app after successful installation.

## Install from source

```sh
sudo apt install python3-pyqt5 python3-serial python3-paramiko python3-scp python3-cryptography xterm
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
- Network/file profiles and optional saved secrets: `credentials.vault` in the serial-desk configuration directory.
- Update preference: Qt settings under `~/.config/MusicCityTelecom/`. Previous non-secret network profiles are imported into the vault.
- Saved passwords/passphrases and optional private-key copies use the encrypted credential vault. Logs, notes and original private-key files are separate and may contain sensitive content.
- Update checks contact the public GitHub API. Installation only occurs after acceptance. SHA-256 checks detect download corruption; they are not a separate code-signing system.

## Shared devices, groups and companion tabs

Create a device from SSH or the Remote File Manager and save it once. SSH/SFTP/SCP entries appear in both places; their settings and saved authentication are synchronized inside the encrypted vault. Existing separately named profiles are retained, since they may represent different accounts or services. Group names are optional and can be assigned from **Profile save options** or **Devices → Saved devices and groups**. Use the group filter and device/host search to narrow the list.

The primary SSH port and optional remote-file service port are retained independently. SFTP/SCP shortcuts on an active SSH session open a new file tab on that connection. A saved companion service on another port uses a separate connection. FTP/FTPS credentials do not replace the device's SSH credentials. Select TFTP to open a dedicated filename-based transfer tab rather than a directory browser.

To open files automatically after SSH login, check **Open remote files with SSH** and choose the companion service before saving the device. FTP/FTPS companions need their own login; only a separately remembered file-service login is used for automatic connection. TFTP requires you to enter a filename and choose Upload or Download; opening a tab does not start a transfer.

## TFTP File Transfer

Open it from **Sessions → New TFTP File Transfer tab** or as an SSH companion. Enter a host, UDP port (normally 69), and exact remote filename, then Upload or Download. The server must permit the operation. The client supports classic [RFC 1350](https://www.rfc-editor.org/rfc/rfc1350.html) octet-mode transfers, 512-byte blocks, timeouts/retries, duplicate-block handling and cancellation, with a conservative limit below 32 MiB. It does not implement directory listing, authentication, block-size/window negotiation or a local TFTP server. TFTP is unencrypted. Failed/cancelled downloads preserve the previous local file.

## Saved credentials and master password

1. In a network or file tab, enter the host, username and authentication details. Use **Choose key file** for SSH key authentication. A separate key-passphrase field is available.
2. Check **Remember password / passphrase** to retain those secrets. Check **Store a private-key copy** to keep an encrypted copy instead of relying on the original file path. Leave it unchecked to keep just a reference to your existing key file. The original key file is never modified.
3. Check **Auto-connect on selection**, then save a named profile. Choosing that profile from the list fills the settings and connects. Refreshing the list or opening the app does not start connections. **Load** fills settings without connecting. A serial tab also has an optional connect-on-selection checkbox.
4. Use **Security → Set / change master password** to require a password before the main window opens. Close and reopen to lock the app. Canceling the unlock prompt exits without opening sessions. There is no forgotten-password recovery.

The vault uses AES-256-GCM authenticated encryption; a master-password key is derived using scrypt with a fresh salt. Without a master password, the vault is encrypted with a random local key stored in a file restricted to your account. **Local-key mode does not protect against someone who can access your Linux account and read both files.** Master-password mode removes that local key and requires your password to decrypt the vault. Back up the vault; in local-key mode, back up `credentials.key` too. There is no automatic cloud synchronization.

Master-password protection covers saved network/file credentials and gates opening the application. It does not encrypt notes, serial profiles, session logs, exports or the original private-key files. An unlocked running app and authenticated sessions still hold usable secrets in memory. Set a strong master password and use your desktop's screen lock when leaving the computer.

SSH host-key checks remain in effect for auto-connect. Unverified or legacy TLS connections still require explicit confirmation. Opening a file tab from SSH reuses authentication through an independent SSH channel; SFTP support is still required for directory browsing, including SCP mode.

## Usability review

The [1.3 review](https://github.com/MusicCityTelecom/linux-console-gui/blob/main/docs/review-1.3/REVIEW.md) records the reviewed steps, screenshots, fixes and remaining accessibility/hardware limits. The review covered shared device selection, console focus, remote-file operations and TFTP task entry.

## Build, test and release

```sh
QT_QPA_PLATFORM=offscreen python3 -m unittest -v
python3 scripts/build-release.py
```

Output in `dist/`: `.deb`, source `.tar.gz`, and `SHA256SUMS`. The builder uses an explicit file list, excluding profiles, logs, notes, reference binaries and local configuration.

Tests run on Linux and Windows and exercise serial loopback and Linux pseudo-terminal RX/TX/unplug handling; Telnet negotiation and a real loopback server; TLS Telnet with a local certificate; a local SSH server and fingerprint trust; a local shell; real SFTP and SCP upload/download on a shared SSH connection; encrypted-vault integrity, master-password and profile auto-connect checks; notes; small-screen sizing; and update version, checksum and archive-path validation. Windows-specific tests cover DPAPI and the actual Windows pseudo-terminal; POSIX-only tests are skipped on Windows. Physical serial devices, real Mitel controllers and external file servers still require equipment-specific validation.

GitHub Actions runs Linux tests on pushes and pull requests; Windows tests and bundled-executable smoke checks run in the Windows release workflow. Changing `version.py` on `main` publishes a matching GitHub release, with Linux installers and checksums, after tests pass. A separate Windows workflow builds the installer, portable archive and Windows source ZIP and publishes `windows-v<version>` without replacing the Linux latest-release marker. Releases are immutable through the workflow: increment the version for subsequent changes.

## Remove

Debian install: `sudo apt remove serial-desk`.

Source install: remove `~/.local/share/serial-desk`, `~/.local/bin/serial-desk`, and `~/.local/share/applications/serial-desk.desktop`. Keep or remove your local notes/profiles separately as desired.
