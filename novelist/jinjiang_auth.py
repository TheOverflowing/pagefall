"""Native account connection adapted from the pinned upstream Jjwxc constructor.

AGPL-3.0-or-later; see engine/NOTICES.md. Passwords/codes are request-local;
only the resulting Token is saved, through the existing Keychain helper.
"""
import asyncio
import base64
from dataclasses import dataclass
import json
from pathlib import Path
import re
import secrets
import ssl
import time
from urllib.parse import urlencode
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener

from . import jinjiang

LOGIN_URL = 'https://app.jjwxc.org/androidapi/login'
VERIFY_URL = 'https://app.jjwxc.org//appDevicesecurityAndroid/getDeviceSecurityCode'
NETWORK_ERROR = '暂时无法连接晋江，请稍后重试。'
EXPIRED = '本次验证已过期，请重新连接。'


def encode(value):
    # Same DES-CBC / PKCS7 and raw key/IV as upstream CryptoJS. HTTPS remains on.
    from Cryptodome.Cipher import DES
    from Cryptodome.Util.Padding import pad
    cipher = DES.new(b'KW8Dvm2N', DES.MODE_CBC, b'1ae2c94b')
    return base64.b64encode(cipher.encrypt(pad(value.encode('utf-8'), 8))).decode('ascii')


def send(endpoint, values):
    """Fixed HTTPS endpoints only; no cookies, redirects, raw errors or logging."""
    if endpoint not in (LOGIN_URL, VERIFY_URL):
        raise ValueError(NETWORK_ERROR)
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    data = urlencode(values).encode('ascii')
    headers = {'User-Agent': 'Mobile '+str(int(time.time()*1000)), 'Accept': 'application/json'}
    if endpoint == LOGIN_URL:
        request = Request(endpoint+'?'+data.decode('ascii'), headers=headers, method='GET')
    else:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        request = Request(endpoint, data=data, headers=headers, method='POST')
    try:
        opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile='/etc/ssl/cert.pem')))
        with opener.open(request, timeout=20) as response:
            raw = response.read(65537)
        if len(raw)>65536:
            raise ValueError()
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except Exception:
        # HTTP exceptions can contain credential-bearing URLs. Never propagate.
        raise ValueError(NETWORK_ERROR) from None


@dataclass(repr=False)
class Challenge:
    identifier: str
    account: str
    delivery: str
    created: float
    sent: float = 0
    attempts: int = 0


