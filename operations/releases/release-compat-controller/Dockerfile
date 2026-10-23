FROM --platform=$BUILDPLATFORM docker.io/library/golang:1.26.10-alpine3.23@sha256:0525c952defebc693cdaf62cdf43afa32be5a7f377e99260642ea19c240c708e AS builder
WORKDIR /src
ENV CGO_ENABLED=0
ARG TARGETOS TARGETARCH
COPY . .
RUN GOOS=$TARGETOS GOARCH=$TARGETARCH go build -trimpath -ldflags="-s -w" -o /out/controller .

FROM scratch
COPY --from=builder /out/controller /controller
USER 65532:65532
EXPOSE 8080 8081
ENTRYPOINT ["/controller"]
