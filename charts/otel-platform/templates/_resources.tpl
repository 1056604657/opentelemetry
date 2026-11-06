{{- define "otel.logBytes" -}}
{{- $value := toString . -}}
{{- $factor := ternary 1048576 1024 (hasSuffix "MiB" $value) -}}
{{- mul (regexFind "^[0-9]+" $value | int64) $factor -}}
{{- end -}}

{{- define "otel.memoryBytes" -}}
{{- $value := toString . -}}
{{- if not (regexMatch "^[0-9]+([.][0-9]+)?(Ki|Mi|Gi|Ti|k|M|G|T)?$" $value) -}}
  {{- fail "collector_resources memory must be bytes or use Ki/Mi/Gi/Ti/k/M/G/T" -}}
{{- end -}}
{{- $unit := regexFind "[a-zA-Z]+$" $value -}}
{{- $factor := index (dict "" 1 "Ki" 1024 "Mi" 1048576 "Gi" 1073741824 "Ti" 1099511627776 "k" 1000 "M" 1000000 "G" 1000000000 "T" 1000000000000) $unit -}}
{{- printf "%.0f" (ceil (mulf (trimSuffix $unit $value | float64) $factor)) -}}
{{- end -}}

{{- define "otel.cpuMillis" -}}
{{- $value := toString . -}}
{{- if not (regexMatch "^([0-9]+([.][0-9]+)?|[0-9]+m)$" $value) -}}
  {{- fail "collector_resources cpu must be cores or integer millicores, e.g. 0.5 or 500m" -}}
{{- end -}}
{{- $factor := ternary 1 1000 (hasSuffix "m" $value) -}}
{{- mulf (trimSuffix "m" $value | float64) $factor -}}
{{- end -}}

{{- define "otel.resources" -}}
{{- $spec := .spec -}}
{{- $override := index .root.Values.collector_resources .role | default dict -}}
{{- $_ := set $spec "resources" (mergeOverwrite $spec.resources (deepCopy $override)) -}}
{{- $limit := include "otel.memoryBytes" $spec.resources.limits.memory | int64 -}}
{{- $request := include "otel.memoryBytes" $spec.resources.requests.memory | int64 -}}
{{- $cpuLimit := include "otel.cpuMillis" $spec.resources.limits.cpu | float64 -}}
{{- $cpuRequest := include "otel.cpuMillis" $spec.resources.requests.cpu | float64 -}}
{{- if or (lt $limit 268435456) (le $request 0) (gt $request $limit) (le $cpuRequest 0.0) (gt $cpuRequest $cpuLimit) -}}
  {{- fail (printf "collector_resources.%s requires positive requests <= limits and memory limit >= 256Mi" .role) -}}
{{- end -}}
{{- /* Derive process budgets from the final Pod limit, including native resource overrides. */ -}}
{{- $memory := div $limit 1048576 -}}
{{- $hard := div (mul $memory 80) 100 -}}
{{- $spike := div (mul $hard 20) 100 -}}
{{- $_ := set $spec.config.processors.memory_limiter "limit_mib" $hard -}}
{{- $_ := set $spec.config.processors.memory_limiter "spike_limit_mib" $spike -}}
{{- range $env := $spec.env -}}
  {{- if eq $env.name "GOMEMLIMIT" -}}{{- $_ := set $env "value" (printf "%dMiB" (sub $hard $spike)) -}}{{- end -}}
{{- end -}}
{{- end -}}
