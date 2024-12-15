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
Same Node Pod Affinity
*/}}
{{- define "mega-media.sameNodePodAffinity" -}}
podAffinity:
  requiredDuringSchedulingIgnoredDuringExecution:
  - labelSelector:
      matchExpressions:
      - key: app.kubernetes.io/instance
        operator: In
        values:
        - {{ .Release.Name }}
    topologyKey: kubernetes.io/hostname
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
      echo $(DB_PASSWORD) && PGPASSWORD="$(DB_PASSWORD)" psql -U {{ .db_config.user }} -h {{ .db_config.host }} -p {{ .db_config.port }} -tc "SELECT 1 FROM pg_database WHERE datname = '{{ .database }}'" | grep -q 1 || PGPASSWORD="$(DB_PASSWORD)" psql -U {{ .db_config.user }} -h {{ .db_config.host }} -p {{ .db_config.port }} -c "CREATE DATABASE {{ .database }}"
  env:
    - name: DB_PASSWORD
      valueFrom:
        secretKeyRef:
          name: {{ .db_config.secret_name }}
          key: {{ .db_config.secret_key }}
{{- end }}