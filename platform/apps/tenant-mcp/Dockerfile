FROM golang:1.25-alpine AS build
WORKDIR /src
COPY platform/apps/tenant-mcp/go.mod platform/apps/tenant-mcp/go.sum ./
RUN go mod download
COPY platform/apps/tenant-mcp/*.go ./
COPY platform/apps/tenant-mcp/*.json ./
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags='-s -w' -o /tenant-mcp .

FROM registry.k8s.io/kubectl:v1.34.1@sha256:59bafa07ff3a6d4b417e7633ddb9d79a9606ca98bf64bac080b3e65748669250 AS kubectl

FROM alpine:3.22
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl
RUN addgroup -S re8ch && adduser -S -G re8ch re8ch \
    && mkdir -p /var/lib/tenant-platform && chown re8ch:re8ch /var/lib/tenant-platform
COPY --from=build /tenant-mcp /usr/local/bin/tenant-mcp
USER re8ch
ENV HOME=/var/lib/tenant-platform
EXPOSE 19448 19450
ENTRYPOINT ["/usr/local/bin/tenant-mcp"]
