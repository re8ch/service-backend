{{- define "public-edge-manager.name" -}}public-edge-manager{{- end }}
{{- define "public-edge-manager.image" -}}
{{ .Values.image.repository }}{{- if .Values.image.digest }}@{{ .Values.image.digest }}{{- else }}:{{ .Values.image.tag }}{{- end }}
{{- end }}
