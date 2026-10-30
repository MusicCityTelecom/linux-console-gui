# Importing sessions and passwords

Open **Devices → Import sessions / passwords**, select the format, then choose an export you created. Browser/password imports are also available from **Security → Password manager → Import**. Nothing scans browser profiles, operating-system credential stores, or other clients' databases automatically.

Review the table before importing. Passwords, passphrases and key contents are represented by availability labels, not displayed. Uncheck entries you do not want. Choose whether to include available secrets. Duplicates default to renamed copies; skip and explicit replacement are also available. Each reviewed batch is saved atomically in the encrypted vault. Imports disable connection-on-selection and automatic companion creation, so importing never starts a session.

| Source | Select / export | Imported fields | Limits |
| --- | --- | --- | --- |
| PuTTY | Registry export `.reg` of `HKEY_CURRENT_USER\Software\SimonTatham\PuTTY\Sessions` | Session names, SSH/Telnet service, host, port, username, private-key path; serial device/settings | PuTTY does not save account passwords. No registry changes, startup commands, proxy execution or automatic key-file reads. Serial entries open through **Devices → Saved devices and groups**. |
| Tera Term | `TERATERM.INI` / saved INI | Current host, `[Hosts]` history, TCP ports, TTSSH default username/key path, explicit `/user`, `/passwd`, `/keyfile`, `/p` connection arguments | Passwords are imported only when explicitly present as plaintext arguments. Protected password files and macros are not executed or decrypted. SSH1 is unsupported. |
| MobaXterm | `.mxtsessions` export or INI with `[Bookmarks…]` sections | SSH/FTP/SFTP bookmark names, groups, host, service, port, username; SSH private-key paths | Verify native bookmark fields in the preview. Other native session types are reported as unsupported; use session CSV for those. Encrypted Passwords/Credentials sections, gateways, macros and login commands are omitted. |
| Termius | Exported OpenSSH configuration or compatible host CSV, where your version provides one | Explicit host aliases, hostnames, ports, usernames, key paths; CSV groups and available plaintext fields | Termius versions/export capabilities differ. Its legacy CLI documents `termius export-ssh-config`; this does not imply the current app exports its encrypted vault. Serial Desk does not decrypt Termius vaults/keychains or recover absent passwords. |
| OpenSSH | A selected SSH `config` file | Explicit aliases, HostName, Port, User, first IdentityFile; matching wildcard defaults | Includes, Match blocks, proxies, local/remote commands and key-file contents are not loaded/executed. Check paths and unsupported network settings before connecting. |
| Chrome / Edge / Firefox / Safari | User-exported password CSV with compatible headers | Name/title, URL, username, password, optional notes/group | Stored in the password manager, not turned into SSH devices. Passkeys, browser cookies and protected browser databases are not imported. |
| Session CSV | UTF-8/UTF-16 CSV with column headers | Name, service type, host, port, username, password, key path/copy, passphrase, group, remote path | Comma/semicolon/tab delimiters accepted. Unsupported protocols and invalid hosts/ports are reported. |
| Session JSON | Flat array, or `{ "hosts": [...] }` / `{ "sessions": [...] }` | Same flat fields as session CSV | Generic interchange format, not a decoder for encrypted application backups. |

## Generic session CSV example

```csv
name,protocol,host,port,username,password,key,passphrase,group,remote_path
PBX East,SSH,pbx.example,2222,operator,,,,Customers/Site A,.
Switch,Telnet,switch.example,23,,,,,Lab,.
Files,SFTP,files.example,22,operator,,,,Customers/Site A,/backups
```

Supported services are SSH, Telnet, Mitel TLS Telnet, SFTP, SCP, FTP, FTPS and TFTP. Serial CSV/JSON entries use `protocol=Serial`, `device`/`port`, `baudrate`, `bytesize`, `parity` (N/E/O/M/S), `stopbits`, and `flow` (None/RTS/CTS/XON/XOFF/DSR/DTR); use a quoted CSV field for a flow value if needed. Imported serial profiles are available through the saved-device manager.

Header aliases include `name`/`label`/`title`, `host`/`hostname`/`address`, `protocol`/`service type`/`connection type`, `user`/`username`, `key`/`keyfile`/`private key path`, and `group`/`group path`/`folder`. An explicitly exported PEM/OpenSSH private-key string can be supplied as `key_data` or `private_key`, including a correctly quoted multiline CSV cell. Referenced files are not opened or copied by the importer. Limits: 10 MiB per file and 10,000 CSV/JSON entries.

## Keys and protected credentials

- A key path on another computer may not exist here. Update it through **Choose key file** before connecting. MobaXterm drive/profile placeholders are preserved for review, not guessed.
- PuTTY `.ppk` keys require OpenSSH conversion using PuTTYgen. The importer retains the path and flags this; it does not silently treat PPK data as an OpenSSH key.
- Only secrets actually present in supported plaintext exports can be imported. Re-enter missing/protected fields in the profile or password manager. Exported passwords are encrypted when saved in Serial Desk's vault.
- Use a master password if you want the vault to require your password at application startup. Without it, the app uses the existing local-account key protection.
- Browser password CSVs are plaintext. Remove the export when it is no longer needed; the importer does not delete your selected source file.

## Format references

- [PuTTY FAQ: password saving](https://www.chiark.greenend.org.uk/~sgtatham/putty/faq.html#faq-password-remember)
- [Tera Term setup fields](https://teratermproject.github.io/manual/4/en/setup/teraterm-ini.html) and [TTSSH connection arguments](https://teratermproject.github.io/manual/4/en/commandline/ttssh.html)
- [MobaXterm bookmark format observations](https://gist.github.com/Ruzgfpegk/ab597838e4abbe8de30d7224afd062ea) and [public importer field mappings](https://github.com/PraneethReddy-github/ternix/blob/main/electron/services/ImportExportService.ts). These describe observed formats rather than a vendor stability guarantee. No third-party parser/decryption code is included.
- [Termius legacy CLI export documentation](https://github.com/termius/termius-cli)
- [Chrome password export](https://support.google.com/chrome/answer/13068232) and [Firefox login export](https://support.mozilla.org/en-US/kb/export-login-data-firefox)
