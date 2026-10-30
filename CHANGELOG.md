# Changelog

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
