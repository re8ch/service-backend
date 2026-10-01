FROM golang:1.25-alpine AS build
WORKDIR /src
COPY platform/apps/tenant-mcp/go.mod platform/apps/tenant-mcp/go.sum ./
RUN go mod download
COPY platform/apps/tenant-mcp/*.go ./
COPY platform/apps/tenant-mcp/*.json ./
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags='-s -w' -o /tenant-mcp .

FROM alpine:3.22
RUN apk add --no-cache ca-certificates kubectl && addgroup -S re8ch && adduser -S -G re8ch re8ch \
    && mkdir -p /var/lib/tenant-platform && chown re8ch:re8ch /var/lib/tenant-platform
COPY --from=build /tenant-mcp /usr/local/bin/tenant-mcp
USER re8ch
ENV HOME=/var/lib/tenant-platform
EXPOSE 19448 19450
ENTRYPOINT ["/usr/local/bin/tenant-mcp"]
