{{/*
Expand the name of the chart.
*/}}
{{- define "mega-media.name" -}}
{{- printf "%s-%s" (default .Release.Name .Values.nameOverride) .name | trunc 63 | trimSuffix "-" }}
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
media/service: {{ .name }}
{{- end }}

{{/*
Postgres Init Db
*/}}
{{- define "mega-media.initDb" -}}
- name: create-{{ kebabcase .database }}-if-missing
  image: docker.io/bitnami/postgresql:17
  command:
  - 'sh'
  - '-e'
  - '-c'
  - |
      PGPASSWORD=$(DB_PASSWORD) psql -U postgres -h {{ .Release.Name }}-postgresql -p 5432 -tc "SELECT 1 FROM pg_database WHERE datname = '{{ .database }}'" | grep -q 1 || PGPASSWORD=$(DB_PASSWORD) psql -U postgres -h {{ .Release.Name }}-postgresql -p 5432 -c "CREATE DATABASE {{ .database }}"
  env:
    - name: DB_PASSWORD
      valueFrom:
        secretKeyRef:
          name: {{ .Release.Name }}-postgresql
          key: postgres-password
{{- end }}