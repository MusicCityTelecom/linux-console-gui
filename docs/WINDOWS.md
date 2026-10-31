# Serial Desk for Windows

Version **1.5.0** adds a local file pane that follows Command Prompt's directory, local/remote text editing, terminal Tab/Shift+Tab handling, live-output scrolling on Enter, and a saved-profile password-paste button with outgoing-log redaction. It also includes encrypted workspace restoration, the F1 offline command reference, the password manager and reviewed imports. See the [import format guide](IMPORTS.md) for supported fields and limitations. Existing client-encrypted passwords and PuTTY PPK files are not automatically converted or decrypted.

Windows 10 (1809 or later) / Windows 11, **x64**. ARM64-native and 32-bit builds are not provided. The same Python application source supports both Windows and Linux.

## Downloads

- **`SerialDesk-1.5.0-windows-x64-setup.exe`** — recommended per-user installer. Python is bundled; no separate Python installation is needed. Installs in your Local AppData Programs directory and creates a Start-menu shortcut. A desktop shortcut is optional.
- **`SerialDesk-1.5.0-windows-x64-portable.zip`** — extract the entire ZIP into a writable folder and open `SerialDesk.exe`. Keep `_internal` and the other files alongside the executable. “Portable” refers to the application files; profiles and notes still live in your Windows account's AppData directory.
- **`serial-desk-1.5.0-windows-source.zip`** — full shared application source, tests, build scripts and Windows installation instructions.
- **`SHA256SUMS`** — SHA-256 checksums for the downloads.

This initial Windows build is not Authenticode-signed. Windows may show an unknown-publisher warning. Verify the download source and checksum before running it; do not disable Windows security protections globally.

## Connections and files

Saved devices are shared between SSH and the Remote File Manager, with group/search filters and optional companion file-service tabs. Console focus has a visible blinking cursor, with appearance options under Settings. TFTP is a separate filename-based transfer tool, not a directory browser.

Detected serial devices use COM names such as `COM3`. Install your adapter's Windows driver if no ports appear. Serial settings, SSH/Telnet/Mitel TLS sessions, SFTP/SCP/FTP/FTPS transfers, tabs, profiles, notepad and master-password protection work through the shared UI. SFTP/SCP upload/download are available through the file-browser buttons; the SFTP and SCP buttons on SSH tabs reuse that authenticated connection.

The local terminal runs Windows Command Prompt through a Windows pseudo-terminal. Commands such as `ping`, `ipconfig`, `tracert`, `nslookup` and installed command-line tools are available. Full-screen terminal programs and ANSI colors have the same limitations as the Linux console.

Settings, notes, trusted SSH keys and the encrypted credential vault live in `%APPDATA%\SerialDesk`. Update preferences use Qt's Windows user settings. Local-key mode additionally protects its random key with Windows DPAPI for your account. Set a master password under **Security** to require unlock at startup and protect the vault independently of the local key. Notes, logs and original SSH key files are not encrypted by the master-password option.

With DPAPI local-key mode, keep backups tied to your Windows account; copying the key file to a different account or computer will not unlock it. To move a vault between Linux and Windows, enable a master password first, then copy `credentials.vault` while the app is closed. There is no forgotten-master-password recovery.

Windows update checks select only `windows-v...` releases. Accepting an update opens the Windows installer; finish active sessions and close the app when prompted. Profiles and notes are retained. A portable build also offers the installer when updating; extract a newer portable ZIP manually if you prefer to remain portable.

## Install from Windows source

Install Python 3.12+ x64 from python.org, including the Python launcher. Extract the source ZIP, open PowerShell in its folder, and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-source.ps1
```

This changes execution policy only for that installer process. The script creates a per-user virtual environment, installs `requirements-windows.txt`, and creates **Serial Desk (Source)** in the Start menu. No administrator rights are required. Alternatively, install dependencies in your own virtual environment and run `python console_gui.py`; `launch-windows.bat` is provided for a prepared Python environment.

## Build on Windows

```powershell
py -3 -m pip install -r requirements-windows.txt "pyinstaller>=6,<7"
$env:QT_QPA_PLATFORM = 'offscreen'
py -3 -m unittest -v
$env:ISCC = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
py -3 scripts\build-windows.py
```

Install Inno Setup 6 before building. The builder creates a Qt application bundle, starts that exact executable and verifies its GUI and local terminal, then creates the installer, portable ZIP, source ZIP and checksums under `dist-windows`. GitHub Actions runs the Windows tests and packaging on a Windows x64 runner. Physical COM adapters and actual Mitel controllers still require hardware testing.

Uninstall the packaged application through Windows Settings → Apps. Source installations can be removed from `%LOCALAPPDATA%\Programs\SerialDesk-Source` and their Start-menu shortcut. User data is kept separately unless you choose to remove it.
