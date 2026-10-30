# Changelog

## 1.3.1 — 2026-10-30

- Treat a Windows UDP peer-close notification after the final TFTP data/ACK as a completed download, preserving correct successful-transfer handling.
- Add a platform-independent regression test for that Windows behavior.

## 1.3.0 — 2026-10-30

- Shared device profiles across SSH/SFTP/SCP, preserving distinct shell/file ports and separate FTP/FTPS credentials.
- Device groups, group/search filters, central device manager and atomic profile renaming.
- Remote File Manager naming, protocol-specific tab labels, sticky Connect controls, filename filtering and meaningful empty states.
- Optional SFTP/SCP/FTP/FTPS/TFTP companion tabs after SSH login; automatic opening preserves console focus.
- Blinking block/bar console cursors, visible focus borders/hints, interface/console font preferences and scrollback controls.
- Classic TFTP file upload/download with retransmission, duplicate-block handling, cancellation and protected local downloads.
- Remote-transfer cancellation and atomic local downloads. Turning off saved passwords or private-key copies now removes older saved values.
- Visual review and regression coverage for the device workflow, cursor, companion tabs, credential isolation, cancellation and TFTP.

## 1.2.0 — 2026-10-30

- Windows 10/11 x64 edition with a per-user installer, portable application ZIP and shared-source ZIP in a separate Windows release.
- Windows local terminal through pywinpty, native COM-port support and AppData configuration paths.
- Cross-platform vault file locks; local Windows vault keys additionally protected by Windows DPAPI.
- Platform-specific update channels and Windows installer/source update handling.
- Windows-native CI tests, bundled GUI/terminal smoke test, packaging scripts and source installation instructions.
- Cross-platform TLS test certificates and UTF-8 notes support.

## 1.1.0 — 2026-10-30

- Multiple independent, movable and closable serial, network, file-browser and local-terminal tabs; new-session menu, tab renaming and duplication.
- One-click SFTP/SCP file tabs from SSH reuse the authenticated connection, with independent channel lifetime.
- Multi-file upload/download, local-file drag-and-drop upload and transfer queue handling.
- AES-256-GCM credential vault with optional scrypt-derived master-password unlock at startup.
- Optional saved passwords, separate key passphrases, private-key references or encrypted key copies, and profile-select auto-connect.
- Shared profiles across tabs, legacy non-secret profile migration and console text search.
- Regression coverage includes real SFTP/SCP round trips on one SSH transport, tab lifecycle and vault security.

## 1.0.0 — 2026-10-30

First public release of Serial Desk.

- Resizable, maximizable, high-DPI window with scrollable settings and tabbed tools.
- Serial port discovery, custom baud, parity/flow settings, text and hex I/O, profiles, passive baud scan and session logs.
- SSH with host-key verification, plain Telnet, and Mitel SSL/TLS Telnet on port 2000.
- SFTP, SCP, FTP and explicit FTPS file browser with upload, download, folder creation and rename.
- Persistent local scratchpad and interactive local shell.
- Startup/manual GitHub release checks with install or decline, SHA-256 verification, and an option to disable checks.
- Debian package and per-user source installation.

Mitel compatibility is based on the vendor documentation and inspection of a user-supplied 2001 Windows client. Actual controller testing remains necessary. SSLv2/SSLv3/RC4-only controllers are not supported by modern OpenSSL.
