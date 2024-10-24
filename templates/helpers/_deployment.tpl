{{- define "mega-media.deployment" -}}
{{- $nameInTable := merge (dict "name" .selected.name) . -}}
{{- $isArr := eq .arr true -}}
{{- $mountMedia := or $isArr (eq (default false .selected.mountMedia) true) -}}
{{- $apiKey := lower (randAlphaNum 32) -}}
---
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "mega-media.name" $nameInTable }}-api-key
  labels:
    {{- include "mega-media.labels" $nameInTable | nindent 4 }}
data:
  key: {{ b64enc $apiKey }}
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
        {{- include "mega-media.initDb" (merge (dict "database" (printf "%s_main" .selected.name)) .) | nindent 8 }}
        {{- include "mega-media.initDb" (merge (dict "database" (printf "%s_log" .selected.name)) .) | nindent 8 }}
        {{- include "mega-media.initDb" (merge (dict "database" (printf "%s_cache" .selected.name)) .) | nindent 8 }}
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
                <InstanceName>{{ .Release.Name }}</InstanceName>
                <PostgresUser>postgres</PostgresUser>
                <PostgresPassword>$(DB_PASSWORD)</PostgresPassword>
                <PostgresPort>5432</PostgresPort>
                <PostgresHost>{{ .Release.Name }}-postgresql</PostgresHost>
                <PostgresMainDb>{{ .selected.name }}_main</PostgresMainDb>
                <PostgresLogDb>{{ .selected.name }}_log</PostgresLogDb>
                <PostgresCacheDb>{{ .selected.name }}_cache</PostgresCacheDb>
              </Config>' > /config/config.xml && chmod 664 /config/config.xml
          env:
            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: {{ .Release.Name }}-postgresql
                  key: postgres-password
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
          {{- if $mountMedia }}
          volumeMounts:
          - mountPath: /media
            name: media
          {{- if $isArr }}
          - mountPath: /config/config.xml
            name: config
            subPath: config.xml
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
{{- end }}
