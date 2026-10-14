"""Local request pacing; these are not published ESB rate limits."""
DAY = 86400
LOGIN_WINDOW = 15 * 60
LOGIN_ATTEMPTS = 3
LOGIN_SPACING = 60


def login_wait(attempts, now):
    """Seconds until another login is allowed, across restarts and setup flows."""
    recent = sorted(stamp for stamp in attempts if now-LOGIN_WINDOW < stamp <= now)
    spacing = max(0, recent[-1]+LOGIN_SPACING-now) if recent else 0
    window = max(0, recent[-LOGIN_ATTEMPTS]+LOGIN_WINDOW-now) if len(recent) >= LOGIN_ATTEMPTS else 0
    return max(spacing, window)


def login_allowed(attempts, now):
    return login_wait(attempts, now) == 0


def retry_delay(code):
    if code == "browser_verification_required":
        return 6 * 3600
    if code in ("portal_rate_limited", "invalid_auth"):
        return 3600
    return 15 * 60
