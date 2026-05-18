{{/*
Pod spec for the reconciler. Shared between the CronJob (ongoing
reconciliation) and the post-install/post-upgrade Job (immediate-on-deploy).

Renders env vars for every API key the reconciler needs:
  - ARR_<NAME>_API_KEY for each enabled *arr (sonarr, radarr, ...)
  - SAB_API_KEY if sabnzbd is enabled
  - INDEXER_<i>_API_KEY for each indexer in arrs.prowlarr.indexers
*/}}
{{- define "mega-media.reconciler.podspec" -}}
{{- $configMapName := include "mega-media.name" (merge (dict "name" "reconciler") .) -}}
restartPolicy: OnFailure
containers:
  - name: reconciler
    image: "{{ .Values.reconciler.image }}:{{ .Values.reconciler.tag }}"
    imagePullPolicy: {{ .Values.reconciler.pullPolicy }}
    resources:
      {{- toYaml .Values.reconciler.resources | nindent 6 }}
    env:
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
    volumeMounts:
      - mountPath: /etc/reconciler
        name: config
volumes:
  - name: config
    configMap:
      name: {{ $configMapName }}
{{- end }}
