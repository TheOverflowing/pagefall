"""Connection reuse and proxy behavior without network or real credentials."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import ssl
import threading
import time
import unittest
from unittest.mock import patch

from novelist import jinjiang
from novelist import jinjiang_transport as transport


class Response:
    def __init__(self,body=b'{"content":"fixture"}',status=200,headers=None):
        self.body=body;self.status=status;self.headers=headers or {};self.length=len(body);self.closed=False
    def getheader(self,key,default=None):return self.headers.get(key.lower(),default)
    def read(self,size):
        value=self.body[:size];self.length-=len(value);return value
    def close(self):self.closed=True


class Connection:
    def __init__(self,*responses):
        self.responses=list(responses);self.requests=[];self.sock=None;self.closed=False;self.connects=0;self.tunnel=None
    def connect(self):self.sock=object();self.closed=False;self.connects+=1
    def set_tunnel(self,host,headers):self.tunnel=(host,headers)
    def request(self,method,path,headers):self.requests.append((method,path,headers))
    def getresponse(self):
        value=self.responses.pop(0)
        if isinstance(value,Exception):raise value
        return value
    def close(self):self.closed=True;self.sock=None


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.client=transport.ChapterTransport()
        self.addCleanup(self.client.close)
        self.proxies=patch.object(transport,'getproxies',return_value={}).start()
        self.bypass=patch('urllib.request.proxy_bypass',return_value=False).start()
        self.factory=patch.object(transport.http.client,'HTTPSConnection').start()
        self.addCleanup(patch.stopall)
        self.headers={'User-Agent':'fixture'}
        self.path='/androidapi/chapterContent?token=fixture-private'
    def read(self):return self.client.read(self.path,self.headers)

    def test_two_reads_reuse_the_same_verified_tls_connection(self):
        connection=Connection(Response(),Response())
        self.factory.return_value=connection
        self.read();self.assertFalse(self.client.last_metrics['reused_connection'])
        self.read();self.assertTrue(self.client.last_metrics['reused_connection'])
        self.factory.assert_called_once();self.assertEqual(connection.connects,1)
        self.assertEqual(len(connection.requests),2)
        context=self.factory.call_args.kwargs['context']
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED);self.assertTrue(context.check_hostname)
        self.assertEqual(self.factory.call_args.args,('app.jjwxc.net',))
        self.assertEqual(self.client.last_metrics['status'],200)
        self.assertNotIn('fixture-private',json.dumps(self.client.last_metrics))
        for method,path,headers in connection.requests:
            self.assertEqual(method,'GET');self.assertEqual(path,self.path)
            self.assertFalse(any(key.lower() in {'cookie','connection'} for key in headers))

    def test_stale_reused_socket_reconnects_once(self):
        stale=Connection(Response(),http.client.RemoteDisconnected('fixture-private'))
        fresh=Connection(Response())
        self.factory.side_effect=[stale,fresh]
        self.read();self.read()
        self.assertTrue(stale.closed);self.assertEqual(self.factory.call_count,2)
        self.assertEqual(self.client.last_metrics['reconnects'],1)
        self.assertFalse(self.client.last_metrics['reused_connection'])

    def test_disconnect_on_fresh_connection_does_not_retry(self):
        self.factory.return_value=Connection(http.client.RemoteDisconnected('fixture-private'))
        with self.assertRaises(ValueError) as error:self.read()
        self.factory.assert_called_once();self.assertNotIn('fixture-private',str(error.exception))
        self.assertIsNone(self.client.connection)

    def test_second_disconnect_after_reconnect_stops(self):
        self.factory.side_effect=[Connection(Response(),ConnectionResetError()),Connection(http.client.RemoteDisconnected())]
        self.read()
        with self.assertRaises(ValueError):self.read()
        self.assertEqual(self.factory.call_count,2)

    def test_status_errors_never_redirect_or_retry_even_on_keepalive(self):
        for status in (302,401,403,429,500):
            with self.subTest(status=status):
                self.client.close();self.factory.reset_mock()
                connection=Connection(Response(),Response(status=status,headers={'location':'https://outside.invalid/?token=fixture-private'}))
                self.factory.side_effect=None;self.factory.return_value=connection
                self.read()
                with self.assertRaises(ValueError):self.read()
                self.factory.assert_called_once();self.assertTrue(connection.closed)
                self.assertEqual(len(connection.requests),2)
                self.assertEqual(self.client.last_metrics['status'],status)
                self.assertEqual(self.client.last_metrics['reconnects'],0)

    def test_oversized_header_or_body_is_rejected_and_connection_closed(self):
        for response in (Response(headers={'content-length':str(transport.MAX_RESPONSE+1)}),Response(body=b'x'*(transport.MAX_RESPONSE+1))):
            self.client.close();connection=Connection(response);self.factory.return_value=connection
            with self.assertRaises(ValueError):self.read()
            self.assertTrue(connection.closed);self.assertTrue(response.closed)

    def test_truncated_body_on_reused_connection_can_reconnect(self):
        broken=Response();broken.length+=20
        self.factory.side_effect=[Connection(Response(),broken),Connection(Response())]
        self.read();self.read()
        self.assertEqual(self.client.last_metrics['reconnects'],1)

    def test_proxy_and_auth_use_urllib_connect_semantics(self):
        self.proxies.return_value={'https':'http://reader:p%40ss@proxy.example:8080'}
        connection=Connection(Response());self.factory.return_value=connection
        self.read()
        self.bypass.assert_called_once_with('app.jjwxc.net')
        self.assertEqual(self.factory.call_args.args,('proxy.example:8080',))
        self.assertEqual(connection.tunnel,('app.jjwxc.net',{'Proxy-Authorization':'Basic cmVhZGVyOnBAc3M='}))
        self.assertFalse(any('proxy' in key.lower() for key in connection.requests[0][2]))
        self.assertNotIn('proxy.example',json.dumps(self.client.last_metrics))
        self.assertNotIn('cmVhZGVy',json.dumps(self.client.last_metrics))

    def test_proxy_bypass_and_changed_configuration_replace_the_connection(self):
        direct=Connection(Response());proxied=Connection(Response());again=Connection(Response())
        self.factory.side_effect=[direct,proxied,again]
        self.read()
        self.proxies.return_value={'https':'http://proxy.example:8080'}
        self.read();self.assertTrue(direct.closed)
        self.bypass.return_value=True
        self.read();self.assertTrue(proxied.closed)
        self.assertEqual([call.args[0] for call in self.factory.call_args_list],['app.jjwxc.net','proxy.example:8080','app.jjwxc.net'])
        self.assertIsNone(again.tunnel)

    def test_http_only_proxy_is_not_used_for_https(self):
        self.proxies.return_value={'http':'http://proxy.example:8080'}
        self.factory.return_value=Connection(Response())
        self.read();self.assertEqual(self.factory.call_args.args,('app.jjwxc.net',))
        self.bypass.assert_not_called()

    def test_tls_verification_failure_does_not_retry_or_expose_request(self):
        connection=Connection()
        self.factory.return_value=connection
        with patch.object(connection,'connect',side_effect=ssl.SSLCertVerificationError('fixture-private')):
            with self.assertRaises(ValueError) as error:self.read()
        self.factory.assert_called_once();self.assertNotIn('fixture-private',str(error.exception))

    def test_proxy_configuration_errors_are_redacted(self):
        self.proxies.side_effect=ValueError('fixture-proxy-password')
        with self.assertRaises(ValueError) as error:self.read()
        self.assertNotIn('fixture-proxy-password',str(error.exception))
        self.factory.assert_not_called()


class SharedTransportTests(unittest.TestCase):
    def test_api_serializes_whole_read_and_close_exposes_no_credentials(self):
        active=0;maximum=0;guard=threading.Lock()
        chapter={'id':'1','url':'https://www.jjwxc.net/onebook.php?novelid=42&chapterid=1'}
        def read(path,headers):
            nonlocal active,maximum
            with guard:active+=1;maximum=max(maximum,active)
            time.sleep(.01)
            with guard:active-=1
            return b'fixture',''
        with patch.object(jinjiang._API_TRANSPORT,'read',side_effect=read),patch.object(jinjiang,'API_REQUEST_INTERVAL',0),patch.object(jinjiang._API_TRANSPORT,'last_metrics',{'status':200,'network_s':.01}):
            with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:jinjiang.api_response(chapter,'fixture-private'),range(4)))
        self.assertEqual(maximum,1);self.assertNotIn('fixture-private',json.dumps(jinjiang.API_LAST_METRICS))
        with patch.object(jinjiang._API_TRANSPORT,'close') as close:jinjiang.close_api_transport()
        close.assert_called_once()
