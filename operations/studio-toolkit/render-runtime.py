"""Render digest-pinned runtime manifests; secret provisioning is separate."""
import argparse
from pathlib import Path
import re
import yaml

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--api-image', required=True)
p.add_argument('--worker-image', required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
for image, target in [(a.api_image, 'api'), (a.worker_image, 'worker')]:
    if not re.fullmatch(r'registry\.re8ch\.com/studio-toolkit/' + target + r'@sha256:[0-9a-f]{64}', image):
        p.error('Use verified immutable Studio release digests')
ns = 'studio-toolkit'
docs = []

def obj(kind, name, spec=None, namespace=ns, api='v1', **extra):
    d = {'apiVersion': api, 'kind': kind, 'metadata': {'name': name}}
    if namespace:
        d['metadata']['namespace'] = namespace
    if spec is not None:
        d['spec'] = spec
    d.update(extra)
    docs.append(d)
    return d


def env(name, value=None, key=None, secret='studio-config'):
    return {'name': name, **({'valueFrom': {'secretKeyRef': {'name': secret, 'key': key or name}}} if value is None else {'value': value})}


security = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}

def deploy(name, image, variables, mounts=None, volumes=None, replicas=1, command=None, args=None, uid=10001, resources=None, probe=None):
    c = {'name': name, 'image': image, 'securityContext': security, 'env': variables,
         'resources': resources or {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '1', 'memory': '1Gi'}}}
    if mounts: c['volumeMounts'] = mounts
    if command: c['command'] = command
    if args: c['args'] = args
    if probe:
        c['readinessProbe'] = {'httpGet': probe, 'initialDelaySeconds': 5, 'periodSeconds': 10}
        c['livenessProbe'] = {'httpGet': probe, 'initialDelaySeconds': 30, 'periodSeconds': 20}
    pod = {'automountServiceAccountToken': False, 'nodeSelector': {'kubernetes.io/hostname': 'restricted-node-6dc3f140f9'},
           'imagePullSecrets': [{'name': 'studio-registry-reader'}],
           'securityContext': {'runAsNonRoot': True, 'runAsUser': uid, 'runAsGroup': uid, 'fsGroup': uid, 'seccompProfile': {'type': 'RuntimeDefault'}},
           'containers': [c], 'terminationGracePeriodSeconds': 30}
    if volumes: pod['volumes'] = volumes
    obj('Deployment', name, {'replicas': replicas, 'selector': {'matchLabels': {'app': name}}, 'template': {'metadata': {'labels': {'app': name}}, 'spec': pod}}, api='apps/v1')


def service(name, port):
    obj('Service', name, {'selector': {'app': name}, 'ports': [{'name': 'http', 'port': port, 'targetPort': port}]})


obj('Namespace', ns, namespace=None)
obj('PersistentVolumeClaim', 'studio-blobs', {'accessModes': ['ReadWriteMany'], 'storageClassName': 'cephfs-r640', 'resources': {'requests': {'storage': '20Gi'}}})
api_env = [env('STUDIO_MODE', 'production'), env('STUDIO_ROOT_PATH', '/studio'), env('STUDIO_BLOB_ROOT', '/data'), env('STUDIO_AUTH_FILE', '/config/auth.json'), env('STUDIO_POSTGREST_RO', 'https://db-ro.re8ch.com/studio'), env('STUDIO_POSTGREST_RW', 'https://db-rw.re8ch.com/studio'), env('STUDIO_POSTGREST_JWT_KEY')]
deploy('studio-api', a.api_image, api_env, replicas=2,
       mounts=[{'name': 'blobs', 'mountPath': '/data'}, {'name': 'config', 'mountPath': '/config', 'readOnly': True}, {'name': 'tmp', 'mountPath': '/tmp'}],
       volumes=[{'name': 'blobs', 'persistentVolumeClaim': {'claimName': 'studio-blobs'}}, {'name': 'config', 'secret': {'secretName': 'studio-config', 'items': [{'key': 'auth.json', 'path': 'auth.json'}]}}, {'name': 'tmp', 'emptyDir': {'sizeLimit': '1Gi'}}], probe={'path': '/healthz', 'port': 8080})
