#!/bin/bash
# 用法: obs.sh [间隔秒数，默认60]
# 观测 new-dev 集群 OTel 采集链关键计数器：两个快照差分算出速率。
# 自动确保 4 个 port-forward 就绪（bash 3.2 兼容，macOS /bin/bash 可用）。
# 覆盖 pod: sampler-0/1 -> 18888/18889, gateway-0/1 -> 18890/18891（各 pod :8888）
SECS=${1:-60}
NS=observability
PODS="otel-sampler-collector-0 otel-sampler-collector-1 otel-gateway-collector-0 otel-gateway-collector-1"

port_for() {
  case "$1" in
    otel-sampler-collector-0) echo 18888 ;;
    otel-sampler-collector-1) echo 18889 ;;
    otel-gateway-collector-0) echo 18890 ;;
    otel-gateway-collector-1) echo 18891 ;;
  esac
}

# 确保 port-forward 就绪
for pod in $PODS; do
  p=$(port_for $pod)
  if ! nc -z 127.0.0.1 "$p" 2>/dev/null; then
    kubectl port-forward -n "$NS" "$pod" "$p:8888" >/dev/null 2>&1 &
    sleep 2
  fi
done

snap() {
  for pod in $PODS; do
    curl -s --noproxy '*' --max-time 8 "http://127.0.0.1:$(port_for $pod)/metrics" 2>/dev/null \
      | grep -E '^otelcol_(receiver_accepted_spans|receiver_refused_spans|processor_tail_sampling_count_traces_sampled|processor_tail_sampling_global_count_traces_sampled|processor_tail_sampling_new_trace_id_received|exporter_queue_size|exporter_send_failed_spans|exporter_sent_spans|processor_refused_spans)' \
      | sed "s/^/${pod} /"
  done
}

snap > /tmp/snap_a.txt
sleep "$SECS"
snap > /tmp/snap_b.txt

python3 - "$SECS" << 'PYEOF'
import sys
secs = float(sys.argv[1])
def load(path):
    d = {}
    for line in open(path):
        parts = line.strip().split(' ', 1)
        if len(parts) != 2:
            continue
        pod, rest = parts
        if ' ' in rest:
            name, val = rest.rsplit(' ', 1)
            try:
                d[(pod, name)] = float(val)
            except ValueError:
                pass
    return d
a, b = load('/tmp/snap_a.txt'), load('/tmp/snap_b.txt')
print(f"{'pod':<26}{'metric':<76}{'value':>12}")
print('-' * 116)
for k in sorted(b):
    name = k[1]
    if 'queue_size' in name:
        print(f"{k[0]:<26}{name:<76}{b[k]:>10.0f}  (gauge)")
    elif k in a:
        print(f"{k[0]:<26}{name:<76}{(b[k]-a[k])/secs:>10.1f}/s")
    else:
        print(f"{k[0]:<26}{name:<76}{'new':>12}")
PYEOF
