{{- define "controlled-cluster-connection.name" -}}
controlled-cluster-connection
{{- end }}

{{- define "controlled-cluster-connection.labels" -}}
app.kubernetes.io/name: {{ include "controlled-cluster-connection.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
access.re8ch.com/relationship: controlled-subcluster
access.re8ch.com/owner: {{ .Values.cluster.owner | quote }}
{{- end }}
