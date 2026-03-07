{{/*
Expand the name of the chart.
*/}}
{{- define "vertex-claude-usage-exporter.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "vertex-claude-usage-exporter.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "vertex-claude-usage-exporter.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "vertex-claude-usage-exporter.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "vertex-claude-usage-exporter.selectorLabels" -}}
app.kubernetes.io/name: {{ include "vertex-claude-usage-exporter.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
GCP credentials secret name
*/}}
{{- define "vertex-claude-usage-exporter.credentialsSecretName" -}}
{{- if .Values.gcpCredentials.existingSecret }}
{{- .Values.gcpCredentials.existingSecret }}
{{- else }}
{{- include "vertex-claude-usage-exporter.fullname" . }}-gcp-credentials
{{- end }}
{{- end }}
