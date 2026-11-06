#!/usr/bin/env python3
"""Run the rendered Chart's log operators and Gateway connection policy on a real Collector.

Requires Python 3, PyYAML, grpcio, Helm and the same otelcol-contrib version as the Chart.
Usage: python3 tests/collector-regression.py --collector /path/to/otelcol-contrib
"""
import argparse
import copy
import http.client
import json
import pathlib
import select
import socket
import subprocess
import tempfile
import threading
import time

import grpc
import yaml


def render(chart, protocol="otlp_http", persistent=False, overrides=None):
    command = ["helm", "template", "regression", str(chart), "--namespace", "otel-test",
               "--set", "cluster_name=regression,operator_metrics.enabled=false",
               "--set", "trace_endpoint=https://traces.test:4318,log_endpoint=https://logs.test:4318",
               "--set", "metrics_endpoint=https://metrics.test:4318",
               "--set", "metrics_protocol=" + protocol,
               "--set", "persistent_queue=" + str(persistent).lower(),
               "--set", "storage_class=test"]
    if protocol == "otlp_grpc":
        command += ["--set", "metrics_endpoint=metrics.test:4317"]
    command += ["-f", "-"]
    result = subprocess.run(command, input=yaml.safe_dump(overrides or {}),
                            check=True, capture_output=True, text=True)
    return {o["metadata"]["name"]: o["spec"]["config"]
            for o in yaml.safe_load_all(result.stdout)
            if o and o["kind"] == "OpenTelemetryCollector"}


def launch(binary, directory, config):
    path = directory / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    subprocess.run([binary, "validate", "--config", str(path)], check=True, capture_output=True)
    log = (directory / "collector.log").open("w")
    process = subprocess.Popen([binary, "--config", str(path)], stdout=log, stderr=log)
    log.close()
    return process


def stop(process):
    process.terminate()
    process.wait(timeout=15)


def records(path):
    result = []
    if path.exists():
        lines = path.read_bytes().splitlines()
        for index, line in enumerate(lines):
            try:
                data = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                if index == len(lines) - 1:
                    continue  # The exporter may still be writing the last line.
                raise
            for resource in data.get("resourceLogs", []):
                for scope in resource.get("scopeLogs", []):
                    result += scope.get("logRecords", [])
    return result


