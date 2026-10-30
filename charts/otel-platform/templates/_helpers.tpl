{{- define "otel.operatorCA" -}}
{{- if .Values.operator_metrics.enabled -}}
  {{- $ca := index .Values.credentials.trust "operator-ca.crt" | default "" -}}
  {{- if not $ca -}}
    {{- $secret := lookup "v1" "Secret" .Values.operator_metrics.namespace .Values.operator_metrics.certificate_secret -}}
    {{- if $secret -}}{{- $ca = index $secret.data "ca.crt" | default "" | b64dec -}}{{- end -}}
  {{- end -}}
  {{- if not (contains "-----BEGIN CERTIFICATE-----" $ca) -}}
    {{- fail "Operator metrics CA unavailable: install the supplied Operator first; offline helm template requires a values YAML containing credentials.trust[operator-ca.crt]=<PEM>. For an existing Operator configure operator_metrics to its TLS endpoint and CA." -}}
  {{- end -}}
  {{- $ca -}}
{{- end -}}
{{- end -}}

{{- define "otel.autoscaling" -}}
{{- if hasKey .root.Values.autoscaling .role -}}
  {{- $a := index .root.Values.autoscaling .role -}}
  {{- if $a.enabled -}}
    {{- $down := dict "selectPolicy" "Disabled" -}}
    {{- $_ := set .spec "autoscaler" (dict "minReplicas" $a.min_replicas "maxReplicas" $a.max_replicas "targetCPUUtilization" $a.target_cpu_utilization "behavior" (dict "scaleDown" $down "scaleUp" (dict "stabilizationWindowSeconds" 60 "policies" (list (dict "type" "Percent" "value" 100 "periodSeconds" 60))))) -}}
    {{- /* HPA targets the Collector CR; Helm must stop managing its replicas field. */ -}}
    {{- $_ := unset .spec "replicas" -}}
  {{- end -}}
{{- end -}}
{{- end -}}
