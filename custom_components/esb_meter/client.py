"""Async ESBN portal transport; protocol references are listed in NOTICE.

No CAPTCHA solver, credential logging, raw response logging or external services.
"""
from __future__ import annotations

import asyncio
import json
import re
from html.parser import HTMLParser
from http.cookies import SimpleCookie
from urllib.parse import urljoin, urlsplit

import aiohttp

PORTAL = "https://myaccount.esbnetworks.ie"
LOGIN = "https://login.esbnetworks.ie/esbntwkscustportalprdb2c01.onmicrosoft.com/B2C_1A_signup_signin"
HISTORY = PORTAL + "/Api/HistoricConsumption"
HOSTS = {"myaccount.esbnetworks.ie", "login.esbnetworks.ie"}
MAX_RESPONSE = 32 * 1024 * 1024


class PortalError(Exception):
    """Only safe, fixed error codes may cross this boundary."""


class PortalHTTPError(PortalError):
    """Safe metadata only: never retain URL, body, headers or credentials."""

    def __init__(self, status, phase):
        super().__init__("portal_http_error")
        self.status = int(status)
        self.phase = phase


class InvalidAuth(PortalError):
    pass


class ChallengeRequired(PortalError):
    pass


class SessionExpired(PortalError):
    pass


def safe_url(url: str) -> str:
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in HOSTS
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise PortalError("unexpected_redirect")
    return url


def challenge(text: str) -> None:
    # Check challenge markup, not incidental references to security scripts.
    if re.search(r'g-recaptcha|h-captcha|captcha\.html|"remoteResource"\s*:\s*"[^"\n]*captcha', text, re.I):
        raise ChallengeRequired("browser_verification_required")


def settings(text: str) -> dict:
    challenge(text)
    match = re.search(r"\bvar\s+SETTINGS\s*=\s*", text)
    if not match:
        raise PortalError("login_page_changed")
    try:
        value, _ = json.JSONDecoder().raw_decode(text[match.end():])
    except ValueError:
        raise PortalError("invalid_login_page") from None
    if not isinstance(value, dict) or not value.get("csrf") or not value.get("transId"):
        raise PortalError("invalid_login_page")
    return value


