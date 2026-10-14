import json
import unittest
from unittest.mock import AsyncMock
from support import module

c = module("client")


class DummySession:
    cookie_jar = [object()]


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        return c.ESBClient("test@example.invalid", "synthetic-password", "10000000000",
                           session=DummySession(), sleep=AsyncMock())

    async def test_cached_session_needs_no_login(self):
        client = self.client()
        client.download = AsyncMock(return_value="CSV")
        client.login = AsyncMock()
        self.assertEqual(await client.fetch(), "CSV")
        client.login.assert_not_awaited()

    async def test_expired_session_gets_exactly_one_login(self):
        client = self.client()
        client.download = AsyncMock(side_effect=[c.SessionExpired("session_expired"), "CSV"])
        client.login = AsyncMock()
        self.assertEqual(await client.fetch(), "CSV")
        client.login.assert_awaited_once()

    async def test_repeated_expiry_does_not_loop(self):
        client = self.client()
        client.download = AsyncMock(side_effect=c.SessionExpired("session_expired"))
        client.login = AsyncMock()
        with self.assertRaises(c.SessionExpired):
            await client.fetch()
        client.login.assert_awaited_once()

    async def test_challenge_does_not_trigger_another_login(self):
        client = self.client()
        client.download = AsyncMock(side_effect=c.ChallengeRequired("browser_verification_required"))
        client.login = AsyncMock()
        with self.assertRaises(c.ChallengeRequired):
            await client.fetch()
        client.login.assert_not_awaited()

    async def test_invalid_credentials_return_safe_error(self):
        client = self.client()
        client.request = AsyncMock(side_effect=[
            ('var SETTINGS = {"csrf":"test","transId":"test"};', c.LOGIN),
            (json.dumps({"status": "400", "message": "private response"}), c.LOGIN)])
        with self.assertRaises(c.InvalidAuth) as result:
            await client.login()
        self.assertEqual(str(result.exception), "invalid_auth")

    async def test_auth_form_cannot_send_tokens_to_external_host(self):
        client = self.client()
        client.request = AsyncMock(side_effect=[
            ('var SETTINGS = {"csrf":"test","transId":"test"};', c.LOGIN),
            ('{"status":"200"}', c.LOGIN),
            ('<form id="auto" action="https://evil.invalid/steal"><input name="state" value="a"><input name="code" value="b"><input name="client_info" value="c"></form>', c.LOGIN)])
        with self.assertRaisesRegex(c.PortalError, "unexpected_redirect"):
            await client.login()
        self.assertEqual(client.request.await_count, 3)

    async def test_login_budget_checked_before_credentials_sent(self):
        client = self.client()
        client.reserve_login = AsyncMock(side_effect=c.PortalError("login_cooldown"))
        client.request = AsyncMock()
        with self.assertRaises(c.PortalError):
            await client.login()
        client.request.assert_not_awaited()

    def test_settings_whitespace_and_multiline(self):
        result = c.settings('var SETTINGS = {\n "csrf": "test", "transId": "test"\n};')
        self.assertEqual(result["csrf"], "test")

    def test_fixed_host_and_https_policy(self):
        for url in ("http://myaccount.esbnetworks.ie", "https://evil.invalid", "https://user:pass@myaccount.esbnetworks.ie", "https://myaccount.esbnetworks.ie:444/"):
            with self.subTest(url=url), self.assertRaises(c.PortalError):
                c.safe_url(url)

    def test_challenge_detection(self):
        with self.assertRaises(c.ChallengeRequired):
            c.challenge('<div class="g-recaptcha">')

    async def test_fresh_install_logs_in_before_download(self):
        client = self.client()
        client.session = DummySession()
        client.session.cookie_jar = []
        calls = []
        async def login():
            calls.append('login')
        async def download():
            calls.append('download')
            return 'CSV'
        client.login = login
        client.download = download
        self.assertEqual(await client.fetch(), 'CSV')
        self.assertEqual(calls, ['login', 'download'])

    async def test_portal_only_redirect_requests_login_before_following(self):
        from unittest.mock import MagicMock
        client = self.client()
        response = MagicMock()
        response.status = 302
        response.url = c.PORTAL + '/af/t'
        response.headers = {'Location': c.LOGIN + '/oauth2/v2.0/authorize'}
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        client.session = MagicMock()
        client.session.request.return_value = context
        with self.assertRaises(c.SessionExpired):
            await client.request('GET',c.PORTAL + '/af/t',portal_only=True)
        self.assertEqual(client.session.request.call_count,1)

    async def test_http_error_has_safe_stage_and_status_only(self):
        from unittest.mock import MagicMock
        client = self.client()
        client.phase = 'login_confirmation'
        response = MagicMock()
        response.status = 400
        response.url = c.LOGIN + '?private-token=secret'
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        client.session = MagicMock()
        client.session.request.return_value = context
        with self.assertRaises(c.PortalHTTPError) as result:
            await client.request('GET',c.LOGIN)
        self.assertEqual(result.exception.status,400)
        self.assertEqual(result.exception.phase,'login_confirmation')
        self.assertEqual(str(result.exception),'portal_http_error')
        self.assertNotIn('secret',repr(result.exception.__dict__))
        response.content.iter_chunked.assert_not_called()

    async def test_confirmation_carries_csrf_header(self):
        client = self.client()
        client.request = AsyncMock(side_effect=[
            ('var SETTINGS = {"csrf":"synthetic-csrf","transId":"test"};', c.LOGIN),
            ('{"status":"200"}',c.LOGIN),
            ('<form id="auto" action="https://myaccount.esbnetworks.ie/callback"><input name="state" value="a"><input name="code" value="b"><input name="client_info" value="c"></form>',c.LOGIN),
            ('',c.PORTAL),('',c.PORTAL),('',c.HISTORY)])
        await client.login()
        confirmation = client.request.await_args_list[2]
        self.assertEqual(confirmation.kwargs['headers']['x-csrf-token'],'synthetic-csrf')
        self.assertEqual(client.auth_path,'password_login')

    async def test_authentication_cookie_wire_values_are_not_quoted(self):
        from yarl import URL
        client = c.ESBClient('test@example.invalid','synthetic-password','10000000000')
        try:
            jar = client.session.cookie_jar
            jar.update_cookies({'x-ms-cpim-csrf':'synthetic==','x-ms-cpim-trans':'synthetic+value=='},
                               response_url=URL('https://login.esbnetworks.ie/'))
            outgoing = jar.filter_cookies(URL(c.LOGIN+'/SelfAsserted'))
            for name in ('x-ms-cpim-csrf','x-ms-cpim-trans'):
                self.assertEqual(outgoing[name].coded_value,outgoing[name].value)
            self.assertFalse(jar.filter_cookies(URL('https://unrelated.invalid/')))
        finally:
            await client.close()

    async def test_persisted_authentication_cookies_remain_unquoted(self):
        from yarl import URL
        client = c.ESBClient('test@example.invalid','synthetic-password','10000000000',cookies=[
            {'name':'x-ms-cpim-csrf','value':'synthetic==','domain':'login.esbnetworks.ie','path':'/','secure':True}])
        try:
            outgoing = client.session.cookie_jar.filter_cookies(URL(c.LOGIN+'/SelfAsserted'))
            self.assertEqual(outgoing['x-ms-cpim-csrf'].coded_value,'synthetic==')
        finally:
            await client.close()
