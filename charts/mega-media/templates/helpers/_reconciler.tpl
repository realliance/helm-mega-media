{{/*
Pod spec for the reconciler. Shared between three call sites:
  - the long-running Deployment (reconciler.mode=deployment) — a reconcile loop
    that serves /healthz, /readyz and /metrics
  - the legacy CronJob (reconciler.mode=cronjob) — one oneshot pass per tick
  - the post-install/post-upgrade bootstrap Job — one oneshot pass on deploy

Pass `oneshot: true` in the merged context for the Job/CronJob call sites. In
oneshot mode the process runs a single pass and exits (RECONCILE_ONESHOT=1), so
it gets restartPolicy OnFailure and no health server / probes / metrics port.
The long-running call site omits `oneshot` and gets restartPolicy Always,
RECONCILE_INTERVAL, a metrics port and liveness/readiness probes.

Renders env vars for every API key the reconciler needs:
  - ARR_<NAME>_API_KEY for each enabled *arr (sonarr, radarr, ...)
  - SAB_API_KEY if sabnzbd is enabled
  - INDEXER_<i>_API_KEY for each indexer in arrs.prowlarr.indexers
*/}}
{{- define "mega-media.reconciler.podspec" -}}
{{- $configMapName := include "mega-media.name" (merge (dict "name" "reconciler") .) -}}
{{- $oneshot := .oneshot | default false -}}
{{- $metricsPort := .Values.reconciler.metricsPort | default 8000 -}}
{{- if $oneshot }}
restartPolicy: OnFailure
{{- else }}
restartPolicy: Always
{{- end }}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
containers:
  - name: reconciler
    image: "{{ .Values.reconciler.image }}:{{ .Values.reconciler.tag }}"
    imagePullPolicy: {{ .Values.reconciler.pullPolicy }}
    resources:
      {{- toYaml .Values.reconciler.resources | nindent 6 }}
    env:
      {{- if $oneshot }}
      - name: RECONCILE_ONESHOT
        value: "1"
      {{- else }}
      - name: RECONCILE_INTERVAL
        value: {{ .Values.reconciler.interval | default 300 | quote }}
      - name: METRICS_PORT
        value: {{ $metricsPort | quote }}
      {{- end }}
      {{- range $name := tuple "sonarr" "radarr" "lidarr" "readarr" "prowlarr" }}
      {{- $svc := get $.Values.arrs $name }}
      {{- if $svc.enabled }}
      {{- $arrName := include "mega-media.name" (merge (dict "name" $svc.name) $) }}
      {{- $secretName := hasKey $svc "apiKey" | ternary (default "" (get (default dict $svc.apiKey) "name")) (printf "%s-api-key" $arrName) }}
      {{- $secretKey := hasKey $svc "apiKey" | ternary (default "" (get (default dict $svc.apiKey) "key")) "key" }}
      - name: ARR_{{ upper $name }}_API_KEY
        valueFrom:
          secretKeyRef:
            name: {{ $secretName }}
            key: {{ $secretKey }}
      {{- end }}
      {{- end }}
      {{- if $.Values.sabnzbd.enabled }}
      {{- $sabName := include "mega-media.name" (merge (dict "name" $.Values.sabnzbd.name) $) }}
      {{- $sabSecret := hasKey $.Values.sabnzbd "apiKey" | ternary (default "" (get (default dict $.Values.sabnzbd.apiKey) "name")) (printf "%s-api-key" $sabName) }}
      {{- $sabKey := hasKey $.Values.sabnzbd "apiKey" | ternary (default "" (get (default dict $.Values.sabnzbd.apiKey) "key")) "key" }}
      - name: SAB_API_KEY
        valueFrom:
          secretKeyRef:
            name: {{ $sabSecret }}
            key: {{ $sabKey }}
      {{- end }}
      {{- range $i, $indexer := $.Values.arrs.prowlarr.indexers }}
      - name: INDEXER_{{ $i }}_API_KEY
        valueFrom:
          secretKeyRef:
            {{- toYaml $indexer.apiKeyFromSecretKeyRef | nindent 14 }}
      {{- end }}
    {{- if not $oneshot }}
    ports:
      - name: metrics
        containerPort: {{ $metricsPort }}
        protocol: TCP
    livenessProbe:
      httpGet:
        path: /healthz
        port: metrics
      initialDelaySeconds: 5
      periodSeconds: 15
    readinessProbe:
      httpGet:
        path: /readyz
        port: metrics
      initialDelaySeconds: 5
      periodSeconds: 10
    {{- end }}
    volumeMounts:
      - mountPath: /etc/reconciler
        name: config
volumes:
  - name: config
    configMap:
      name: {{ $configMapName }}
{{- end }}
