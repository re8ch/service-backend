FROM golang:1.25-alpine AS build
WORKDIR /src
COPY go.mod main.go ./
RUN apk add --no-cache ca-certificates && \
    CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o /regional-routing-authority .

FROM scratch
COPY --from=build /regional-routing-authority /regional-routing-authority
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
USER 65532:65532
ENTRYPOINT ["/regional-routing-authority"]
