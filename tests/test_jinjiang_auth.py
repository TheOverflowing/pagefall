"""Offline account connection tests. No real credentials or login requests."""
import asyncio
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

from novelist import jinjiang, jinjiang_auth as auth

ACCOUNT = 'fixture-reader@example.invalid'
PASSWORD = 'fixture-password-not-real'
TOKEN = '42_'+'f'*24


class LoginCryptoTests(unittest.TestCase):
    def test_des_encoding_matches_actual_upstream_cryptojs(self):
        values = ['fixture-password-not-real','示例密码','123_fixture_device_']
        script = "const C=require('crypto-js'); const fs=require('fs'); const a=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(a.map(v=>C.DES.encrypt(v,C.enc.Utf8.parse('KW8Dvm2N'),{iv:C.enc.Utf8.parse('1ae2c94b'),padding:C.pad.Pkcs7,mode:C.mode.CBC}).toString())));"
        expected = json.loads(subprocess.check_output(['node','-e',script],input=json.dumps(values).encode()))
        self.assertEqual([auth.encode(v) for v in values],expected)

    def test_fixed_network_boundary_and_error_redaction(self):
        with self.assertRaisesRegex(ValueError,auth.NETWORK_ERROR):auth.send('https://evil.invalid',{})
        sensitive_url = auth.LOGIN_URL+'?loginPassword=fixture-secret'
        with patch.object(auth,'build_opener') as opener:
            opener.return_value.open.side_effect = HTTPError(sensitive_url,302,'fixture-secret',{},None)
            with self.assertRaises(ValueError) as error:auth.send(auth.LOGIN_URL,{'loginPassword':'fixture-secret'})
            self.assertNotIn('fixture-secret',str(error.exception))
            args=opener.call_args.args
            redirect=args[0]
            self.assertIsNone(redirect.redirect_request(None,None,None,None,None,None))
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'],20)

    def test_unknown_or_oversized_network_response_is_rejected(self):
        for raw in (b'[]',b'not-json',b' '*65537):
            with patch.object(auth,'build_opener') as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value=raw
                with self.assertRaisesRegex(ValueError,auth.NETWORK_ERROR):auth.send(auth.LOGIN_URL,{})

    def test_token_expiry_requires_explicit_auth_failure(self):
        try:
            for message in ('网络错误','本章未购买','请稍后重试'):
                jinjiang.token_expired=False;jinjiang.mark_expired(json.dumps({'message':message}).encode());self.assertFalse(jinjiang.token_expired)
            jinjiang.mark_expired(json.dumps({'message':'token已失效，请重新登录'}).encode());self.assertTrue(jinjiang.token_expired)
        finally:jinjiang.token_expired=False


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.manager=auth.Connection(Path(self.temp.name)/'device.json')
        self.sender=patch.object(auth,'send');self.send=self.sender.start();self.addCleanup(self.sender.stop)
        self.saver=patch.object(jinjiang,'save_token',return_value={'ok':True,'has_token':True});self.save=self.saver.start();self.addCleanup(self.saver.stop)
        self.addCleanup(setattr,jinjiang,'token_expired',False)

    async def challenge(self):
        self.send.return_value={'code':'221003'}
        result=await self.manager.login({'account':ACCOUNT,'password':PASSWORD})
        self.manager.next_login=0
        return result['challenge_id']

    async def test_direct_login_saves_only_token_and_retains_device_identity(self):
        self.send.return_value={'token':TOKEN}
        jinjiang.token_expired=True
        result=await self.manager.login({'account':ACCOUNT,'password':PASSWORD})
        self.assertEqual(result,{'state':'connected'});self.save.assert_called_once_with(TOKEN)
        self.assertFalse(jinjiang.token_expired);self.assertFalse(self.manager.busy)
        saved=self.manager.device_path.read_text()
        for secret in (ACCOUNT,PASSWORD,TOKEN):self.assertNotIn(secret,saved)
        self.assertEqual(self.manager.device(),auth.Connection(self.manager.device_path).device())
        self.assertEqual(self.manager.device_path.stat().st_mode&0o777,0o600)
        self.assertNotIn(PASSWORD,json.dumps(self.send.call_args.args[1]))
        self.assertNotIn(TOKEN,json.dumps(result))

    async def test_verification_uses_same_device_and_saves_after_code(self):
        identifier=await self.challenge();first=self.send.call_args.args[1]
        self.assertNotIn(ACCOUNT,repr(self.manager.challenge))
        self.send.return_value={'data':{'message':'验证码已发送，请查收'}}
        result=await self.manager.verification(identifier)
        self.assertEqual(result,{'state':'code_sent','retry_after':60})
        self.assertEqual(self.send.call_args.args[0],auth.VERIFY_URL)
        self.assertEqual(self.send.call_args.args[1]['checktype'],'email')
        self.send.return_value={'token':TOKEN}
        result=await self.manager.login({'account':ACCOUNT,'password':PASSWORD,'challenge_id':identifier,'code':'123456'})
        last=self.send.call_args.args[1]
        self.assertEqual(first['identifiers'],last['identifiers']);self.assertEqual(last['checkdevicecode'],'123456')
        self.assertEqual(result,{'state':'connected'});self.assertIsNone(self.manager.challenge)

    async def test_sms_is_not_automatically_sent_and_resend_is_limited(self):
        self.send.return_value={'code':221003}
        result=await self.manager.login({'account':'fixture-phone','password':PASSWORD})
        self.assertEqual(result['delivery'],'phone');self.assertEqual(self.send.call_count,1)
        self.send.return_value={'message':'发送成功'}
        await self.manager.verification(result['challenge_id'])
        with self.assertRaisesRegex(ValueError,'验证码已请求'):await self.manager.verification(result['challenge_id'])
        self.assertEqual(self.send.call_count,2)

    async def test_delivery_failure_never_claims_code_sent(self):
        identifier=await self.challenge()
        self.send.return_value={'message':'发送失败，fixture-secret'}
        with self.assertRaises(ValueError) as error:await self.manager.verification(identifier)
        self.assertNotIn('fixture-secret',str(error.exception));self.save.assert_not_called()

    async def test_unknown_acknowledgement_keeps_verification_available(self):
        identifier=await self.challenge()
        for response in ({'code':'0'},{'message':'验证码已经发出'},{'data':{'message':'ok'}}):
            self.manager.challenge.sent=0;self.send.return_value=response
            result=await self.manager.verification(identifier)
            self.assertEqual(result,{'state':'code_requested','retry_after':60})
            self.assertIsNotNone(self.manager.challenge)
        self.save.assert_not_called()

    async def test_expanded_success_wording_is_acknowledged(self):
        identifier=await self.challenge()
        for message in ('发送验证码成功','验证码已经发送','获取验证码成功'):
            self.manager.challenge.sent=0;self.send.return_value={'message':message}
            result=await self.manager.verification(identifier)
            self.assertEqual(result['state'],'code_sent')

    async def test_wrong_code_retains_challenge_without_overwriting_token(self):
        identifier=await self.challenge();self.send.return_value={'message':'wrong code fixture-secret'}
        with self.assertRaisesRegex(ValueError,'验证码未通过'):
            await self.manager.login({'account':ACCOUNT,'password':PASSWORD,'challenge_id':identifier,'code':'0000'})
        self.assertIsNotNone(self.manager.challenge);self.save.assert_not_called()

    async def test_expired_wrong_account_and_cancelled_challenges_cannot_be_used(self):
        identifier=await self.challenge();self.manager.challenge.created-=301
        with self.assertRaisesRegex(ValueError,auth.EXPIRED):await self.manager.verification(identifier)
        identifier=await self.challenge()
        with self.assertRaisesRegex(ValueError,auth.EXPIRED):await self.manager.login({'account':'other','password':PASSWORD,'challenge_id':identifier,'code':'1234'})
        self.manager.next_login=0;identifier=await self.challenge();self.manager.clear(identifier)
        with self.assertRaisesRegex(ValueError,auth.EXPIRED):await self.manager.verification(identifier)
        self.save.assert_not_called()

    async def test_validation_and_concurrency_precede_network(self):
        for data in ({},{'account':ACCOUNT,'password':[]},{'account':ACCOUNT,'password':PASSWORD,'code':'1234'},{'account':ACCOUNT,'password':PASSWORD,'code':12}):
            with self.assertRaises(ValueError):await self.manager.login(data)
        async with self.manager.lock:
            with self.assertRaisesRegex(ValueError,'正在连接'):await self.manager.login({'account':ACCOUNT,'password':PASSWORD})
        self.send.assert_not_called()

    async def test_keychain_failure_keeps_existing_token_and_does_not_claim_success(self):
        self.send.return_value={'token':TOKEN};self.save.side_effect=ValueError('晋江 Token 钥匙串操作未完成，请稍后重试。')
        with self.assertRaisesRegex(ValueError,'钥匙串'):await self.manager.login({'account':ACCOUNT,'password':PASSWORD})
        self.assertFalse(self.manager.busy)

    async def test_unknown_response_is_redacted_and_existing_token_is_not_deleted(self):
        self.send.return_value={'message':'fixture-secret','token':'invalid'}
        with self.assertRaises(ValueError) as error:await self.manager.login({'account':ACCOUNT,'password':PASSWORD})
        self.assertNotIn('fixture-secret',str(error.exception));self.save.assert_not_called()


class ConnectionRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_connect_and_status_do_not_expose_credentials(self):
        from novelist import server
        from test_translation_integration import http_json
        with patch.object(server.jinjiang_connection,'login',AsyncMock(return_value={'state':'connected'})) as login,patch.object(server.workbench,'kick_queue'),patch.object(jinjiang,'has_token',return_value=True):
            status,headers,result=await http_json('/api/jinjiang/connect',method='POST',data={'account':ACCOUNT,'password':PASSWORD})
            self.assertEqual(status,200);self.assertEqual(result,{'state':'connected'});self.assertEqual(headers[b'cache-control'],b'no-store');login.assert_awaited_once()
            status,_,result=await http_json('/api/jinjiang/connection');self.assertEqual(result,{'state':'connected'})
            with patch.object(jinjiang,'token_expired',True):
                status,_,result=await http_json('/api/jinjiang/connection');self.assertEqual(result,{'state':'needs_reconnect'})

    async def test_cross_origin_busy_and_bad_cancel_requests_do_not_log_in(self):
        from novelist import server
        from test_translation_integration import http_json
        with patch.object(server.jinjiang_connection,'login',AsyncMock()) as login:
            status,_,_=await http_json('/api/jinjiang/connect',method='POST',data={'account':ACCOUNT,'password':PASSWORD},origin='https://evil.invalid');self.assertEqual(status,403)
            with patch.object(server,'require_idle',side_effect=server.HTTPException(409,'busy')):
                status,_,_=await http_json('/api/jinjiang/connect',method='POST',data={});self.assertEqual(status,409)
            status,_,_=await http_json('/api/jinjiang/cancel-connection',method='POST',data={'challenge_id':[]});self.assertEqual(status,400)
            login.assert_not_awaited()
