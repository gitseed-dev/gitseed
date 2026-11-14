{{/*
Expand the name of the chart.
*/}}
{{- define "gitseed.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "gitseed.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "gitseed.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "gitseed.labels" -}}
helm.sh/chart: {{ include "gitseed.chart" . }}
{{ include "gitseed.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "gitseed.selectorLabels" -}}
app.kubernetes.io/name: {{ include "gitseed.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Control plane image
*/}}
{{- define "gitseed.image" -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag }}
{{- printf "%s:%s" .Values.image.repository $tag }}
{{- end }}

{{/*
Runner image
*/}}
{{- define "gitseed.runnerImage" -}}
{{- $tag := default .Chart.AppVersion .Values.runnerImage.tag }}
{{- printf "%s:%s" .Values.runnerImage.repository $tag }}
{{- end }}

{{/*
ServiceAccount name
*/}}
{{- define "gitseed.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "gitseed.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Secret name
*/}}
{{- define "gitseed.secretName" -}}
{{- if .Values.auth.existingSecret }}
{{- .Values.auth.existingSecret }}
{{- else }}
{{- printf "%s-secret" (include "gitseed.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Resolved runner namespace (for SDLC Stage Jobs)
*/}}
{{- define "gitseed.runnerNamespace" -}}
{{- if .Values.compute.runnerNamespace }}
{{- .Values.compute.runnerNamespace }}
{{- else if eq .Values.compute.namespaceIsolation "shared" }}
{{- .Release.Namespace }}
{{- else }}
{{- printf "%s-runners" .Release.Namespace | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Resolved sandbox namespace (for Interactive Terminals, Sandbox Jobs & Live Agent Sessions)
*/}}
{{- define "gitseed.sandboxNamespace" -}}
{{- if .Values.compute.sandboxNamespace }}
{{- .Values.compute.sandboxNamespace }}
{{- else if eq .Values.compute.namespaceIsolation "shared" }}
{{- .Release.Namespace }}
{{- else }}
{{- printf "%s-sandboxes" .Release.Namespace | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
PostgreSQL host
*/}}
{{- define "gitseed.postgresHost" -}}
{{- if .Values.postgresql.enabled }}
{{- printf "%s-postgresql" (include "gitseed.fullname" .) }}
{{- else }}
{{- .Values.externalDatabase.host }}
{{- end }}
{{- end }}

{{/*
Resolved cookie-less Sandbox Origin for Embedded VS Code webviews.
Automatically derives http(s)://<ingress.sandboxHost> when Ingress is enabled with sandboxHost
and web.sandboxOrigin was left at its localhost port-forward default.
*/}}
{{- define "gitseed.sandboxOrigin" -}}
{{- $explicit := .Values.web.sandboxOrigin | default "" -}}
{{- if and .Values.ingress.enabled .Values.ingress.sandboxHost (or (eq $explicit "") (eq $explicit "http://127.0.0.1:8423")) -}}
{{- $scheme := ternary "https" "http" (gt (len (.Values.ingress.tls | default list)) 0) -}}
{{- printf "%s://%s" $scheme .Values.ingress.sandboxHost -}}
{{- else -}}
{{- $explicit -}}
{{- end -}}
{{- end }}

{{/*
Redis / Valkey URL
*/}}
{{- define "gitseed.redisUrl" -}}
{{- if .Values.valkey.enabled }}
{{- $auth := .Values.valkey.auth | default dict }}
{{- $pass := default (index $auth "password") (index $auth "_resolvedPassword") }}
{{- if $pass }}
{{- printf "redis://:%s@%s-valkey:%v/0" (urlquery $pass) (include "gitseed.fullname" .) .Values.valkey.port }}
{{- else }}
{{- printf "redis://:${VALKEY_PASSWORD}@%s-valkey:%v/0" (include "gitseed.fullname" .) .Values.valkey.port }}
{{- end }}
{{- else }}
{{- .Values.externalRedis.url }}
{{- end }}
{{- end }}