class Connection:
    def __init__(self, device_path):
        self.device_path = Path(device_path)
        self.challenge = None
        self.lock = asyncio.Lock()
        self.next_login = 0

    @property
    def busy(self):
        return self.lock.locked()

    def device(self):
        try:
            value = json.loads(self.device_path.read_text())['identifier']
            if re.fullmatch(r'\d{20}:[0-9a-f]{16}d4:', value):
                return value
        except (OSError, ValueError, KeyError, TypeError):
            pass
        value = ''.join(secrets.choice('0123456789') for _ in range(20))+':'+secrets.token_hex(8)+'d4:'
        self.device_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Device identity only, never the account, password, code or Token.
        temporary = self.device_path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'identifier': value}))
        temporary.chmod(0o600)
        temporary.replace(self.device_path)
        return value

    def current(self, challenge_id):
        challenge = self.challenge
        if not challenge or not isinstance(challenge_id, str) or not secrets.compare_digest(challenge.identifier, challenge_id) or time.monotonic()-challenge.created>300 or challenge.attempts>=5:
            self.challenge = None
            raise ValueError(EXPIRED)
        return challenge

    def clear(self, challenge_id=None):
        if challenge_id is None or (self.challenge and secrets.compare_digest(self.challenge.identifier, challenge_id)):
            self.challenge = None

    async def login(self, data):
        account, password = data.get('account'), data.get('password')
        code, challenge_id = data.get('code', ''), data.get('challenge_id')
        if not isinstance(account, str) or not 1<=len(account.strip())<=254 or re.search(r'[\s\x00-\x1f\x7f]',account.strip()) or not isinstance(password, str) or not 1<=len(password)<=1024:
            raise ValueError('请输入晋江账号和密码。')
        account = account.strip()
        if not isinstance(code, str) or (code and not re.fullmatch(r'[A-Za-z0-9]{4,12}',code)):
            raise ValueError('请输入有效的验证码。')
        if bool(code) != bool(challenge_id):
            raise ValueError(EXPIRED)
        if self.busy:
            raise ValueError('正在连接晋江，请稍候。')
        async with self.lock:
            if time.monotonic()<self.next_login:
                raise ValueError('操作太快，请稍后重试。')
            self.next_login = time.monotonic()+2
            challenge = self.current(challenge_id) if code else None
            if challenge and challenge.account != account:
                self.clear()
                raise ValueError(EXPIRED)
            if challenge:
                challenge.attempts+=1
            else:
                self.clear()
            device = self.device()
            values = {'versionCode':'402','loginName':account,'encode':'1','loginPassword':encode(password),'sign':encode(str(int(time.time()*1000))+'_'+device+'_'),'identifiers':device,'autologin':'1'}
            if challenge:
                values.update(checktype=challenge.delivery,checkdevicecode=code)
            result = await asyncio.to_thread(send,LOGIN_URL,values)
            token = result.get('token')
            if isinstance(token,str) and re.fullmatch(r'[0-9]+_[A-Za-z0-9_]{16,4000}',token):
                await asyncio.to_thread(jinjiang.save_token, token)
                jinjiang.token_expired = False
                self.clear()
                return {'state':'connected'}
            if str(result.get('code'))=='221003' and not challenge:
                self.challenge = Challenge(secrets.token_urlsafe(32),account,'email' if '@' in account else 'phone',time.monotonic())
                asyncio.get_running_loop().call_later(300,self.clear,self.challenge.identifier)
                return {'state':'verification_required','challenge_id':self.challenge.identifier,'delivery':self.challenge.delivery}
            # Map only known meanings; upstream response text may contain secrets/URLs.
            message = str(result.get('message',''))[:500]
            if challenge:
                raise ValueError('验证码未通过，请检查后重试。')
            if re.search(r'密码|账号不存在|用户不存在|账户不存在',message):
                raise ValueError('账号或密码未通过，请检查后重试。')
            if re.search(r'主设备|安全验证|设备验证',message):
                raise ValueError('晋江要求额外设备验证，请先在官方 App 完成验证后重试。')
            raise ValueError('晋江暂未允许连接，请稍后重试；网页下载仍可使用。')

    async def verification(self, challenge_id):
        if self.busy:
            raise ValueError('正在连接晋江，请稍候。')
        async with self.lock:
            challenge = self.current(challenge_id)
            wait = 60-(time.monotonic()-challenge.sent) if challenge.sent else 0
            if wait>0:
                raise ValueError('验证码已请求，请稍后再发送。')
            challenge.sent = time.monotonic()
            result = await asyncio.to_thread(send,VERIFY_URL,{'versionCode':'402','username':challenge.account,'checktype':challenge.delivery})
            info = result.get('data')
            message = info.get('message','') if isinstance(info,dict) else ''
            message = str(message or result.get('message',''))[:500]
            # Unknown acknowledgements are not a failure: the code may already
            # have arrived. Distinguish submitted from explicitly confirmed.
            failed = bool(re.search(r'失败|未发送|不能|无法|不成功|错误|无效|不正确',message))
            if re.search(r'频繁|间隔|次数',message):
                raise ValueError('验证码请求受限，请稍后重试。')
            if failed:
                raise ValueError('验证码未能确认发送，请稍后重试或在官方 App 完成验证。')
            if re.search(r'发送.{0,8}成功|(?:已经|已).{0,6}发送|发送了|发送至|发送到|查收|获取.{0,4}成功',message):
                return {'state':'code_sent','retry_after':60}
            return {'state':'code_requested','retry_after':60}
