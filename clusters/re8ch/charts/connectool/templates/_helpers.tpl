{{- define "connectool.labels" -}}
app.kubernetes.io/name: connectool
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name (.Chart.Version | replace "+" "_") | trunc 63 | trimSuffix "-" | quote }}
ai.re8ch.com/identity-provider: dex
ai.re8ch.com/distribution-plane: toolhive
{{- end }}

{{- define "connectool.publication" -}}
{{- $name := . -}}
{{- $matches := list -}}
{{- range $.root.Values.toolhive.publications }}{{ if eq .name $name }}{{ $matches = append $matches . }}{{ end }}{{ end -}}
{{- if ne (len $matches) 1 }}{{ fail (printf "Codex plugin references unknown or duplicate publication %q" $name) }}{{ end -}}
{{- index $matches 0 | toJson -}}
{{- end -}}
