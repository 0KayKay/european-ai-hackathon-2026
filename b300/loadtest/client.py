"""Minimal asyncio HTTP/1.1 client for OpenAI-compatible APIs (standard library only).

Keeps connections alive, measures time to first body byte on streams and reads
token usage from the final stream event, without parsing every token event.
"""
import asyncio
import json
import ssl
import time
import urllib.parse


class HttpError(Exception):
    def __init__(self, status, body):
        super().__init__('HTTP %s: %s' % (status, body[:200]))
        self.status = status


class Http:
    def __init__(self, base_url, key, timeout=900):
        url = urllib.parse.urlsplit(base_url)
        self.host = url.hostname
        self.port = url.port or (443 if url.scheme == 'https' else 80)
        self.prefix = url.path.rstrip('/')
        self.ssl = ssl.create_default_context() if url.scheme == 'https' else None
        self.key = key
        self.timeout = timeout
        self.idle = []

    async def _connect(self):
        while self.idle:
            reader, writer = self.idle.pop()
            if not writer.is_closing() and not reader.at_eof():
                return reader, writer, True
            writer.close()
        reader, writer = await asyncio.open_connection(self.host, self.port, ssl=self.ssl, limit=1 << 22)
        return reader, writer, False

    def close(self):
        for _, writer in self.idle:
            writer.close()
        self.idle = []

    async def request(self, path, body, content_type='application/json', on_data=None):
        """POST (or GET when body is None); returns (status, body bytes, first-byte time)."""
        return await asyncio.wait_for(self._request(path, body, content_type, on_data), self.timeout)

    async def _request(self, path, body, content_type, on_data):
        for attempt in (0, 1):
            reader, writer, reused = await self._connect()
            try:
                return await self._exchange(reader, writer, path, body, content_type, on_data)
            except (ConnectionError, asyncio.IncompleteReadError, _Stale):
                writer.close()
                # A kept-alive connection the server already closed: retry once on a fresh one.
                if not reused or attempt:
                    raise
            except BaseException:
                writer.close()
                raise

    async def _exchange(self, reader, writer, path, body, content_type, on_data):
        method = 'GET' if body is None else 'POST'
        head = ['%s %s%s HTTP/1.1' % (method, self.prefix, path), 'Host: %s:%d' % (self.host, self.port),
                'Authorization: Bearer ' + self.key, 'Accept: */*']
        if body is not None:
            head += ['Content-Type: ' + content_type, 'Content-Length: %d' % len(body)]
        writer.write(('\r\n'.join(head) + '\r\n\r\n').encode() + (body or b''))
        await writer.drain()
        line = await reader.readline()
        if not line:
            raise _Stale()
        status = int(line.split()[1])
        headers = {}
        while True:
            line = await reader.readline()
            if line in (b'\r\n', b'\n', b''):
                break
            name, _, value = line.decode('latin-1').partition(':')
            headers[name.strip().lower()] = value.strip()
        first, parts, keep = None, [], headers.get('connection', '').lower() != 'close'
        if headers.get('transfer-encoding', '').lower() == 'chunked':
            while True:
                size = int((await reader.readline()).split(b';')[0], 16)
                if size == 0:
                    while (await reader.readline()) not in (b'\r\n', b'\n', b''):
                        pass
                    break
                data = await reader.readexactly(size)
                await reader.readexactly(2)
                first = first or time.monotonic()
                parts.append(on_data(data) if on_data else data)
        elif 'content-length' in headers:
            remaining = int(headers['content-length'])
            while remaining:
                data = await reader.read(min(remaining, 1 << 20))
                if not data:
                    raise asyncio.IncompleteReadError(b'', remaining)
                first = first or time.monotonic()
                remaining -= len(data)
                parts.append(on_data(data) if on_data else data)
        else:
            keep = False
            while True:
                data = await reader.read(1 << 20)
                if not data:
                    break
                first = first or time.monotonic()
                parts.append(on_data(data) if on_data else data)
        if keep:
            self.idle.append((reader, writer))
        else:
            writer.close()
        return status, b''.join(p for p in parts if p), first


class _Stale(Exception):
    pass


class StreamTail:
    """Keeps only the end of an SSE stream: enough for the usage event and errors."""

    def __init__(self, keep=16384):
        self.keep = keep
        self.tail = b''
        self.events = 0

    def __call__(self, data):
        self.events += data.count(b'data:')
        self.tail = (self.tail + data)[-self.keep:]
        return None

    def usage(self):
        for line in reversed(self.tail.split(b'\n')):
            line = line.strip()
            if line.startswith(b'data:') and b'"usage"' in line:
                try:
                    usage = json.loads(line[5:]).get('usage')
                except ValueError:
                    continue
                if usage:
                    return usage
        return None

    def error(self):
        if b'"error"' in self.tail and b'"usage"' not in self.tail:
            return self.tail[-300:].decode('utf-8', 'replace')
        return None


def multipart(fields, files):
    boundary = 'loadtest%x' % int(time.time() * 1e6)
    out = []
    for name, value in fields.items():
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, name, value)).encode())
    for name, (filename, data, ctype) in files.items():
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: %s\r\n\r\n'
                    % (boundary, name, filename, ctype)).encode() + data + b'\r\n')
    out.append(('--%s--\r\n' % boundary).encode())
    return b''.join(out), 'multipart/form-data; boundary=' + boundary


class Recorder:
    """Appends one JSON line per call or session; the coordinator tails these files."""

    def __init__(self, path, worker, epoch):
        self.handle = open(path, 'a', buffering=1 << 16)
        self.worker = worker
        self.epoch = epoch
        self.stage = lambda: 0

    def write(self, record):
        record['w'] = self.worker
        record.setdefault('stage', self.stage())
        self.handle.write(json.dumps(record, separators=(',', ':')) + '\n')

    def flush(self):
        self.handle.flush()

    def close(self):
        self.handle.close()

    def now(self):
        return time.time() - self.epoch
