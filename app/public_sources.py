"""Public original sources with pinned HTTPS connections and immutable extracts.

Fetching proves retrieval and quote integrity, never the truth of an assertion.
HTML is retained as an attachment and is never rendered as active page content.
"""
import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from pydantic import Field
from app import config
from app.schemas import Inputs
from app.db import now, one


class SourceFetch(Inputs):
    url: str = Field(min_length=8, max_length=4000, description='原始公开HTTPS页面或PDF地址；不支持登录、内网或绕过付费访问')
    purpose: str = Field(min_length=1, max_length=1000, description='该来源拟支持的工程依据或待核对问题')


def public_target(url):
    try:
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.username is not None or parts.password is not None or parts.port not in {None, 443}:
            raise ValueError('仅允许无认证信息的公开HTTPS/443来源')
        hostname = parts.hostname.encode('idna').decode('ascii') if parts.hostname else ''
        if not hostname or any(c.isspace() or ord(c) < 32 for c in url) or '\\' in url:
            raise ValueError('来源URL无效')
        addresses = list(dict.fromkeys(r[4][0] for r in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)))
        parsed_addresses = [ipaddress.ip_address(address) for address in addresses]
        if not addresses or any(not address.is_global or address.is_multicast or address.is_unspecified
                                or address.is_loopback or address.is_link_local or address.is_reserved
                                for address in parsed_addresses):
            raise ValueError('来源解析到非公开地址，拒绝连接')
        if any(isinstance(address, ipaddress.IPv6Address) and address.sixtofour is not None
               for address in parsed_addresses):
            raise ValueError('拒绝可能封装非公开IPv4目标的6to4地址')
        host = '[' + hostname + ']' if ':' in hostname else hostname
        canonical = urlunsplit(('https', host, parts.path or '/', parts.query, ''))
        target = urlunsplit(('', '', parts.path or '/', parts.query, ''))
        return hostname, addresses, canonical, target
    except (UnicodeError, OSError, ValueError) as exc:
        raise ValueError('公开来源地址校验失败：' + str(exc)[:200]) from exc


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname, address, timeout):
        super().__init__(hostname, port=443, timeout=timeout, context=ssl.create_default_context())
        self.address = address
        self.raw_socket = None

    def connect(self):
        # The validated IP is used directly. No second hostname resolution,
        # environment proxy, cookie jar or redirect handler can change it.
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        self.raw_socket = raw
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def _abort_connection(connection):
    """Interrupt slow-drip headers/body reads even while bytes keep arriving."""
    for stream in (connection.sock, getattr(connection, 'raw_socket', None)):
        if stream is not None:
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    connection.close()