def wait_for(predicate, process, directory, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if process.poll() is not None:
            raise AssertionError((directory / "collector.log").read_text())
        time.sleep(.1)
    raise AssertionError("Timed out; " + (directory / "collector.log").read_text()[-3000:])


def log_test(binary, config, directory, max_size=262144, queued=False):
    directory.mkdir()
    pod = directory / "apps_demo_12345678-1234-1234-1234-123456789abc" / "app"
    pod.mkdir(parents=True)
    source, output = pod / "0.log", directory / "logs.json"
    expected = []
    wire = bytearray()
    # Include two-, three- and four-byte characters, each possible split in one
    # character, 16 KiB CRI boundaries, JSON, ASCII and near-limit messages.
    for index, payload in enumerate([
        "plain English", "中文边界测试" * 1000, "中文边界测试" * 14000,
        "mixed é中😀" * 5000, "x" * 200000,
        json.dumps({"message": "中文😀" * 7000}, ensure_ascii=False),
        *["prefix-" + "中" * 6000 for _ in range(2)],
        *["prefix-" + "😀" * 5000 for _ in range(3)],
        "x" * 16375 + " " + "continued after CRI boundary",
        "trailing whitespace \t  ",
    ]):
        body = f"case-{index}:" + payload
        expected.append(body)
        raw = body.encode()
        if 6 <= index <= 10:
            char = "中" if index < 8 else "😀"
            offset = raw.index(char.encode()) + (index - 6 + 1 if index < 8 else index - 8 + 1)
            chunks = [raw[:offset], raw[offset:]]
        else:
            chunks = [raw[i:i + 16384] for i in range(0, len(raw), 16384)]
        for i, chunk in enumerate(chunks):
            flag = b"F" if i == len(chunks) - 1 else b"P"
            wire += b"2026-11-05T00:00:00.000000000Z stdout " + flag + b" " + chunk + b"\n"
    if max_size > 262144:
        prefix, seed = f"case-{len(expected)}:", "é中😀 "
        count, remainder = divmod(max_size - len(prefix.encode()), len(seed.encode()))
        body = prefix + seed * count + "x" * remainder
        assert len(body.encode()) == max_size
        expected.append(body)
        raw = body.encode()
        chunks = [raw[i:i + 16384] for i in range(0, len(raw), 16384)]
        for i, chunk in enumerate(chunks):
            flag = b"F" if i == len(chunks) - 1 else b"P"
            wire += b"2026-11-05T00:00:00.000000000Z stdout " + flag + b" " + chunk + b"\n"
    source.write_bytes(wire)
    receiver = copy.deepcopy(config["receivers"]["filelog"])
    receiver.update(include=[str(pod / "*.log*")], exclude=[], start_at="beginning")
    receiver.pop("storage", None)
    test_config = {
        "receivers": {"filelog": receiver},
        "exporters": {"file": {"path": str(output), "flush_interval": "100ms"}},
        "service": {"telemetry": {"metrics": {"level": "none"}}, "pipelines": {
            "logs": {"receivers": ["filelog"], "exporters": ["file"]}}},
    }
    sink = None
    if queued:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        sink_directory = directory / "sink"
        sink_directory.mkdir()
        sink_config = {
            "receivers": {"otlp": {"protocols": {"grpc": {"endpoint": f"127.0.0.1:{port}"}}}},
            "exporters": test_config["exporters"],
            "service": {"telemetry": {"metrics": {"level": "none"}}, "pipelines": {
                "logs": {"receivers": ["otlp"], "exporters": ["file"]}}},
        }
        sink = launch(binary, sink_directory, sink_config)
        exporter = copy.deepcopy(config["exporters"]["otlp/logs"])
        exporter["endpoint"] = f"127.0.0.1:{port}"
        exporter["sending_queue"].pop("storage", None)
        test_config["exporters"] = {"otlp": exporter}
        test_config["service"]["pipelines"]["logs"]["exporters"] = ["otlp"]
    try:
        process = launch(binary, directory, test_config)
    except Exception:
        if sink:
            stop(sink)
        raise
    try:
        wait_for(lambda: len(records(output)) >= len(expected), process, directory)
        source.rename(pod / "0.log.1")
        rotated = "after-rotation:" + "中文😀" * 2000
        expected.append(rotated)
        source.write_bytes(b"2026-11-05T00:00:01.000000000Z stderr F " + rotated.encode() + b"\n")
        wait_for(lambda: len(records(output)) >= len(expected), process, directory)
    finally:
        stop(process)
        if sink:
            stop(sink)
    actual = [entry["body"]["stringValue"] for entry in records(output)]
    assert sorted(actual) == sorted(expected), {
        "expected_records": len(expected), "actual_records": len(actual),
        "replacement_count": sum(value.count("\ufffd") for value in actual),
    }
    return {"records": len(actual), "missing": 0, "duplicates": 0, "corrupted": 0}


def http_test(binary, config, directory):
    directory.mkdir()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    http_config = copy.deepcopy(config["receivers"]["otlp"]["protocols"]["http"])
    http_config["endpoint"] = f"127.0.0.1:{port}"
    test_config = {
        "receivers": {"otlp": {"protocols": {"http": http_config}}},
        "exporters": {"nop": {}},
        "service": {"telemetry": {"metrics": {"level": "none"}}, "pipelines": {
            "logs": {"receivers": ["otlp"], "exporters": ["nop"]}}},
    }
    process = launch(binary, directory, test_config)
    # Keep one client object alive: a closed connection must be reopened by it.
    client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        def listening():
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.1):
                    return True
            except OSError:
                return False
        wait_for(listening, process, directory)
        for _ in range(5):
            client.request("POST", "/v1/logs", b"{}", {"Content-Type": "application/json"})
            response = client.getresponse()
            response.read()
            assert response.status == 200, response.status
            assert response.will_close, "Gateway retained HTTP connection; scaling cannot redistribute this client"
    finally:
        client.close()
        stop(process)
    return {"successful_requests": 5, "connections_released": 5}


