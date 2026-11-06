#!/usr/bin/env python3
"""Isolated OTLP receiver, source ledger and bounded HPA load. Standard library only.

Run with --self-test locally. In a test Pod set PROBE_RUN, GATEWAY and GRPC_BRIDGE;
POST /start?seconds=600&rate=4000 starts one finite run. GET /stats returns evidence.
"""
import collections
import gzip
import hashlib
import http.client
import http.server
import json
import os
import struct
import sys
import threading
import time
import urllib.parse


RUN = os.environ.get('PROBE_RUN', 'selftest')
PREFIX = hashlib.sha256(RUN.encode()).hexdigest()[:16]
LOCK = threading.RLock()
SEEN = {}
COUNTS = collections.Counter()
METRICS = collections.Counter()
FILES = {}
LEDGER = collections.Counter()
ERRORS = []
STARTED = False
FINISHED = False


def varint(data, offset):
    value = shift = 0
    while True:
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
        shift += 7
        if shift > 63:
            raise ValueError('oversized protobuf varint')


def fields(data):
    result = collections.defaultdict(list)
    offset = 0
    while offset < len(data):
        tag, offset = varint(data, offset)
        wire = tag & 7
        if wire == 0:
            value, offset = varint(data, offset)
        elif wire in (1, 5):
            length = 8 if wire == 1 else 4
            value, offset = data[offset:offset + length], offset + length
            if len(value) != length:
                raise ValueError('truncated protobuf fixed field')
        elif wire == 2:
            length, offset = varint(data, offset)
            value, offset = data[offset:offset + length], offset + length
            if len(value) != length:
                raise ValueError('truncated protobuf bytes')
        else:
            raise ValueError('unsupported protobuf wire type')
        result[tag >> 3].append(value)
    return result


def attrs(entries):
    result = {}
    for raw in entries:
        entry = fields(raw)
        value = fields(entry[2][0])
        result[entry[1][0].decode()] = (value[1][0].decode() if 1 in value else
                                       value[3][0] if 3 in value else None)
    return result


def mark(kind, sequence):
    sequence = int(sequence)
    if sequence < 0 or sequence >= 20000000:
        raise ValueError('source sequence out of bounds')
    seen = SEEN.setdefault(kind, bytearray())
    if len(seen) <= sequence:
        seen.extend(b'\0' * (sequence + 1 - len(seen)))
    if seen[sequence]:
        COUNTS[kind + '_duplicates'] += 1
    else:
        seen[sequence] = 1
        COUNTS[kind] += 1


def receive(signal, data):
    for resource in fields(data)[1]:
        for scope in fields(resource)[2]:
            for raw in fields(scope)[2]:
                record = fields(raw)
                if signal == 'logs':
                    body = fields(record[5][0]).get(1, [b''])[0].decode()
                    if body.startswith('probe:' + RUN + ':'):
                        _, _, kind, sequence, _ = body.split(':', 4)
                        if kind == 'file':
                            FILES[sequence] = hashlib.sha256(body.encode()).hexdigest()
                            COUNTS['file_records'] += 1
                        else:
                            mark(kind, sequence)
                elif signal == 'traces':
                    trace = record[1][0].hex()
                    if trace.startswith(PREFIX):
                        mark('traces', int(trace[16:], 16))
                        if fields(record[15][0]).get(3, [0])[0] != 2:
                            COUNTS['trace_status_corrupted'] += 1
                elif signal == 'metrics':
                    name = record[1][0].decode()
                    METRICS[name] += 1
                    if name not in ('otel_hpa_load', 'otel_hpa_canary'):
                        continue
                    for point in fields(record[5][0])[1]:
                        point = fields(point)
                        labels = attrs(point.get(7, []))
                        if labels.get('test.run') != RUN:
                            continue
                        sequence = labels['seq']
                        value = struct.unpack('<d', point[4][0])[0]
                        if value != sequence:
                            COUNTS['metric_value_corrupted'] += 1
                        mark('load' if name == 'otel_hpa_load' else 'metrics', sequence)


def attribute(key, value):
    return {'key': key, 'value': {'intValue': str(value)} if isinstance(value, int)
            else {'stringValue': value}}


def post(host, path, payload):
    raw = json.dumps(payload, separators=(',', ':')).encode()
    connection = http.client.HTTPConnection(host, timeout=30)
    try:
        connection.request('POST', path, raw, {'Content-Type': 'application/json'})
        response = connection.getresponse()
        body = response.read()
        if response.status != 200:
            raise RuntimeError(f'{response.status}: {body[:200]!r}')
    finally:
        connection.close()


def metric_payload(name, first, count):
    now = str(time.time_ns())
    points = [{'timeUnixNano': now, 'asDouble': sequence,
               'attributes': [attribute('test.run', RUN), attribute('seq', sequence)]}
              for sequence in range(first, first + count)]
    return {'resourceMetrics': [{'resource': {'attributes': [attribute('service.name', 'otel-probe')]},
                                'scopeMetrics': [{'metrics': [{'name': name, 'gauge': {'dataPoints': points}}]}]}]}


