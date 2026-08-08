# Changelog

All notable changes to MailRelay Guard are documented here.

## v1.0.0 - 2026-08-09

- Initial independent release for AstrBot 4.16 through 4.x.
- Added 网易 163 SSL defaults: `smtp.163.com`, port `465`, and `ssl`.
- Added strict exact-address/domain recipient policy with safe empty-list behavior.
- Added double authorization for control commands: real AstrBot admin plus sender-ID allowlist.
- Added SMTP connection tests, explicit manual sends, and fixed-format test mail.
- Added session-bound, expiring LLM drafts that require a human confirmation command before delivery.
- Added atomic success-only rate limiting, SMTP timeouts, input validation, and privacy-minimized JSONL audit records.
- Added unit tests, public setup guidance, and upstream acknowledgement.
