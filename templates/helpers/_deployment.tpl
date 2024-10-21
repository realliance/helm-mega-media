{{- define "mega-media.deployment" -}}
{{- $nameInTable := merge (dict "name" .selected.name) . -}}
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
            {{- toYaml .selected.livenessProbe | nindent 12 }}
          readinessProbe:
            {{- toYaml .selected.readinessProbe | nindent 12 }}
          resources:
            {{- toYaml .selected.resources | nindent 12 }}
          {{- with .Values.volumeMounts }}
          volumeMounts:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{ if eq .arr "true" }}
          envFrom:
            - configMapRef:
              name: {{ include "mega-media.name" (merge (dict "name" "arr-config") .) }}
          {{ end }}
      {{- with .Values.volumes }}
      volumes:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
{{- end }}
