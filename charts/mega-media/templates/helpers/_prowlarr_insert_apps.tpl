{{- define "mega-media.insert.apps" -}}
- name: drop-{{ kebabcase .database }}-tables
  image: docker.io/bitnami/postgresql:17
  command:
  - 'sh'
  - '-e'
  - '-c'
  - |
      PGPASSWORD="$(DB_PASSWORD)" psql -U {{ .db_config.user }} -h {{ .db_config.host }} -p {{ .db_config.port }} -d {{ .database }} -tc 'TRUNCATE TABLE "Applications";'
      PGPASSWORD="$(DB_PASSWORD)" psql -U {{ .db_config.user }} -h {{ .db_config.host }} -p {{ .db_config.port }} -d {{ .database }} -tc 'TRUNCATE TABLE "Indexers";'
      PGPASSWORD="$(DB_PASSWORD)" psql -U {{ .db_config.user }} -h {{ .db_config.host }} -p {{ .db_config.port }} -d {{ .database }} -tc 'TRUNCATE TABLE "DownloadClients";'
  env:
    - name: DB_PASSWORD
      valueFrom:
        secretKeyRef:
          name: {{ .db_config.secret_name }}
          key: {{ .db_config.secret_key }}
{{- $database := .database -}}
{{- $db_config := .db_config -}}
{{- $apiKey := .apiKey -}}
{{- $sab_url := print (include "mega-media.name" (merge (dict "name" "sabnzbd") $)) "." $.Release.Namespace ".svc.cluster.local"  -}}
{{- $prowlarrUrl := print "http://" (include "mega-media.name" (dict "name" "prowlarr" | merge .)) "." .Release.Namespace ".svc.cluster.local:" .port  -}}
{{- range tuple "Sonarr" "Radarr" "Lidarr" "Readarr" "Prowlarr" }}
{{- $tableSelect := get $.Values.arrs (lower .) -}}
{{- $arr_database := print $tableSelect.name "_main" -}}
{{- $name := include "mega-media.name" (merge (dict "name" $tableSelect.name) $) -}}
{{- $url := print "http://" $name "." $.Release.Namespace ".svc.cluster.local:" $tableSelect.port  -}}
{{- $configContract := print . "Settings" }}
{{ if ne . "Prowlarr" }}
{{- $settings := dict "prowlarrUrl" $prowlarrUrl "baseUrl" $url "apiKey" "$API_KEY" | merge $tableSelect.search | mustToJson | quote -}}
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
{{ if ne . "Readarr" }}
- name: insert-{{ kebabcase $tableSelect.name }}-root-folder
  image: docker.io/bitnami/postgresql:17
  command:
  - 'sh'
  - '-e'
  - '-c'
  - |
      PGPASSWORD="$(DB_PASSWORD)" psql -U {{ $db_config.user }} -h {{ $db_config.host }} -p {{ $db_config.port }} -d {{ $arr_database }} -tc 'TRUNCATE TABLE "RootFolders";'
      PGPASSWORD="$(DB_PASSWORD)" psql -U {{ $db_config.user }} -h {{ $db_config.host }} -p {{ $db_config.port }} -d {{ $arr_database }} -tc "INSERT INTO \"RootFolders\" (\"Path\") VALUES ('/media/{{ $tableSelect.mediaDir }}');"
  env:
    - name: DB_PASSWORD
      valueFrom:
        secretKeyRef:
          name: {{ $db_config.secret_name }}
          key: {{ $db_config.secret_key }}
{{ else }}
- name: insert-{{ kebabcase $tableSelect.name }}-root-folder
  image: docker.io/bitnami/postgresql:17
  command:
  - 'sh'
  - '-e'
  - '-c'
  - |
      PGPASSWORD="$(DB_PASSWORD)" psql -U {{ $db_config.user }} -h {{ $db_config.host }} -p {{ $db_config.port }} -d {{ $arr_database }} -tc 'TRUNCATE TABLE "RootFolders";'
      PGPASSWORD="$(DB_PASSWORD)" psql -U {{ $db_config.user }} -h {{ $db_config.host }} -p {{ $db_config.port }} -d {{ $arr_database }} -tc "INSERT INTO \"RootFolders\" (\"Path\", \"IsCalibreLibrary\") VALUES ('/media/{{ $tableSelect.mediaDir }}', false);"
  env:
    - name: DB_PASSWORD
      valueFrom:
        secretKeyRef:
          name: {{ $db_config.secret_name }}
          key: {{ $db_config.secret_key }}
{{ end }}
{{ end }}
- name: sabnzbd-for-{{ kebabcase $tableSelect.name }}
  image: docker.io/bitnami/postgresql:17
  command:
  - 'sh'
  - '-e'
  - '-c'
  - |
      export CONFIG=$(echo "{\"host\": \"{{ $sab_url }}\", \"port\": {{ $.Values.sabnzbd.port }}, \"useSsl\": false, \"apiKey\": \"$API_KEY\", \"category\": \"prowlarr\", \"priority\": -100 }")
      echo $CONFIG
      PGPASSWORD="$(DB_PASSWORD)" psql -U {{ $db_config.user }} -h {{ $db_config.host }} -p {{ $db_config.port }} -d {{ $arr_database }} -tc 'TRUNCATE TABLE "DownloadClients";'
      PGPASSWORD="$(DB_PASSWORD)" psql -U {{ $db_config.user }} -h {{ $db_config.host }} -p {{ $db_config.port }} -d {{ $arr_database }} -tc "INSERT INTO \"DownloadClients\" (\"Enable\", \"Name\", \"Implementation\", \"Settings\", \"ConfigContract\", \"Priority\") VALUES (true, 'SABnzbd', 'Sabnzbd', '$CONFIG', 'SabnzbdSettings', 1);"
  env:
    - name: DB_PASSWORD
      valueFrom:
        secretKeyRef:
          name: {{ $db_config.secret_name }}
          key: {{ $db_config.secret_key }}
    - name: API_KEY
      valueFrom:
        secretKeyRef:
          name: {{ include "mega-media.name" (merge (dict "name" "sabnzbd") $) }}-api-key
          key: key
{{- end }}
{{- range $.Values.arrs.prowlarr.indexers }}
{{- $configContract := print .type "Settings" }}
- name: insert-{{ kebabcase .name }}-indexer
  image: docker.io/bitnami/postgresql:17
  command:
  - 'sh'
  - '-e'
  - '-c'
  - |
      export CONFIG=$(echo "{{ .settings | mustToJson | quote }}")
      echo $CONFIG
      PGPASSWORD="$(DB_PASSWORD)" psql -U {{ $db_config.user }} -h {{ $db_config.host }} -p {{ $db_config.port }} -d {{ $database }} -tc "INSERT INTO \"Indexers\" (\"Name\", \"Implementation\", \"Settings\", \"ConfigContract\", \"Enable\", \"Priority\", \"Added\", \"Tags\") VALUES ({{ .name | squote }}, {{ .type | squote }}, '$CONFIG', {{ $configContract | squote }}, {{ .enabled }}, {{ .priority }}, NOW(), '[]');"
  env:
    - name: DB_PASSWORD
      valueFrom:
        secretKeyRef:
          name: {{ $db_config.secret_name }}
          key: {{ $db_config.secret_key }}
    - name: API_KEY
      valueFrom:
        secretKeyRef:
          {{ toYaml .apiKeyFromSecretKeyRef | nindent 10 }}
{{- end }}
{{- end }}