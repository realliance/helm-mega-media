{{- define "mega-media.service" -}}
{{- $nameInContext := merge (dict "name" .name) . -}}

---
apiVersion: v1
kind: Service
metadata:
  name: {{ include "mega-media.name" $nameInContext }}
  labels:
    {{- include "mega-media.labels" $nameInContext | nindent 4 }}
spec:
  selector:
    {{- include "mega-media.selectorLabels" $nameInContext | nindent 6 }}
  ports:
    - protocol: TCP
      port: {{ .port }}
      targetPort: {{ .targetPort }}
{{- end }}