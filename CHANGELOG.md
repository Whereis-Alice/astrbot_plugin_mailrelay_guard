# Changelog

All notable changes to MailRelay Guard are documented here.

## v1.1.0 - 2026-08-09

- Replaced the draft-only LLM flow with three direct, policy-scoped mail tools:
  fixed owner delivery, current-user self delivery, and administrator-only
  third-party delivery.
- Enforced recipient authority inside the shared SMTP boundary instead of
  relying on dashboard command permissions or a model's chosen arguments.
- Added privacy-aware QQ/NapCat self-mail resolution using only the current
  sender's feature-detected profile data, with a friend-list fallback and no
  separate NapCat URL configuration.
- Added private-chat email binding with a one-time verification code, local
  opt-out through `/mailrelay_unbind`, and optional explicit QQ-number mailbox
  derivation that remains disabled by default.
- Added platform-scoped owner/admin allowlists, failure-inclusive per-user
  attempt limits, success limits, cooldowns, bounded limiter storage, and
  mode-aware recipient allowlists for administrator delivery.
- Reworked all public setup and privacy documentation for the direct-delivery
  model; NetEase 163 defaults and placeholders are now present for every
  configuration item.
- Expanded unit coverage for direct tool authorization, self-recipient
  isolation, binding verification, NapCat feature detection, and rate limits.
- Raised the declared AstrBot minimum version to 4.25 so direct LLM tools are
  cleaned up correctly when the plugin is reloaded or uninstalled.

## v1.0.0 - 2026-08-09

- Initial independent release for AstrBot 4.16 through 4.x.
- Added ?? 163 SSL defaults: `smtp.163.com`, port `465`, and `ssl`.
- Added strict exact-address/domain recipient policy with safe empty-list behavior.
- Added double authorization for control commands: real AstrBot admin plus sender-ID allowlist.
- Added SMTP connection tests, explicit manual sends, and fixed-format test mail.
- Added session-bound, expiring LLM drafts that require a human confirmation command before delivery.
- Added atomic success-only rate limiting, SMTP timeouts, input validation, and privacy-minimized JSONL audit records.
- Added unit tests, public setup guidance, and upstream acknowledgement.
