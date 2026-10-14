FROM --platform=$BUILDPLATFORM docker.io/library/golang:1.26.9-alpine3.23@sha256:96123126ac58e910f4dd3619a8901e2fb6d1ad84b59b1232cac7c9ea65a8f888 AS builder
WORKDIR /src
ENV CGO_ENABLED=0
ARG TARGETOS TARGETARCH
COPY . .
RUN GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -ldflags="-s -w" -o /out/result-proxy ./cmd/result-proxy

FROM scratch
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=builder /out/result-proxy /result-proxy
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/result-proxy"]