class AuthForm(HTMLParser):
    def __init__(self, text: str):
        super().__init__()
        self.active = False
        self.action = None
        self.fields = {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form" and values.get("id") == "auto":
            self.active = True
            self.action = values.get("action")
        if tag == "input" and self.active and values.get("name") in ("state", "client_info", "code"):
            self.fields[values["name"]] = values.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.active = False


class ESBClient:
    """One isolated cookie jar per meter; at most one login per fetch."""

    def __init__(self, username: str, password: str, mprn: str, *, session=None,
                 cookies=None, reserve_login=None, sleep=asyncio.sleep):
        self.username = username
        self.password = password
        self.mprn = mprn
        self.reserve_login = reserve_login
        self.sleep = sleep
        self.owned = session is None
        self.session = session or aiohttp.ClientSession(
            # Azure B2C cookies must retain browser/requests wire values.
            cookie_jar=aiohttp.CookieJar(quote_cookie=False),
            timeout=aiohttp.ClientTimeout(total=45),
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:142.0) Gecko/20100101 Firefox/142.0"})
        self.auth_path = "cached_session"
        self.phase = "not_started"
        for record in cookies or []:
            if record.get("domain", "").lstrip(".") not in HOSTS:
                continue
            cookie = SimpleCookie()
            cookie[record["name"]] = record["value"]
            for attr in ("domain", "path", "secure", "httponly", "expires"):
                if record.get(attr):
                    cookie[record["name"]][attr] = record[attr]
            self.session.cookie_jar.update_cookies(cookie)

    def export_cookies(self) -> list[dict]:
        return [{"name": c.key, "value": c.value, **{k: c[k] for k in
                 ("domain", "path", "secure", "httponly", "expires")}}
                for c in self.session.cookie_jar if c["domain"].lstrip(".") in HOSTS]

    async def close(self):
        if self.owned:
            await self.session.close()

    async def request(self, method, url, *, portal_only=False, **kwargs):
        for _ in range(8):
            safe_url(url)
            async with self.session.request(method, url, allow_redirects=False, **kwargs) as response:
                if response.status in (301, 302, 303, 307, 308):
                    url = safe_url(urljoin(str(response.url), response.headers.get("Location", "")))
                    if portal_only and urlsplit(url).hostname != "myaccount.esbnetworks.ie":
                        raise SessionExpired("session_expired")
                    if response.status == 303 or response.status in (301, 302) and method == "POST":
                        method = "GET"
                        kwargs.pop("data", None)
                        kwargs.pop("json", None)
                    kwargs.pop("params", None)
                    continue
                if response.status in (401, 403):
                    raise SessionExpired("session_expired")
                if response.status == 429:
                    raise PortalError("portal_rate_limited")
                if response.status >= 400:
                    raise PortalHTTPError(response.status, self.phase)
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE:
                        raise PortalError("response_too_large")
                try:
                    text = data.decode("utf-8-sig")
                except UnicodeError:
                    raise PortalError("invalid_response_encoding") from None
                return text, str(response.url)
        raise PortalError("too_many_redirects")

    async def login(self):
        if self.reserve_login:
            await self.reserve_login()
        self.phase = "login_page"
        text, authorize = await self.request("GET", PORTAL + "/")
        data = settings(text)
        # Non-blocking pacing follows observed ESB login requirements.
        await self.sleep(12)
        self.phase = "credentials_submission"
        text, _ = await self.request("POST", LOGIN + "/SelfAsserted",
            params={"tx": data["transId"], "p": "B2C_1A_signup_signin"},
            data={"signInName": self.username, "password": self.password, "request_type": "RESPONSE"},
            headers={"x-csrf-token": data["csrf"], "X-Requested-With": "XMLHttpRequest",
                     "Origin": "https://login.esbnetworks.ie", "Referer": authorize,
                     "Accept": "application/json, text/javascript, */*; q=0.01"})
        challenge(text)
        try:
            status = str(json.loads(text).get("status"))
        except (ValueError, AttributeError):
            raise PortalError("invalid_login_response") from None
        if status == "400":
            raise InvalidAuth("invalid_auth")
        if status != "200":
            raise PortalError("login_rejected")
        self.phase = "login_confirmation"
        text, final_url = await self.request("GET", LOGIN + "/api/CombinedSigninAndSignup/confirmed",
            params={"rememberMe": "false", "csrf_token": data["csrf"], "tx": data["transId"],
                    "p": "B2C_1A_signup_signin", "diags": json.dumps({
                        "pageViewId": data.get("pageViewId", ""),
                        "pageId": "CombinedSigninAndSignup", "trace": []})},
            headers={"Referer": authorize, "x-csrf-token": data["csrf"]})
        challenge(text)
        form = AuthForm(text)
        if not form.action or set(form.fields) != {"state", "client_info", "code"}:
            raise PortalError("missing_auth_form")
        await self.sleep(3)
        self.phase = "portal_auth_callback"
        await self.request("POST", safe_url(urljoin(final_url, form.action)), data=form.fields)
        self.phase = "portal_session"
        await self.request("GET", PORTAL + "/")
        await self.sleep(4)
        self.phase = "history_page"
        await self.request("GET", HISTORY, headers={"Referer": PORTAL + "/"})
        self.auth_path = "password_login"

    async def download(self):
        self.phase = "download_token"
        text, final_url = await self.request("GET", PORTAL + "/af/t", portal_only=True, headers={"X-Returnurl": HISTORY,
            "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"})
        challenge(text)
        if urlsplit(final_url).hostname != "myaccount.esbnetworks.ie":
            raise SessionExpired("session_expired")
        try:
            token = json.loads(text)["token"]
        except (ValueError, KeyError, TypeError):
            raise SessionExpired("session_expired") from None
        self.phase = "interval_download"
        text, final_url = await self.request("POST", PORTAL + "/DataHub/DownloadHdfPeriodic",
            portal_only=True, json={"mprn": self.mprn, "searchType": "intervalkwh"},
            headers={"X-Xsrf-Token": token, "X-Returnurl": HISTORY,
                     "Referer": HISTORY, "Origin": PORTAL})
        challenge(text)
        if urlsplit(final_url).hostname != "myaccount.esbnetworks.ie" or "Read Value" not in text[:2048]:
            raise SessionExpired("session_expired")
        return text

    async def fetch(self):
        try:
            async with asyncio.timeout(180):
                if not any(True for _ in self.session.cookie_jar):
                    await self.login()
                    return await self.download()
                try:
                    return await self.download()
                except SessionExpired:
                    await self.login()
                    return await self.download()
        except (aiohttp.ClientError, TimeoutError):
            raise PortalError("network_error") from None
