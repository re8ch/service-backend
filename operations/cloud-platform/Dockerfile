FROM --platform=$BUILDPLATFORM golang:1.24-alpine AS network-planner
ARG TARGETOS TARGETARCH
WORKDIR /src
COPY operations/cloud-platform/network-planner/ ./
RUN go test ./... && CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -o /network-planner .

FROM python:3.12-alpine
RUN addgroup -S re8ch && adduser -S -G re8ch -u 65532 re8ch && apk add --no-cache kubectl openssh-client
WORKDIR /app
RUN pip install --no-cache-dir PyYAML==6.0.2 jsonschema==4.23.0 schemathesis==4.29.1
COPY operations/cloud-platform/runtime.py operations/cloud-platform/server.py operations/cloud-platform/plugin_capabilities.py operations/cloud-platform/plugin_http_conformance.py operations/cloud-platform/plugin_http_transport.py operations/cloud-platform/anatomy-operation-authority.json operations/cloud-platform/storage-read-projection.json operations/cloud-platform/mihomos_capabilities.py operations/cloud-platform/mihomo-controller-openapi.json operations/cloud-platform/plugin-operation-tools.json ./
COPY operations/cloud-platform/anatomy_diff_operation.py ./
COPY operations/cloud-platform/cilium_cidr_operation.py ./
COPY operations/cloud-platform/network_native_mutations.py ./
COPY operations/cloud-platform/stalwart_admin.py operations/cloud-platform/stalwart_bootstrap.py ./
COPY operations/cloud-platform/network-planner/operations.json ./network-planner/operations.json
COPY --from=network-planner /network-planner ./network-planner/network-planner
COPY operations/plugin-capabilities/openapi/apis_anatomy.re8ch.com_v1alpha1.json ./anatomy-native-openapi.json
COPY operations/plugin-capabilities/openapi/apis_cilium.io_v2.json ./cilium-native-openapi.json
COPY operations/plugin-capabilities/openapi/apis_networking.advfab.org_v1alpha1.json operations/plugin-capabilities/openapi/apis_networking.advfab.org_v1alpha2.json operations/plugin-capabilities/openapi/apis_networking.re8ch.com_v1alpha1.json operations/plugin-capabilities/openapi/apis_networking.re8ch.com_v1alpha2.json ./
COPY operations/workspace-controller/workspace.py ./workspace.py
COPY inventory/ /etc/re8ch-platform/inventory/
COPY catalogs/ /srv/service-backend/catalogs/
COPY workspace/ /srv/service-backend/workspace/
COPY indexes/projects.json /srv/service-backend/indexes/projects.json
COPY schemas/ /srv/service-backend/schemas/
USER 65532:65532
EXPOSE 19443
ENTRYPOINT ["python", "/app/server.py"]
