{{- define "mega-media.api-key-secret" -}}
{{- $nameInTable := merge (dict "name" .name) . -}}
---
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "mega-media.name" $nameInTable }}-api-key
  labels:
    {{- include "mega-media.labels" $nameInTable | nindent 4 }}
  annotations:
    helm.sh/hook: pre-install
    helm.sh/hook-delete-policy: before-hook-creation
data:
  key: {{ b64enc .apiKey }}
{{- end }}
