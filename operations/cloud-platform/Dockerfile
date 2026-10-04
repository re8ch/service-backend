FROM python:3.12-alpine
RUN addgroup -S re8ch && adduser -S -G re8ch -u 65532 re8ch && apk add --no-cache kubectl openssh-client
WORKDIR /app
COPY operations/cloud-platform/runtime.py operations/cloud-platform/server.py operations/cloud-platform/plugin_capabilities.py operations/cloud-platform/plugin_http_conformance.py operations/cloud-platform/plugin_http_transport.py operations/cloud-platform/anatomy-operation-authority.json operations/cloud-platform/storage-read-projection.json operations/cloud-platform/mihomos_capabilities.py operations/cloud-platform/mihomo-controller-openapi.json operations/cloud-platform/plugin-operation-tools.json ./
COPY operations/cloud-platform/anatomy_diff_operation.py ./
COPY operations/plugin-capabilities/openapi/apis_anatomy.re8ch.com_v1alpha1.json ./anatomy-native-openapi.json
COPY operations/workspace-controller/workspace.py ./workspace.py
COPY inventory/ /etc/re8ch-platform/inventory/
COPY catalogs/ /srv/service-backend/catalogs/
COPY workspace/ /srv/service-backend/workspace/
COPY schemas/ /srv/service-backend/schemas/
RUN pip install --no-cache-dir PyYAML==6.0.2 jsonschema==4.23.0 schemathesis==4.29.1
USER 65532:65532
EXPOSE 19443
ENTRYPOINT ["python", "/app/server.py"]
