{{- define "mega-media.sabnzbd.server" -}}
- name: init-{{ .name | kebabcase | replace "." "-" | trunc 63 }}
  image: docker.io/busybox:1
  command: 
    - 'sh'
    - '-c'
    - |
      echo "
      [[{{ .name }}]]
      name = {{ .name }}
      displayname = {{ .name }}
      host = {{ .host }}
      port = {{ .port }}
      timeout = {{ .timeout }}
      username = $USERNAME
      password = $PASSWORD
      connections = {{ .connections }}
      ssl = 1
      ssl_verify = 2
      ssl_ciphers = ''
      enable = 1
      required = 0
      optional = 0
      retention = 0
      expire_date = ''
      quota = ''
      usage_at_start = 0
      priority = 0
      notes = ''
      " > /config/stub-{{ .name | kebabcase }}.ini && chmod 664 /config/stub-{{ .name | kebabcase }}.ini
  env:
    {{ with .usernameFromSecretKeyRef -}}
    - name: USERNAME
      valueFrom:
        secretKeyRef:
          name: {{ .name }}
          key: {{ .key }}
    {{- end }}
    {{ with .passwordFromSecretKeyRef -}}
    - name: PASSWORD
      valueFrom:
        secretKeyRef:
          name: {{ .name }}
          key: {{ .key }}
    {{- end }}
  volumeMounts:
    - mountPath: /config
      name: config-file
{{- end }}