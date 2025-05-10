{{- define "mega-media.arr.deployment" -}}
{{- $nameInTable := merge (dict "name" .selected.name) . -}}

{{- $db_host := .Values.postgresql.enabled | ternary (printf "%s-postgresql" .Release.Name) .Values.externalPostgres.host -}}
{{- $db_port := .Values.postgresql.enabled | ternary "5432" .Values.externalPostgres.port -}}
{{- $db_user := .Values.postgresql.enabled | ternary "postgres" .Values.externalPostgres.username -}}
{{- $db_secret_name := .Values.postgresql.enabled | ternary (printf "%s-postgresql" .Release.Name) .Values.externalPostgres.passwordFromSecretKeyRef.name -}}
{{- $db_secret_key := .Values.postgresql.enabled | ternary "postgres-password" .Values.externalPostgres.passwordFromSecretKeyRef.key -}}
{{- $db_dict := dict "host" $db_host "port" $db_port "user" $db_user "secret_name" $db_secret_name "secret_key" $db_secret_key -}}

{{- $api_key_secret_name := hasKey .selected "apiKey" | ternary (get (.selected.apiKey) "name") (printf "%s-api-key" (include "mega-media.name" $nameInTable)) -}}
{{- $api_key_secret_key := hasKey .selected "apiKey" | ternary (get (.selected.apiKey) "key") "key" -}}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "mega-media.name" $nameInTable }}
  labels:
    {{- include "mega-media.labels" $nameInTable | nindent 4 }}
spec:
  replicas: 1
  selector:
    matchLabels:
      {{- include "mega-media.selectorLabels" $nameInTable | nindent 6 }}
  template:
    metadata:
      {{- with .Values.podAnnotations }}
      annotations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      labels:
        {{- include "mega-media.labels" $nameInTable | nindent 8 }}
        {{- with .Values.podLabels }}
          {{- toYaml . | nindent 8 }}
        {{- end }}
    spec:
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- if .Values.nodeName }}
      nodeName: {{ .Values.nodeName | quote }}
      {{- end }}
      securityContext:
        {{- toYaml .Values.podSecurityContext | nindent 8 }}
      affinity:
        {{- include "mega-media.sameNodePodAffinity" . | nindent 8 }}
      # https://wiki.servarr.com/sonarr/postgres-setup
      initContainers:
        - name: wait-for-db
          image: docker.io/bitnami/postgresql:17
          command:
            - 'sh'
            - '-e'
            - '-c'
            - 'until pg_isready -U "postgres" -h {{ $db_host }} -p 5432; do sleep 1; done'
        {{- include "mega-media.initDb" (merge (dict "database" (printf "%s_main" .selected.name) "db_config" $db_dict) .) | nindent 8 }}
        {{- include "mega-media.initDb" (merge (dict "database" (printf "%s_log" .selected.name) "db_config" $db_dict) .) | nindent 8 }}
        {{- include "mega-media.initDb" (merge (dict "database" (printf "%s_cache" .selected.name) "db_config" $db_dict) .) | nindent 8 }}
        - name: init-myservice
          image: docker.io/busybox:1
          command: 
            - 'sh'
            - '-c'
            - |
              echo '
              <Config>
                <BindAddress>*</BindAddress>
                <Port>{{ .selected.port }}</Port>
                <Branch>develop</Branch>
                <LogLevel>debug</LogLevel>
                <UrlBase></UrlBase>
                <ApiKey>$(API_KEY)</ApiKey>
                <AuthenticationMethod>External</AuthenticationMethod>
                <InstanceName>{{ .Release.Name }}</InstanceName>
                <PostgresUser>{{ $db_user }}</PostgresUser>
                <PostgresPassword>$(DB_PASSWORD)</PostgresPassword>
                <PostgresPort>{{ $db_port }}</PostgresPort>
                <PostgresHost>{{ $db_host }}</PostgresHost>
                <PostgresMainDb>{{ .selected.name }}_main</PostgresMainDb>
                <PostgresLogDb>{{ .selected.name }}_log</PostgresLogDb>
                <PostgresCacheDb>{{ .selected.name }}_cache</PostgresCacheDb>
              </Config>' > /config/config.xml && chmod 664 /config/config.xml
          env:
            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: {{ $db_secret_name }}
                  key: {{ $db_secret_key }}
            - name: API_KEY
              valueFrom:
                secretKeyRef:
                  name: {{ $api_key_secret_name }}
                  key: {{ $api_key_secret_key }}
          volumeMounts:
            - mountPath: /config
              name: config
        - name: init-media-subpath
          image: docker.io/busybox:1
          command: 
            - 'sh'
            - '-c'
            - |
              mkdir -p /media/{{ .selected.mediaDir }}
              chown 1000:1000 /media/{{ .selected.mediaDir }}
          volumeMounts:
            - mountPath: /media
              name: media
      containers:
        - name: {{ .selected.name }}
          image: "{{ .selected.image }}:{{ .selected.tag }}"
          imagePullPolicy: {{ .selected.pullPolicy }}
          ports:
            - name: http
              containerPort: {{ .selected.port }}
              protocol: TCP
          livenessProbe:
            {{- .selected.livenessProbe | default .Values.arrs.probes.livenessProbe | toYaml | nindent 12 }}
          readinessProbe:
            {{- .selected.readinessProbe | default .Values.arrs.probes.readinessProbe | toYaml | nindent 12 }}
          resources:
            {{- toYaml .selected.resources | nindent 12 }}
          envFrom:
            - configMapRef:
                name: {{ include "mega-media.name" (merge (dict "name" "arr-config") .) }}
          volumeMounts:
          - mountPath: /media
            name: media
          - mountPath: /config/config.xml
            name: config
            subPath: config.xml
      volumes:
      - name: config
        emptyDir:
          medium: Memory
      - name: media
        persistentVolumeClaim:
          claimName: {{ include "mega-media.name" (merge (dict "name" "media") .) }}
{{- end }}