service('studio-api', 8080)
postgrest = 'docker.io/postgrest/postgrest:v14.12@sha256:54000f24847d01a2c2302e0041cf0618b875c57fb48507d743cfa9aaa50bf43c'
for mode in ['ro', 'rw']:
    name = 'studio-db-' + mode
    deploy(name, postgrest, [env('PGRST_DB_URI', key='PGRST_DB_URI_' + mode.upper()), env('PGRST_DB_SCHEMAS', 'studio'), env('PGRST_JWT_SECRET', key='STUDIO_POSTGREST_JWT_KEY'), env('PGRST_DB_POOL', '10'), env('PGRST_ADMIN_SERVER_PORT', '3001'), env('PGRST_ADMIN_SERVER_HOST', '0.0.0.0')], uid=10001, probe={'path': '/ready', 'port': 3001})
    service(name, 3000)
deploy('studio-worker-r640', a.worker_image, [env('STUDIO_URL', 'http://studio-api:8080'), env('STUDIO_WORKER_TOKEN', secret='studio-worker')],
       command=['python', '-m', 'toolkit.worker'], args=['--region', 'cn-r640', '--work', '/work', '--allow-private-http'],
       mounts=[{'name': 'work', 'mountPath': '/work'}, {'name': 'tmp', 'mountPath': '/tmp'}, {'name': 'home', 'mountPath': '/home/studio'}],
       volumes=[{'name': 'work', 'emptyDir': {'sizeLimit': '8Gi'}}, {'name': 'tmp', 'emptyDir': {'sizeLimit': '1Gi'}}, {'name': 'home', 'emptyDir': {'sizeLimit': '128Mi'}}],
       resources={'requests': {'cpu': '1', 'memory': '1Gi'}, 'limits': {'cpu': '4', 'memory': '4Gi', 'ephemeral-storage': '10Gi'}})
