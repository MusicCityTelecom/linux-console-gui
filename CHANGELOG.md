# Changelog

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
