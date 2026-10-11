"""One reusable, host-owned HTTPS connection for Jinjiang chapter reads."""
import http.client
import ssl
import time
from urllib.request import ProxyHandler, Request, getproxies


ORIGIN='https://app.jjwxc.net'
MAX_RESPONSE=8*1024*1024
DISCONNECTED=(http.client.RemoteDisconnected,http.client.IncompleteRead,
              ConnectionResetError,ConnectionAbortedError,BrokenPipeError,ssl.SSLEOFError)


class ChapterTransport:
    # The caller holds API_REQUEST_LOCK over configuration, request and response.
    def __init__(self):
        self.connection=None
        self.route=None
        self.context=None
        self.last_metrics={}

    def close(self):
        if self.connection is not None:
            self.connection.close()
        self.connection=None
        self.route=None

    def read(self,path,headers):
        started=time.monotonic()
        self.last_metrics={'status':None,'reused_connection':False,'reconnects':0,'connect_s':0.0,'network_s':0.0}
        try:
            request=Request(ORIGIN+path,headers=headers,method='GET')
            # ProxyHandler supplies exactly urllib's environment/macOS proxy and
            # bypass rules, including percent-decoded CONNECT credentials.
            proxies=getproxies()
            proxy=proxies.get('https')
            if proxy:ProxyHandler(proxies).proxy_open(request,proxy,'https')
            request_headers=dict(request.header_items())
            proxy_authorization=request_headers.pop('Proxy-authorization',None)
            route=(request.host,request._tunnel_host,proxy_authorization)
            if self.route!=route:
                self.close()
            self.route=route
            for attempt in range(2):
                reused=self.connection is not None and self.connection.sock is not None
                self.last_metrics['reused_connection']=reused
                if self.connection is None:
                    if self.context is None:self.context=ssl.create_default_context(cafile='/etc/ssl/cert.pem')
                    self.connection=http.client.HTTPSConnection(request.host,timeout=30,context=self.context)
                    if request._tunnel_host:
                        tunnel_headers={'Proxy-Authorization':proxy_authorization} if proxy_authorization else {}
                        self.connection.set_tunnel(request._tunnel_host,headers=tunnel_headers)
                response=None
                try:
                    if not reused:
                        connect_started=time.monotonic()
                        self.connection.connect()
                        self.last_metrics['connect_s']+=time.monotonic()-connect_started
                    self.connection.request('GET',request.selector,headers=request_headers)
                    response=self.connection.getresponse()
                    self.last_metrics['status']=response.status
                    if response.status!=200:raise ValueError('Unexpected HTTP status')
                    length=response.getheader('Content-Length')
                    if length and int(length)>MAX_RESPONSE:raise ValueError('Response too large')
                    content=response.read(MAX_RESPONSE+1)
                    if len(content)>MAX_RESPONSE:raise ValueError('Response too large')
                    if response.length:
                        raise http.client.IncompleteRead(content,response.length)
                    safe_headers='\n'.join(key+':'+response.getheader(key,'') for key in ('accesskey','keystring'))
                    return content,safe_headers
                except DISCONNECTED:
                    self.close()
                    self.route=route
                    if not reused or attempt:raise
                    self.last_metrics['reconnects']=1
                finally:
                    if response is not None:response.close()
        except Exception:
            self.close()
            # Socket, proxy and HTTP errors can embed the Token-bearing path.
            raise ValueError('晋江正文接口暂时不可用。') from None
        finally:
            self.last_metrics['network_s']=time.monotonic()-started
