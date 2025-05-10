{{- define "mega-media.media-covers.pvc" -}}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "mega-media.name" (merge (dict "name" .name) .) }}-media-covers
  labels:
    {{- include "mega-media.labels" (merge (dict "name" .name) .) | nindent 4 }}
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: {{ .storageClassName }}
  resources:
    requests:
      storage: {{ .size }}
{{- end }} 