cleanup = {'automountServiceAccountToken': False, 'restartPolicy': 'Never', 'nodeSelector': {'kubernetes.io/hostname': 'restricted-node-6dc3f140f9'}, 'imagePullSecrets': [{'name': 'studio-registry-reader'}], 'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001, 'seccompProfile': {'type': 'RuntimeDefault'}}, 'containers': [{'name': 'collect', 'image': a.api_image, 'securityContext': security, 'env': [env('STUDIO_TOKEN', secret='studio-retention')], 'command': ['python', '-c', 'import os,httpx; r=httpx.post("http://studio-api:8080/v1/retention/collect",headers={"Authorization":"Bearer "+os.environ["STUDIO_TOKEN"]},timeout=60); r.raise_for_status(); print(r.text)'], 'resources': {'requests': {'cpu': '25m', 'memory': '64Mi'}, 'limits': {'cpu': '250m', 'memory': '256Mi'}}}]}
obj('CronJob', 'studio-retention', {'schedule': '17 3 * * *', 'concurrencyPolicy': 'Forbid', 'startingDeadlineSeconds': 1800, 'successfulJobsHistoryLimit': 1, 'failedJobsHistoryLimit': 2, 'jobTemplate': {'spec': {'backoffLimit': 2, 'activeDeadlineSeconds': 120, 'template': {'spec': cleanup}}}}, api='batch/v1')
for host, section, target, port in [('api.re8ch.com', 'api-https', 'studio-api', 8080), ('db-ro.re8ch.com', 'db-ro-https', 'studio-db-ro', 3000), ('db-rw.re8ch.com', 'db-rw-https', 'studio-db-rw', 3000)]:
    obj('HTTPRoute', target, {'parentRefs': [{'name': 're8ch-gateway-canary', 'namespace': 'kube-system', 'sectionName': section}], 'hostnames': [host], 'rules': [{'matches': [{'path': {'type': 'PathPrefix', 'value': '/studio'}}], 'filters': [{'type': 'URLRewrite', 'urlRewrite': {'path': {'type': 'ReplacePrefixMatch', 'replacePrefixMatch': '/'}}}], 'backendRefs': [{'name': target, 'port': port}]}]}, api='gateway.networking.k8s.io/v1')
# Only the migration job uses PostgreSQL protocol; all product API queries use PostgREST.
script = """export PGPASSWORD="$POSTGRES_ADMIN_PASSWORD"
export PGHOST=pg-re8ch-primary.pigsty-external.svc.cluster.local PGPORT=5432 PGUSER="$POSTGRES_ADMIN_USER"
until pg_isready; do sleep 5; done
psql -d postgres -v ON_ERROR_STOP=1 -v password="$STUDIO_DATABASE_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE studio_authenticator LOGIN NOINHERIT PASSWORD %L', :'password') WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname='studio_authenticator')\\gexec
SELECT format('ALTER ROLE studio_authenticator LOGIN NOINHERIT PASSWORD %L', :'password')\\gexec
SELECT 'CREATE DATABASE studio_toolkit TEMPLATE template0 ENCODING ''UTF8''' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='studio_toolkit')\\gexec
REVOKE ALL ON DATABASE studio_toolkit FROM PUBLIC;
GRANT CONNECT ON DATABASE studio_toolkit TO studio_authenticator;
SQL
psql -d studio_toolkit -v ON_ERROR_STOP=1 -f /migrations/001_studio.sql
psql -d studio_toolkit -v ON_ERROR_STOP=1 <<'SQL'
GRANT studio_app_ro,studio_app_rw,studio_identity_ro TO studio_authenticator;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
NOTIFY pgrst, 'reload schema';
SQL
"""
pod = {'automountServiceAccountToken': False, 'restartPolicy': 'OnFailure', 'nodeSelector': {'kubernetes.io/hostname': 'restricted-node-6dc3f140f9'}, 'imagePullSecrets': [{'name': 'studio-registry-reader'}], 'securityContext': {'runAsNonRoot': True, 'runAsUser': 999, 'runAsGroup': 999, 'fsGroup': 999, 'seccompProfile': {'type': 'RuntimeDefault'}}, 'volumes': [{'name': 'migrations', 'emptyDir': {}}],
 'initContainers': [{'name': 'source-migration', 'image': a.api_image, 'command': ['python', '-c', 'import shutil; shutil.copyfile("/app/toolkit/sql/001_studio.sql", "/migrations/001_studio.sql")'], 'securityContext': security, 'volumeMounts': [{'name': 'migrations', 'mountPath': '/migrations'}]}],
 'containers': [{'name': 'migrate', 'image': 'docker.io/library/postgres@sha256:7c688148e5e156d0e86df7ba8ae5a05a2386aaec1e2ad8e6d11bdf10504b1fb7', 'command': ['/bin/sh', '-ec'], 'args': [script], 'securityContext': security, 'env': [env('POSTGRES_ADMIN_USER', key='POSTGRES_USER_READ_WRITE', secret='supabase-pg-re8ch-secrets'), env('POSTGRES_ADMIN_PASSWORD', key='POSTGRES_PASSWORD', secret='supabase-pg-re8ch-secrets'), env('STUDIO_DATABASE_PASSWORD', secret='studio-db-bootstrap')], 'volumeMounts': [{'name': 'migrations', 'mountPath': '/migrations', 'readOnly': True}], 'resources': {'requests': {'cpu': '25m', 'memory': '32Mi'}, 'limits': {'cpu': '250m', 'memory': '256Mi'}}}]}
obj('Job', 'studio-database-bootstrap-v1', {'backoffLimit': 6, 'activeDeadlineSeconds': 600, 'template': {'spec': pod}}, namespace='supabase-pg-re8ch', api='batch/v1')
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(yaml.safe_dump_all(docs, sort_keys=False))
print('Rendered digest-pinned runtime without secret values')