def fetch_public(url, *, timeout=45, max_bytes=None):
    deadline = time.monotonic() + timeout
    max_bytes = config.MAX_UPLOAD if max_bytes is None else min(max_bytes, config.MAX_UPLOAD)
    if max_bytes < 1:
        raise ValueError('公开来源读取大小上限无效')
    chain = []
    for redirect in range(6):
        hostname, addresses, canonical, target = public_target(url)
        last_error = None
        for address in addresses[:4]:
            if time.monotonic() >= deadline:
                raise ValueError('公开来源请求超过45秒限制')
            connection = PinnedHTTPS(hostname, address, max(.1, deadline-time.monotonic()))
            timer = threading.Timer(max(.1, deadline-time.monotonic()), _abort_connection, args=(connection,))
            timer.daemon = True
            timer.start()
            try:
                connection.request('GET', target, headers={'User-Agent': 'ChemELLM/0.1 source verification',
                    'Accept': 'text/html,application/pdf,text/plain;q=0.9', 'Accept-Encoding': 'identity'})
                response = connection.getresponse()
                headers = {k.lower(): v for k, v in response.getheaders()}
                chain.append({'url': canonical, 'peer_ip': address, 'status': response.status})
                if response.status in {301, 302, 303, 307, 308}:
                    if redirect == 5 or not headers.get('location'):
                        raise ValueError('来源重定向过多或没有目标地址')
                    url = urljoin(canonical, headers['location'])
                    break
                if response.status != 200:
                    raise ValueError('公开来源HTTP ' + str(response.status) + '；未用题录或生成内容替代原文')
                if headers.get('content-encoding', 'identity').lower() not in {'', 'identity'}:
                    raise ValueError('来源未遵守identity编码；拒绝未受限解压')
                if headers.get('content-length', '').isdigit() and int(headers['content-length']) > max_bytes:
                    raise ValueError('来源原件超过25 MiB限制')
                chunks, size = [], 0
                while True:
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        raise ValueError('公开来源读取超过45秒限制')
                    if connection.sock:
                        connection.sock.settimeout(remaining)
                    # read1 performs a bounded buffered read rather than waiting
                    # to fill an entire chunk. A watchdog also interrupts slow
                    # headers/chunk framing which per-socket timeouts cannot bound.
                    chunk = response.read1(min(65536, max_bytes + 1-size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError('来源原件超过25 MiB限制')
                    chunks.append(chunk)
                return {'content': b''.join(chunks), 'final_url': canonical, 'redirects': chain,
                        'headers': {k: headers[k] for k in ('content-type', 'content-length', 'last-modified', 'etag', 'date') if k in headers}}
            except (OSError, http.client.HTTPException) as exc:
                last_error = type(exc).__name__
            finally:
                timer.cancel()
                connection.close()
        else:
            raise ValueError('公开来源连接失败：' + str(last_error))
    raise ValueError('公开来源重定向失败')


class SourceHTML(HTMLParser):
    boundaries = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'li', 'tr', 'section', 'article', 'header', 'footer', 'br', 'dt', 'dd'}
    ignored = {'script', 'style', 'noscript', 'template', 'svg', 'canvas'}

    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.suppressed = []
        self.parts, self.blocks, self.links, self.licenses = [], [], [], []
        self.heading = ''
        self.title_parts = []
        self.in_title = False
        self.anchor = None
        self.text_size = 0

    def flush(self):
        text = re.sub(r'\s+', ' ', ''.join(self.parts)).strip()
        self.parts = []
        if text:
            self.text_size += len(text)
            if len(self.blocks) >= 10000 or self.text_size > 2_000_000:
                raise ValueError('网页可定位文本超过提取限制；不截断原文来制造不完整快照')
            self.blocks.append({'id': 'b'+str(len(self.blocks)+1).zfill(5), 'kind': 'html_text', 'text': text,
                                'locator': {'page': None, 'section': self.heading, 'paragraph': len(self.blocks)+1}, 'ocr': False})

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag in self.ignored:
            self.suppressed.append(tag)
        if self.suppressed:
            return
        if tag in self.boundaries:
            self.flush()
        if tag in {'td', 'th'}:
            self.parts.append(' | ')
        if tag == 'title':
            self.in_title = True
        if tag == 'a' and attributes.get('href'):
            address = urljoin(self.base_url, attributes['href'])
            if address.startswith('https://'):
                self.anchor = {'url': address, 'text': ''}
                if 'license' in attributes.get('rel', '').lower().split():
                    self.licenses.append(address)

    def handle_endtag(self, tag):
        if self.suppressed:
            # Track nested ignored tags by depth; malformed pages with a nested
            # script/template must not leak active content into the text extract.
            if tag in self.ignored:
                for index in range(len(self.suppressed)-1, -1, -1):
                    if self.suppressed[index] == tag:
                        del self.suppressed[index:]
                        break
            return
        if re.fullmatch('h[1-4]', tag):
            self.heading = re.sub(r'\s+', ' ', ''.join(self.parts)).strip()
        if tag in self.boundaries:
            self.flush()
        if tag == 'title':
            self.in_title = False
            self.flush()
        if tag == 'a' and self.anchor:
            if len(self.links) < 1000 and self.anchor not in self.links:
                self.links.append(self.anchor)
            self.anchor = None

    def handle_data(self, data):
        if self.suppressed:
            return
        self.parts.append(data)
        if self.in_title:
            self.title_parts.append(data)
        if self.anchor:
            self.anchor['text'] = (self.anchor['text']+data)[:500]


def extract_html(content, base_url='', content_type=''):
    prefix = content[:4096].decode('ascii', errors='ignore')
    charsets = re.findall(r'charset\s*=\s*["\']?([a-zA-Z0-9._-]+)', content_type + ' ' + prefix, re.I)
    encoding = charsets[0] if charsets else 'utf-8-sig'
    try:
        text = content.decode(encoding)
    except (LookupError, UnicodeError) as exc:
        raise ValueError('来源网页编码不可可靠识别；保留原URL，不伪造转录') from exc
    parser = SourceHTML(base_url)
    parser.feed(text)
    parser.close()
    parser.flush()
    return {'blocks': parser.blocks, 'title': ''.join(parser.title_parts).strip(), 'links': parser.links,
            'license_text': parser.licenses, 'encoding': encoding, 'extractor': 'stdlib.HTMLParser-v1',
            'warnings': ['网页提取保留段落序号和标题，不伪造页码；脚本内容不执行，动态页面可能不完整。',
                         '许可链接只表示原文出现的标记；未确认授权时许可状态保留unknown。']}


def import_source(project_id, p):
    from app.documents import ingest, public_artifact
    from app.fulltext import ExtractInput, extract_document
    fetched = fetch_public(p.url)
    content = fetched['content']
    content_type = fetched['headers'].get('content-type', '')
    if content.startswith(b'%PDF-'):
        suffix = '.pdf'
    elif 'html' in content_type.lower() or content.lstrip().lower().startswith((b'<!doctype html', b'<html')):
        suffix = '.html'
    elif content_type.lower().startswith('text/plain'):
        suffix = '.txt'
    else:
        raise ValueError('仅支持可核对的原始HTML/PDF/UTF-8文本；来源返回了其他格式')
    source_metadata = {'public_source': {'requested_url': p.url, 'source_url': fetched['final_url'],
                       'retrieved_at': now(), 'purpose': p.purpose, 'redirects': fetched['redirects'], 'headers': fetched['headers'],
                       'license_status': 'unknown', 'authenticity_review': 'not_reviewed'}}
    parsed_html = extract_html(content, fetched['final_url'], content_type) if suffix == '.html' else None
    record = ingest(project_id, 'source-'+hashlib.sha256(content).hexdigest()[:16]+suffix, content,
                    html_extraction=parsed_html, metadata_extra=source_metadata)
    extraction = extract_document(project_id, ExtractInput(artifact_id=record['id']))
    return {'artifact': public_artifact(one('SELECT * FROM artifacts WHERE id=?', (record['id'],))),
            'extraction': extraction, 'status': 'retrieved_not_verified'}
