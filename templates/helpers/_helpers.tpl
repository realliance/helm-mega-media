{{/*
Expand the name of the chart.
*/}}
{{- define "mega-media.name" -}}
{{- printf "%s-%s" (default .Chart.Name .Values.nameOverride) .medianame | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "mega-media.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "mega-media.labels" -}}
helm.sh/chart: {{ include "mega-media.chart" . }}
{{ include "mega-media.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "mega-media.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mega-media.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
media/service: {{ .medianame }}
{{- end }}
