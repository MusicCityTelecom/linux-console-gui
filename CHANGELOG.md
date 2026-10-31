# Changelog

## 1.5.2 — 2026-10-31

- Make baud-rate discovery listen before sending anything, then optionally send up to five spaced Enter line endings at each rate to wake quiet console prompts. CR, LF or CRLF, timing and probe count are explicit scan controls; echoed blank lines are never treated as a baud candidate.
- Add per-device SSH authentication selection (automatic, password, keyboard-interactive/device prompts, or key/agent) and desktop-safe credential prompts. Missing passwords now request credentials instead of ending with an unhelpful “No authentication methods available” message; multi-step device logins can request a one-time code.
- Add an explicitly confirmed **Legacy SSH algorithms** setting for older devices such as Cisco switches. Where the bundled SSH library supports them, it allows SHA-1 Diffie-Hellman, ssh-rsa/ssh-dss, CBC and older HMAC negotiation for that device only; host-key verification remains required and SSHv1 is never enabled.
- Add regression coverage for prompted baud scans, cancellation and echo rejection, SSH password/device prompts, old-only algorithm negotiation, RSA authentication and changed-host-key rejection. Update Linux and Windows source/package manifests.

## 1.5.1 — 2026-10-31

- Preserve Windows owner/group/access rules on staged local text saves before writing contents, then use native file replacement with a recovery backup. Do not ignore permission-merge failures.
- Correct Windows short/long-path assumptions and SFTP POSIX-path handling in test fixtures; verify the native Windows directory tracker and terminal behavior alongside the complete suite.
- Add a native Windows private-file access-rule preservation test.

## 1.5.0 — 2026-10-31

- Replace the empty local-terminal sidebar with a local filesystem pane that follows shell directory changes on Linux and Windows, with independent browsing, history, hidden-file/filter controls, creation/rename, path copying, quoted-command insertion and Ctrl+B visibility.
- Add local and remote text editing with encoding/BOM and line-ending handling, search/replace, reload, local copies, conflict checks, staged replacement, permission preservation and unsaved-edit close guards. SFTP uses atomic rename when supported, with backup/rollback fallback; FTP direct saving requires permission metadata/CHMOD support.
- Deliver Tab and Shift+Tab to SSH/local/serial consoles before Qt focus traversal, and return to live output on Enter after scrolling back.
- Add Paste saved password for matching active SSH profiles and linked password-manager entries, without Enter; redact the button's outgoing password bytes from logs.
- Retain local pane folders/follow state in encrypted workspace snapshots. Add 20 regression tests, including real SSH keyboard/password traffic, live shell directory changes and SFTP editing/rollback.

## 1.4.0

- Encrypted workspace snapshots retain tab order/names, selected tab, serial/network/file settings, remote folders and window geometry. Startup asks before restoration by default; ask/always/never policies and separate optional reconnection are configurable. Master unlock precedes restoration. Commands, unsaved secrets, logs and transfers are not replayed.
- SSH-linked SFTP/SCP tabs can restore on the same authenticated SSH transport. Restored companions are reused after a manual reconnect. Failed startup connections report errors within their tabs.
- Offline, searchable F1 command reference for Linux, Windows CMD/PowerShell, macOS, Cisco IOS/XE, ArubaOS-Switch, AOS-CX, Comware, ADTRAN AOS, Junos and RouterOS. Copy or insert for review without execution.
- Optional encrypted password manager with manual entries, private-key copies, encrypted notes, a password generator, guarded 30-second clipboard clearing and centrally linked device logins.
- Reviewed imports for PuTTY registry exports, Tera Term INI hosts, MobaXterm SSH/FTP/SFTP bookmarks, Termius/OpenSSH configuration, generic session CSV/JSON, and compatible browser password CSVs. Per-entry selection, masked secrets, duplicate policies and atomic saves; protected/missing/unsupported source data is flagged.
- SSH and separate FTP credential references remain isolated. Switching unrelated protocol families clears stale login fields.
- Added 21 automated tests, including real upload/download through a restored shared SSH connection. Updated Linux/Windows installers and source packages.

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
