#!/usr/bin/env python3
"""Check legacy render preservation, resource budgets and diagnostic selectors.

Requires Helm and PyYAML. Run from any directory; no cluster is contacted.
"""
import itertools
import pathlib
import re
import subprocess

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
CHART = ROOT / 'charts/otel-platform'
LEGACY = ROOT / 'charts/otel-platform-0.7.11.tgz'
BASE = {
    'cluster_name': 'regression', 'operator_metrics': {'enabled': False},
    'trace_endpoint': 'https://traces.test:4318', 'log_endpoint': 'https://logs.test:4318',
    'metrics_endpoint': 'https://metrics.test:4318', 'storage_class': 'test',
    'kafka_brokers': ['broker.test:9092'],
    'credentials': {'kafka': {'KAFKA_USERNAME': 'test', 'KAFKA_PASSWORD': 'test'}},
}


def render(values, chart=CHART, error=None):
    result = subprocess.run(
        ['helm', 'template', 'regression', str(chart), '-n', 'otel-test', '-f', '-'],
        input=yaml.safe_dump({**BASE, **values}), capture_output=True, text=True)
    if error:
        assert result.returncode and error in result.stderr, result.stderr
        return None
    assert result.returncode == 0, result.stderr
    return {o['metadata']['name']: o for o in yaml.safe_load_all(result.stdout) if o}


def collector(objects, role):
    return objects['otel-' + role]['spec']


def main():
    defaults = yaml.safe_load((CHART / 'values.yaml').read_text())
    sample = yaml.safe_load((ROOT / 'values-platform.yaml').read_text())
    def check_sample(actual, expected):
        if isinstance(actual, dict):
            for key, value in actual.items():
                check_sample(value, expected[key])
        else:
            assert actual == expected, (actual, expected)
    check_sample(sample, defaults)
    print('VISIBLE_DEFAULTS_MATCH_PASS', flush=True)
    for protocol, persistent, transport, hpa in itertools.product(
            ['otlp_http', 'otlp_grpc', 'prometheus_remote_write'],
            [False, True], ['direct', 'kafka'], [False, True]):
        values = {'metrics_protocol': protocol, 'persistent_queue': persistent,
                  'logs_transport': transport, 'autoscaling': {'gateway': {'enabled': hpa}}}
        if protocol == 'otlp_grpc':
            values['metrics_endpoint'] = 'metrics.test:4317'
        expected = render(values, LEGACY)
        # Numeric byte limits and byte-size strings describe the same parser limit.
        collector(expected, 'node')['config']['receivers']['filelog']['operators'][0]['max_log_size'] = 262144
        assert render(values) == expected, values
    print('LEGACY_RENDER_EQUIVALENCE_PASS 24', flush=True)

    for size, count in [('256KiB', 262144), ('2MiB', 2097152)]:
        spec = collector(render({'filelog_max_log_size': size}), 'node')
        receiver = spec['config']['receivers']['filelog']
        assert receiver['max_log_size'] == size
        assert receiver['operators'][0]['max_log_size'] == count
        assert receiver['operators'][2]['max_log_size'] == count
        batch = spec['config']['exporters']['otlp/logs']['sending_queue']['batch']
        assert batch['max_size'] == max(524288, count + 65536)
    render({'filelog_max_log_size': '4MiB'}, error='64KiB')
    render({'filelog_max_log_size': '2MiB', 'exporter_queue_mib': 1}, error='exporter_queue_mib')
    kafka = render({'filelog_max_log_size': '5MiB', 'otlp_max_recv_mib': 8, 'logs_transport': 'kafka'})
    assert collector(kafka, 'node')['config']['exporters']['kafka']['producer']['max_message_bytes'] == 5308416
    assert collector(kafka, 'gateway')['config']['receivers']['kafka']['max_partition_fetch_size'] == 5308416

    for quantity in ['1536Mi', '1.5Gi', '1610612736']:
        spec = collector(render({'collector_resources': {'gateway': {
            'requests': {'cpu': '125m', 'memory': '512Mi'},
            'limits': {'cpu': '1.5', 'memory': quantity}}}}), 'gateway')
        assert spec['resources']['requests'] == {'cpu': '125m', 'memory': '512Mi'}
        assert spec['resources']['limits'] == {'cpu': '1.5', 'memory': quantity}
        limiter = spec['config']['processors']['memory_limiter']
        assert limiter['limit_mib'] == 1228 and limiter['spike_limit_mib'] == 245
        assert next(e['value'] for e in spec['env'] if e['name'] == 'GOMEMLIMIT') == '983MiB'
    for resources in [
            {'requests': {'memory': '5Gi'}}, {'requests': {'cpu': '3'}},
            {'requests': {'cpu': 0}}, {'limits': {'memory': '128Mi'}}]:
        render({'collector_resources': {'gateway': resources}}, error='requires positive requests')
    render({'collector_resources': {'gateway': {'limits': {'memory': '1XB'}}}},
           error='memory must be')
    print('LOG_LIMIT_AND_RESOURCE_BUDGET_PASS', flush=True)

    values = {'diagnostics': {k: {'enabled': True} for k in [
        'cadvisor', 'apiserver', 'kube_state_metrics', 'host_process']}}
    objects = render(values)
    node = collector(objects, 'node')['config']
    cadvisor = node['receivers']['prometheus/cadvisor']['config']['scrape_configs'][0]
    pattern = cadvisor['metric_relabel_configs'][0]['regex']
    for metric in ['container_cpu_cfs_throttled_seconds_total', 'container_memory_working_set_bytes',
                   'container_oom_events_total', 'container_network_receive_bytes_total']:
        assert re.fullmatch(pattern, metric), metric
    assert 'interface' in cadvisor['metric_relabel_configs'][-1]['regex']
    drop = cadvisor['metric_relabel_configs'][2]['regex']
    assert re.fullmatch(drop, 'container_memory_usage_bytes;')
    assert not re.fullmatch(drop, 'container_network_receive_bytes_total;')
    assert node['receivers']['hostmetrics']['scrapers']['process']['metrics']['process.threads']['enabled']
    jobs = collector(objects, 'scraper')['config']['receivers']['prometheus']['config']['scrape_configs']
    jobs = {j['job_name']: j for j in jobs}
    for name, metric in [('diagnostics-apiserver', 'apiserver_storage_objects'),
                         ('diagnostics-kube-state-metrics', 'kube_pod_container_status_restarts_total')]:
        assert re.fullmatch(jobs[name]['metric_relabel_configs'][0]['regex'], metric)
    custom = collector(render({'diagnostics': {'cadvisor': {
        'enabled': True, 'metric_names': ['container_oom_events_total']}}}), 'node')
    assert custom['config']['receivers']['prometheus/cadvisor']['config']['scrape_configs'][0][
        'metric_relabel_configs'][0]['regex'] == 'container_oom_events_total'
    render({'diagnostics': {'cadvisor': {'metric_names': []}}}, error='at least 1')
    print('DIAGNOSTICS_SELECTOR_PASS', flush=True)

    spec = collector(render({'autoscaling': {'gateway': {'enabled': True}},
                             'collector_resources': {'gateway': {'requests': {'cpu': '250m'}}}}), 'gateway')
    assert 'replicas' not in spec
    assert spec['resources']['requests']['cpu'] == '250m'
    assert spec['autoscaler']['behavior']['scaleDown'] == {'selectPolicy': 'Disabled'}
    assert spec['autoscaler']['targetCPUUtilization'] == 70
    print('HPA_RENDER_PASS', flush=True)


if __name__ == '__main__':
    main()