def run(seconds, rate):
    global FINISHED
    gateway, bridge = os.environ['GATEWAY'], os.environ['GRPC_BRIDGE']
    deadline = time.monotonic() + seconds

    def load(worker):
        batch, period = 100, 200 / rate
        turn = 0
        while time.monotonic() < deadline:
            first = (turn * 2 + worker) * batch
            # Keep the source range disjoint and record every attempted ID before sending.
            with LOCK:
                LEDGER['load'] += batch
            try:
                post(gateway, '/v1/metrics', metric_payload('otel_hpa_load', first, batch))
            except Exception as error:
                with LOCK:
                    ERRORS.append(str(error))
            turn += 1
            time.sleep(period)

    threads = [threading.Thread(target=load, args=(worker,)) for worker in range(2)]
    for thread in threads:
        thread.start()
    sequence = 0
    while time.monotonic() < deadline:
        now = time.time_ns()
        try:
            for kind, host in [('http', gateway), ('grpc', bridge)]:
                payload = {'resourceLogs': [{'resource': {'attributes': [attribute('service.name', 'otel-probe')]},
                    'scopeLogs': [{'logRecords': [{'timeUnixNano': str(now), 'body': {
                        'stringValue': f'probe:{RUN}:{kind}:{sequence}:中文😀 whitespace \t  '}}]}]}]}
                with LOCK:
                    LEDGER[kind] += 1
                post(host, '/v1/logs', payload)
            with LOCK:
                LEDGER['metrics'] += 1
            post(gateway, '/v1/metrics', metric_payload('otel_hpa_canary', sequence, 1))
            span = {'traceId': PREFIX + f'{sequence:016x}', 'spanId': f'{sequence + 1:016x}',
                    'name': 'error-canary', 'kind': 2, 'startTimeUnixNano': str(now),
                    'endTimeUnixNano': str(now + 1000000), 'status': {'code': 2}}
            with LOCK:
                LEDGER['traces'] += 1
            post(gateway, '/v1/traces', {'resourceSpans': [{'resource': {
                'attributes': [attribute('service.name', 'otel-probe')]}, 'scopeSpans': [{'spans': [span]}]}]})
        except Exception as error:
            with LOCK:
                ERRORS.append(str(error))
        sequence += 1
        time.sleep(1)
    for thread in threads:
        thread.join()
    with LOCK:
        FINISHED = True


def file_cases():
    for sequence, size in enumerate([262144, 1048576, 2097152]):
        prefix, seed = f'probe:{RUN}:file:{sequence}:', 'é中😀 '
        count, remainder = divmod(size - len(prefix.encode()) - 3, len(seed.encode()))
        body = prefix + seed * count + 'x' * remainder + '\t  '
        assert len(body.encode()) == size
        FILES_EXPECTED[str(sequence)] = hashlib.sha256(body.encode()).hexdigest()
        stream = sys.stdout if sequence % 2 == 0 else sys.stderr
        stream.write(body + '\n')
        stream.flush()


FILES_EXPECTED = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/stats':
            with LOCK:
                self.reply({'run': RUN, 'started': STARTED, 'finished': FINISHED,
                            'ledger': dict(LEDGER), 'received': dict(COUNTS),
                            'files': dict(FILES), 'files_expected': dict(FILES_EXPECTED),
                            'metric_names': dict(METRICS), 'errors': ERRORS[:20]})
        elif self.path == '/metrics':
            body = b'# TYPE otel_probe_scraped_total counter\notel_probe_scraped_total 7\n'
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        else:
            self.reply({}, 404)

    def do_POST(self):
        global STARTED
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/start':
            with LOCK:
                if STARTED:
                    self.reply({'error': 'already started'}, 409)
                    return
                args = urllib.parse.parse_qs(parsed.query)
                seconds, rate = int(args.get('seconds', ['600'])[0]), int(args.get('rate', ['4000'])[0])
                if not 1 <= seconds <= 900 or not 1 <= rate <= 10000:
                    self.reply({'error': 'bounded seconds/rate required'}, 400)
                    return
                STARTED = True
            file_cases()
            threading.Thread(target=run, args=(seconds, rate), daemon=True).start()
            self.reply({'started': True})
            return
        try:
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            if self.headers.get('Content-Encoding') == 'gzip':
                raw = gzip.decompress(raw)
            if self.headers.get('Content-Type') != 'application/x-protobuf':
                raise ValueError('receiver expects protobuf from the real Collector')
            with LOCK:
                receive(parsed.path.rsplit('/', 1)[-1], raw)
            self.reply({})
        except Exception as error:
            with LOCK:
                ERRORS.append('receiver: ' + str(error))
            self.reply({'error': str(error)}, 400)


def self_test():
    assert fields(b'\x0a\x03abc\x10\x96\x01') == {1: [b'abc'], 2: [150]}
    assert attrs([b'\x0a\x03seq\x12\x02\x18\x07']) == {'seq': 7}
    mark('check', 3)
    mark('check', 3)
    assert COUNTS['check'] == 1 and COUNTS['check_duplicates'] == 1
    payload = metric_payload('otel_hpa_canary', 10, 2)
    assert payload['resourceMetrics'][0]['scopeMetrics'][0]['metrics'][0]['gauge']['dataPoints'][1]['asDouble'] == 11
    print('PROBE_SELF_TEST_PASS')


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        self_test()
    else:
        http.server.ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
