{{/*
Chart name, optionally overridden by nameOverride.
*/}}
{{- define "pansoph.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully qualified app name. Truncated to 63 chars for the DNS label limit. If the
release name already contains the chart name it is not repeated.
*/}}
{{- define "pansoph.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Chart label value (name-version).
*/}}
{{- define "pansoph.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels applied to every object.
*/}}
{{- define "pansoph.labels" -}}
helm.sh/chart: {{ include "pansoph.chart" . }}
{{ include "pansoph.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: pansoph
{{- end -}}

{{/*
Selector labels (stable across upgrades, never include version).
*/}}
{{- define "pansoph.selectorLabels" -}}
app.kubernetes.io/name: {{ include "pansoph.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Per component name, e.g. release-pansoph-agora.
*/}}
{{- define "pansoph.componentName" -}}
{{- printf "%s-%s" (include "pansoph.fullname" .root) .component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
The name of the config map holding non secret environment.
*/}}
{{- define "pansoph.configMapName" -}}
{{- printf "%s-config" (include "pansoph.fullname" .) -}}
{{- end -}}

{{/*
The name of the secret to mount. Uses an existing secret when set, so production
can manage credentials out of band, otherwise the chart managed secret.
*/}}
{{- define "pansoph.secretName" -}}
{{- if .Values.secret.existingSecret -}}
{{- .Values.secret.existingSecret -}}
{{- else -}}
{{- printf "%s-secret" (include "pansoph.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
Service account name.
*/}}
{{- define "pansoph.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "pansoph.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Resolved host names. When a bundled data service is enabled its in cluster
Service name is used, otherwise the external host from values.
*/}}
{{- define "pansoph.postgresHost" -}}
{{- if .Values.postgres.enabled -}}
{{- printf "%s-postgres" (include "pansoph.fullname" .) -}}
{{- else -}}
{{- required "postgres.enabled is false, set externalPostgres.host" .Values.externalPostgres.host -}}
{{- end -}}
{{- end -}}

{{- define "pansoph.valkeyHost" -}}
{{- if .Values.valkey.enabled -}}
{{- printf "%s-valkey" (include "pansoph.fullname" .) -}}
{{- else -}}
{{- required "valkey.enabled is false, set externalValkey.host" .Values.externalValkey.host -}}
{{- end -}}
{{- end -}}

{{- define "pansoph.spicedbHost" -}}
{{- if .Values.spicedb.enabled -}}
{{- printf "%s-spicedb" (include "pansoph.fullname" .) -}}
{{- else -}}
{{- required "spicedb.enabled is false, set externalSpicedb.host" .Values.externalSpicedb.host -}}
{{- end -}}
{{- end -}}

{{- define "pansoph.objectstoreHost" -}}
{{- if .Values.objectstore.enabled -}}
{{- printf "%s-objectstore" (include "pansoph.fullname" .) -}}
{{- else -}}
{{- required "objectstore.enabled is false, set externalObjectstore.host" .Values.externalObjectstore.host -}}
{{- end -}}
{{- end -}}
