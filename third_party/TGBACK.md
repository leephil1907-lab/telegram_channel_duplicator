# Tgback upstream reference

- Legacy repository: https://github.com/NotStatilko/tgback
- Newer project referenced by the upstream README: https://github.com/NotStatilko/tgback-x-qt
- PyPI package: https://pypi.org/project/tgback/

Tgback is an unofficial Telegram session-backup utility, not an official account recovery service. Its upstream documentation says the backup represents an authenticated session and that anyone obtaining a usable backup may gain full account control. The README also warns that the project's cryptographic strength has not been independently audited and describes the repository as mostly legacy.

This project records the upstream reference but does not vendor or run Tgback code. Keep it optional and isolated from the channel duplicator; never ask users to upload session files or secrets into this application.
