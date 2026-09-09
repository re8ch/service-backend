{{- define "runtime-adapter.fullname" -}}{{ default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}{{- end }}
{{- define "runtime-adapter.image" -}}{{ .Values.image.repository }}@{{ .Values.image.digest }}{{- end }}
{{- define "runtime-adapter.labels" -}}
app.kubernetes.io/name: {{ include "runtime-adapter.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

