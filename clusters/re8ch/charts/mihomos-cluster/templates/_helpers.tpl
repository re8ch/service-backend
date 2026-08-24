{{- define "mihomos-cluster.name" -}}mihomos-cluster{{- end }}
{{- define "mihomos-cluster.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}{{- if .Values.image.digest }}@{{ .Values.image.digest }}{{- end }}
{{- end }}
