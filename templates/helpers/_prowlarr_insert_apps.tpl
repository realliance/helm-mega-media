{{- define "mega-media.insert.apps" -}}
- name: drop-{{ kebabcase .database }}-applications
  image: docker.io/bitnami/postgresql:17
  command:
  - 'sh'
  - '-e'
  - '-c'
  - |
      PGPASSWORD="$(DB_PASSWORD)" psql -U {{ .db_config.user }} -h {{ .db_config.host }} -p {{ .db_config.port }} -d {{ .database }} -tc 'TRUNCATE TABLE "Applications";'
  env:
    - name: DB_PASSWORD
      valueFrom:
        secretKeyRef:
          name: {{ .db_config.secret_name }}
          key: {{ .db_config.secret_key }}
{{- $database := .database -}}
{{- $db_config := .db_config -}}
{{- $apiKey := .apiKey -}}
{{- $prowlarrUrl := print "http://" (include "mega-media.name" (dict "name" "prowlarr" | merge .)) "." .Release.Namespace ".svc.cluster.local:" .port  -}}
{{- range tuple "Sonarr" "Radarr" "Lidarr" "Readarr" }}
{{- $tableSelect := get $.Values.arrs (lower .) -}}
{{- $name := include "mega-media.name" (merge (dict "name" $tableSelect.name) $) -}}
{{- $url := print "http://" $name "." $.Release.Namespace ".svc.cluster.local:" $tableSelect.port  -}}
{{- $settings := dict "prowlarrUrl" $prowlarrUrl "baseUrl" $url "apiKey" "$API_KEY" | merge $tableSelect.search | mustToJson | quote -}}
{{- $configContract := print . "Settings" }}
- name: insert-{{ kebabcase $tableSelect.name }}-app-sync
  image: docker.io/bitnami/postgresql:17
  command:
  - 'sh'
  - '-e'
  - '-c'
  - |
      export CONFIG=$(echo "{{ $settings | squote }}")
      echo $CONFIG
      PGPASSWORD="$(DB_PASSWORD)" psql -U {{ $db_config.user }} -h {{ $db_config.host }} -p {{ $db_config.port }} -d {{ $database }} -tc "INSERT INTO \"Applications\" (\"Name\", \"Implementation\", \"Settings\", \"ConfigContract\", \"SyncLevel\", \"Tags\") VALUES ({{ . | squote }}, {{ . | squote }}, $CONFIG, {{ $configContract | squote }}, 2, '[]');"
  env:
    - name: DB_PASSWORD
      valueFrom:
        secretKeyRef:
          name: {{ $db_config.secret_name }}
          key: {{ $db_config.secret_key }}
    - name: API_KEY
      valueFrom:
        secretKeyRef:
          name: {{ include "mega-media.name" (merge (dict "name" $tableSelect.name) $) }}-api-key
          key: key
{{- end }}
{{- end }}