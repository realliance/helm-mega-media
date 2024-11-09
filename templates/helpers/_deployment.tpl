{{- define "mega-media.deployment" -}}
{{- $nameInTable := merge (dict "name" .selected.name) . -}}
{{- $isArr := eq .arr true -}}
{{- $mountMedia := or $isArr (eq (default false .selected.mountMedia) true) -}}
{{- $configMount := eq .selected.configMount.enabled true -}}
{{- $genApiKey := eq .selected.overrideGenApiKey.enabled true | default false -}}
{{- $apiKey := lower (randAlphaNum 32) -}}

{{- $db_host := .Values.postgresql.enabled | ternary (printf "%s-postgresql" .Release.Name) .Values.externalPostgres.host -}}
{{- $db_port := .Values.postgresql.enabled | ternary "5432" .Values.externalPostgres.port -}}
{{- $db_user := .Values.postgresql.enabled | ternary "postgres" .Values.externalPostgres.username -}}
{{- $db_secret_name := .Values.postgresql.enabled | ternary (printf "%s-postgresql" .Release.Name) .Values.externalPostgres.passwordFromSecretKeyRef.name -}}
{{- $db_secret_key := .Values.postgresql.enabled | ternary "postgres-password" .Values.externalPostgres.passwordFromSecretKeyRef.key -}}
{{- $db_dict := dict "host" $db_host "port" $db_port "user" $db_user "secret_name" $db_secret_name "secret_key" $db_secret_key -}}
{{ if or $isArr $genApiKey }}
---
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "mega-media.name" $nameInTable }}-api-key
  labels:
    {{- include "mega-media.labels" $nameInTable | nindent 4 }}
data:
  key: {{ b64enc $apiKey }}
{{ end }}
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
      securityContext:
        {{- toYaml .Values.podSecurityContext | nindent 8 }}
      affinity:
        podAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
          - labelSelector:
              matchExpressions:
              - key: app.kubernetes.io/instance
                operator: In
                values:
                - {{ .Release.Name }}
            topologyKey: kubernetes.io/hostname
      {{ if $isArr }}
      # https://wiki.servarr.com/sonarr/postgres-setup
      initContainers:
        - name: wait-for-db
          image: docker.io/bitnami/postgresql:17
          command:
            - 'sh'
            - '-e'
            - '-c'
            - 'until pg_isready -U "postgres" -h {{ .Release.Name }}-postgresql -p 5432; do sleep 1; done'
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
                <ApiKey>{{ $apiKey }}</ApiKey>
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
          volumeMounts:
            - mountPath: /config
              name: config
      {{ end }}
      containers:
        - name: {{ .selected.name }}
          securityContext:
            {{- toYaml .Values.securityContext | nindent 12 }}
          image: "{{ .selected.image }}:{{ .selected.tag }}"
          imagePullPolicy: {{ .selected.pullPolicy }}
          ports:
            - name: http
              containerPort: {{ .selected.port }}
              protocol: TCP
          livenessProbe:
            {{- toYaml (default .Values.arrProbes.livenessProbe .selected.livenessProbe) | nindent 12 }}
          readinessProbe:
            {{- toYaml (default .Values.arrProbes.readinessProbe .selected.readinessProbe) | nindent 12 }}
          resources:
            {{- toYaml .selected.resources | nindent 12 }}
          {{- if eq .arr true }}
          envFrom:
            - configMapRef:
                name: {{ include "mega-media.name" (merge (dict "name" "arr-config") .) }}
          {{ end }}
          {{- if (.selected.env | empty | not) | or $genApiKey }}
          env:
            - name: SHIM
              value: ""
            {{- toYaml .selected.env | nindent 12 }}
            {{ if $genApiKey }}
            - name: {{ .selected.overrideGenApiKey.env }}
              valueFrom:
                secretKeyRef:
                  name: {{ include "mega-media.name" $nameInTable }}-api-key
                  key: key
            {{- end }}
          {{- end }}
          {{- if $mountMedia }}
          volumeMounts:
          - mountPath: /media
            name: media
          {{- if $isArr }}
          - mountPath: /config/config.xml
            name: config
            subPath: config.xml
          {{- end }}
          {{- if $configMount }}
          - name: config
            mountPath: {{ .selected.configMount.path }}
          {{- end }}
          {{- end }}
      volumes:
      {{- if $isArr }}
      - name: config
        emptyDir:
          medium: Memory
      {{ end }}
      {{- if $mountMedia }}
      - name: media
        persistentVolumeClaim:
          claimName: {{ include "mega-media.name" (merge (dict "name" "media") .) }}
      {{ end }}
      {{- if $configMount }}
      - name: config
        persistentVolumeClaim:
          claimName: {{ include "mega-media.name" (merge (dict "name" .selected.name) .) }}
      {{- end }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ include "mega-media.name" $nameInTable }}
  labels:
    {{- include "mega-media.labels" $nameInTable | nindent 4 }}
spec:
  selector:
    {{- include "mega-media.selectorLabels" $nameInTable | nindent 6 }}
  ports:
    - protocol: TCP
      port: {{ .selected.port }}
      targetPort: http
{{ if $configMount -}}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "mega-media.name" (merge (dict "name" .selected.name) .) }}
  labels:
    {{- include "mega-media.labels" (merge (dict "name" .selected.name) .) | nindent 4 }}
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: {{ .selected.configMount.storageClassName }}
  resources:
    requests:
      storage: {{ .selected.configMount.size }}
{{- end -}}
{{- end }}
