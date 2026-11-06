{{- define "otel.diagnostics" -}}
{{- $root := .root -}}{{- $spec := .spec -}}{{- $d := $root.Values.diagnostics -}}
{{- if eq .name "otel-node" -}}
  {{- if $d.cadvisor.enabled -}}
    {{- $job := dict "job_name" "diagnostics-cadvisor" "scrape_interval" "30s" "scrape_timeout" "10s" "scheme" "https" "metrics_path" "/metrics/cadvisor" "authorization" (dict "credentials_file" "/var/run/secrets/kubernetes.io/serviceaccount/token") "tls_config" (dict "ca_file" $root.Values.kubelet_ca_file) "static_configs" (list (dict "targets" (list (trimPrefix "https://" $root.Values.kubelet_endpoint)))) "metric_relabel_configs" (list
      (dict "source_labels" (list "__name__") "regex" (join "|" $d.cadvisor.metric_names) "action" "keep")
      (dict "source_labels" (list "namespace") "regex" "^$" "action" "drop")
      (dict "source_labels" (list "__name__" "container") "regex" "container_(cpu|memory|oom|fs)_.*;(|POD)" "action" "drop")
      (dict "regex" "__name__|namespace|pod|container|instance|job|interface|device" "action" "labelkeep")) -}}
    {{- $_ := set $spec.config.receivers "prometheus/cadvisor" (dict "config" (dict "scrape_configs" (list $job))) -}}
    {{- $_ := set $spec.config.service.pipelines.metrics "receivers" (append $spec.config.service.pipelines.metrics.receivers "prometheus/cadvisor") -}}
  {{- end -}}
  {{- if $d.host_process.enabled -}}
    {{- $process := $spec.config.receivers.hostmetrics.scrapers.process | default dict -}}
    {{- $_ := set $process "include" (dict "match_type" "regexp" "names" $d.host_process.names) -}}
    {{- $_ := set $process "metrics" (mergeOverwrite ($process.metrics | default dict) $d.host_process.metrics) -}}
    {{- $_ := set $spec.config.receivers.hostmetrics.scrapers "process" $process -}}
    {{- $statements := list -}}
    {{- range $key := list "process.pid" "process.executable.name" -}}
      {{- $statements = append $statements (printf "set(attributes[%s], resource.attributes[%s]) where IsMatch(metric.name, \"^process[.]\") and resource.attributes[%s] != nil" (toJson (replace "." "_" $key)) (toJson $key) (toJson $key)) -}}
    {{- end -}}
    {{- $_ := set $spec.config.processors "transform/diagnostic_process" (dict "error_mode" "propagate" "metric_statements" (list (dict "context" "datapoint" "statements" $statements))) -}}
    {{- $_ := set $spec.config.service.pipelines.metrics "processors" (append $spec.config.service.pipelines.metrics.processors "transform/diagnostic_process") -}}
  {{- end -}}
{{- end -}}
{{- if eq .name "otel-scraper" -}}
  {{- $config := $spec.config.receivers.prometheus.config | default dict -}}
  {{- $jobs := $config.scrape_configs | default list -}}
  {{- if $d.apiserver.enabled -}}
    {{- $jobs = append $jobs (dict "job_name" "diagnostics-apiserver" "scrape_interval" "60s" "scrape_timeout" "10s" "scheme" "https" "authorization" (dict "credentials_file" "/var/run/secrets/kubernetes.io/serviceaccount/token") "tls_config" (dict "ca_file" "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt" "server_name" "kubernetes.default.svc") "static_configs" (list (dict "targets" (list "kubernetes.default.svc:443"))) "metric_relabel_configs" (list (dict "source_labels" (list "__name__") "regex" (join "|" $d.apiserver.metric_names) "action" "keep"))) -}}
  {{- end -}}
  {{- if $d.kube_state_metrics.enabled -}}
    {{- $jobs = append $jobs (dict "job_name" "diagnostics-kube-state-metrics" "scrape_interval" "60s" "scrape_timeout" "10s" "static_configs" (list (dict "targets" (list $d.kube_state_metrics.target))) "metric_relabel_configs" (list (dict "source_labels" (list "__name__") "regex" (join "|" $d.kube_state_metrics.metric_names) "action" "keep"))) -}}
  {{- end -}}
  {{- if or $d.apiserver.enabled $d.kube_state_metrics.enabled -}}
    {{- $_ := set $spec.config.receivers.prometheus "config" (dict "scrape_configs" $jobs) -}}
  {{- end -}}
{{- end -}}
{{- end -}}
