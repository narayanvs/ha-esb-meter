# Changelog

## 0.1.0-beta.8 — unreleased

- Optional same-day reference import/export comparisons with configurable dual thresholds.
- Report differences and data-quality states through six entities; support MWh via recorder conversion and Irish DST days.
- Defer recorder comparison reads until HA startup is complete.
- Document restart, cached-session and scheduled-refresh behaviour.

## 0.1.0-beta.7 — unreleased

- Remove recorder queue-drain waits from config-entry setup to prevent a startup dependency deadlock.
- Queue historical imports in bounded chunks and yield between chunks.
- Add a regression verifying import submission finishes even when recorder completion is blocked.

## 0.1.0-beta.6 — unreleased

- Normalize external statistics identifiers to lowercase as HA requires.
- Persist accepted downloads and sessions before recorder import; replay pending data after failures/restarts without another download.
- Recover the initial beta.5 recorder failure without its stale pre-import cooldown.

## 0.1.0-beta.5 — unreleased

- Preserve Azure B2C authentication cookie wire values instead of adding SimpleCookie quoting.
- Verify fresh and restored cookie behaviour and cross-host isolation with regression tests.
- Authenticated ESB login remains pending live verification.

## 0.1.0-beta.4 — unreleased

- Replace the two-per-day login guard with three attempts per rolling 15 minutes, at least one minute apart.
- Automatically reinterpret old attempt records under the shorter window without editing HA storage.
- Shorten ordinary failure backoff to 15 minutes; retain longer pauses for rejected credentials, rate limits and browser verification.
- Explain the local cooldown in the setup form.

## 0.1.0-beta.3 — unreleased

- Include the CSRF header during login confirmation, matching the reference protocol.
- Log fixed request stage and HTTP status for setup failures without retaining response bodies, URLs or credentials.
- Add regressions for HTTP diagnostic privacy and login confirmation headers.

## 0.1.0-beta.2 — unreleased

- Log in before downloading on a fresh installation.
- Treat download redirects to the login host as session expiry.
- Expose safe setup error codes and add regression tests.

## 0.1.0-beta.1 — unreleased

- Automatic account-based interval downloads and reauthentication UI.
- Persistent session reuse, login budget and retry cooldowns.
- Independent import/export parsing, Irish DST handling and correction-aware hourly statistics.
- Bounded 730-day ledger, dated summaries and freshness/completeness diagnostics.
- Synthetic parser/history tests and mocked authentication tests.

Real-account and Home Assistant recorder validation pending.
