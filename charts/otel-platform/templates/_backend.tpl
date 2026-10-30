{{- define "otel.validateHTTP" -}}
{{- $url := required (printf "%s required" .key) .url -}}
{{- if or (contains ".invalid" $url) (contains "REPLACE_ME" $url) -}}{{- fail (printf "%s still contains example value" .key) -}}{{- end -}}
{{- if not (or (hasPrefix "https://" $url) (and .allow_http (hasPrefix "http://" $url))) -}}
  {{- fail (printf "%s requires HTTPS; HTTP needs explicit allow_http" .key) -}}
{{- end -}}
{{- /* Parse only the origin so parser errors cannot echo a Token carried in the path. */ -}}
{{- $origin := regexFind "^https?://[^/?#]*" $url -}}
{{- if or (contains "@" $origin) (regexMatch "[[:space:]#]" $url) -}}
  {{- fail (printf "%s requires a URL without embedded credentials, whitespace or fragment" .key) -}}
{{- end -}}
{{- $parsed := urlParse $origin -}}
{{- if or (empty $parsed.host) (not (empty $parsed.userinfo)) -}}{{- fail (printf "%s requires a host without embedded credentials" .key) -}}{{- end -}}
{{- end -}}
