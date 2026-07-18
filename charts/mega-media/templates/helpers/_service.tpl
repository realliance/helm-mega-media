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
      {{- if and .metricsSidecar .Values.metrics.enabled }}
      # A multi-port Service requires every port to be named — so name the
      # primary port once the metrics port below is added.
      name: http
      {{- end }}
      port: {{ .port }}
      targetPort: {{ .targetPort }}
    {{- if and .metricsSidecar .Values.metrics.enabled }}
    - name: metrics
      protocol: TCP
      port: {{ .Values.metrics.port }}
      targetPort: metrics
    {{- end }}
{{- end }}