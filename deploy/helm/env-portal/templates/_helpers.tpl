{{- define "env-portal.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "env-portal.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* Call with (dict "root" $ "component" "api") */}}
{{- define "env-portal.selectorLabels" -}}
app.kubernetes.io/name: {{ .root.Chart.Name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "env-portal.image" -}}
{{- printf "%s/%s:%s" .root.Values.image.repository .component (.root.Values.image.tag | default .root.Chart.AppVersion) -}}
{{- end -}}

{{/* Environment shared by every backend container. */}}
{{- define "env-portal.backendEnv" -}}
envFrom:
  - configMapRef:
      name: {{ include "env-portal.fullname" . }}
env:
  - name: PORTAL_DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: {{ .Values.existingSecret }}
        key: database-url
  - name: PORTAL_CELERY_BROKER_URL
    valueFrom:
      secretKeyRef:
        name: {{ .Values.existingSecret }}
        key: broker-url
{{- end -}}

{{- define "env-portal.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "env-portal.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}
