# SMFTP privacy policy

Last updated: November 26, 2026. Applies to SMFTP 1.1 and later, whatever the source of installation (Microsoft Store, Uptodown, GitHub). French version: [CONFIDENTIALITE.md](CONFIDENTIALITE.md).

## In short

SMFTP collects no data. The application contains no usage statistics, no advertising and no automatic crash reporting. The publisher of SMFTP receives nothing of what you do in the application.

## Data kept on your computer

SMFTP stores locally, in your Windows account only:

- your server profiles: name, host, port, user, start folders, path of the SSH key (`%APPDATA%\SMFTP`);
- the fingerprints of the SSH servers you accepted (`known_hosts`);
- the unfinished transfer queue and the scheduled tasks, without passwords;
- a technical log (`smftp.log`) in which passwords are masked;
- your display preferences (Windows registry, `HKEY_CURRENT_USER\Software\SMFTP`).

Passwords, passphrases and the assistant's API key are saved only if you tick "Remember". They are then stored in the Windows Credential Manager, never in an SMFTP file.

The Microsoft Store version keeps these files in the application's private folder. Windows deletes them when the application is uninstalled.

## Network connections

SMFTP connects only:

1. **To the servers you configure** (FTP, FTPS, SFTP, SSH), for the operations you request or schedule.
2. **To GitHub** (`api.github.com`, and `github.com` with its file servers to download an update): at startup, at most once a day, to check for a new version (can be turned off in *Preferences → Check for updates automatically*), when you choose *Help → Check for updates*, and when you choose *Download and install*. These requests contain no personal data. The Microsoft Store version, which the Store updates, sends none.
3. **To the AI service of your choice**, only if you use the assistant (see below).

## AI assistant (optional)

The assistant is inactive until you use it. Before the first request to Claude, SMFTP asks for your consent.

- **Claude (Anthropic)**: your messages are sent to Anthropic with your own API key, together with the information the assistant looks at to answer you: file and folder names, profile names, transfer status, log excerpts. Passwords are never sent. This data is handled under Anthropic's privacy policy: <https://www.anthropic.com/legal/privacy>.
- **Ollama**: the same information is sent to the Ollama server whose address you enter, by default your own computer (`localhost`). A remote address requires your consent first.

## Deleting your data

- Profiles: right-click a profile → Delete. Its password is removed from the Credential Manager.
- Everything else: delete the `%APPDATA%\SMFTP` folder and the registry key `HKEY_CURRENT_USER\Software\SMFTP`, then the "SMFTP" entries of the Windows Credential Manager.

## Contact

Questions or requests: <https://github.com/Smeth/smftp/issues>.
