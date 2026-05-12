{{/*
Shared template helpers for the qx-service chart.
*/}}

{{- define "qx.name" -}}
{{- default .Chart.Name .Values.service.name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "qx.fullname" -}}
{{- include "qx.name" . -}}
{{- end -}}

{{- define "qx.labels" -}}
app.kubernetes.io/name: {{ include "qx.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end -}}

{{- define "qx.image" -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag -}}
{{ .Values.image.repository }}:{{ $tag }}
{{- end -}}