def grpc_test(binary, config, directory):
    directory.mkdir()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    grpc_config = copy.deepcopy(config["receivers"]["otlp"]["protocols"]["grpc"])
    grpc_config["endpoint"] = f"127.0.0.1:{port}"
    test_config = {
        "receivers": {"otlp": {"protocols": {"grpc": grpc_config}}},
        "exporters": {"nop": {}},
        "service": {"telemetry": {"metrics": {"level": "none"}}, "pipelines": {
            "logs": {"receivers": ["otlp"], "exporters": ["nop"]}}},
    }
    process = launch(binary, directory, test_config)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(.2)
    connections, shutdown = [], threading.Event()

    def forward(client):
        with client, socket.create_connection(("127.0.0.1", port), timeout=5) as upstream:
            while not shutdown.is_set():
                readable, _, _ = select.select([client, upstream], [], [], .2)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is client else client).sendall(data)

    def accept():
        while not shutdown.is_set():
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            connections.append(time.monotonic())
            threading.Thread(target=forward, args=(client,), daemon=True).start()

    channel = grpc.insecure_channel(f"127.0.0.1:{listener.getsockname()[1]}")
    worker = threading.Thread(target=accept)
    try:
        def listening():
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.1):
                    return True
            except OSError:
                return False
        wait_for(listening, process, directory)
        worker.start()
        rpc = channel.unary_unary("/opentelemetry.proto.collector.logs.v1.LogsService/Export")
        deadline, calls, after_reconnect = time.monotonic() + 80, 0, 0
        while time.monotonic() < deadline and after_reconnect < 5:
            rpc(b"", timeout=5)
            calls += 1
            if len(connections) >= 2:
                after_reconnect += 1
            time.sleep(.2)
        assert after_reconnect == 5, f"gRPC connection never renewed: {len(connections)} connections"
    finally:
        shutdown.set()
        channel.close()
        if worker.ident is not None:
            worker.join(timeout=2)
        listener.close()
        stop(process)
    return {"successful_requests": calls, "connections": len(connections),
            "renewal_seconds": round(connections[1] - connections[0], 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collector", required=True)
    parser.add_argument("--chart", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[1] / "charts/otel-platform")
    args = parser.parse_args()
    config = render(args.chart)
    with tempfile.TemporaryDirectory(prefix="otel-regression-") as temporary:
        directory = pathlib.Path(temporary)
        print("UTF8_ROTATION_PASS", json.dumps(log_test(args.collector, config["otel-node"], directory / "logs")), flush=True)
        large = render(args.chart, overrides={"filelog_max_log_size": "2MiB",
            "log_processing": {"filelog": {"recombine": {
                "if": "true", "is_first_entry": 'body matches "^(case-|after-rotation:)"'}}}})
        print("LARGE_LOG_PASS", json.dumps(log_test(args.collector, large["otel-node"],
              directory / "large-logs", max_size=2097152, queued=True)), flush=True)
        print("HTTP_RECONNECT_PASS", json.dumps(http_test(args.collector, config["otel-gateway"], directory / "http")), flush=True)
        print("GRPC_RECONNECT_PASS", json.dumps(grpc_test(args.collector, config["otel-gateway"], directory / "grpc")), flush=True)
    for protocol in ["otlp_http", "otlp_grpc", "prometheus_remote_write"]:
        for persistent in [False, True]:
            current = render(args.chart, protocol, persistent)
            assert current["otel-node"]["receivers"]["filelog"]["encoding"] == "utf-8-raw"
            assert current["otel-node"]["receivers"]["filelog"]["preserve_trailing_whitespaces"] is True
            gateway = current["otel-gateway"]["receivers"]["otlp"]["protocols"]
            assert gateway["http"]["keepalive"] == {"enabled": False}
            assert gateway["grpc"]["keepalive"]["server_parameters"] == {
                "max_connection_age": "60s", "max_connection_age_grace": "30s"}
            assert "keepalive" not in current["otel-sampler"]["receivers"]["otlp"]["protocols"]["grpc"]
            assert current["otel-gateway"]["exporters"]["load_balancing/traces"]["routing_key"] == "traceID"
    print("RENDER_MATRIX_PASS 6")


if __name__ == "__main__":
    main()
