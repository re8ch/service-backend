{{- define "re8ch-storage-fabric.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "re8ch-storage-fabric.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "re8ch-storage-fabric.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
