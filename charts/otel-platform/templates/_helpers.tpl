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
