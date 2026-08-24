FROM golang:1.25-alpine AS build
WORKDIR /src
COPY apps/akash-lease-admission/main.go ./main.go
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o /out/akash-lease-admission ./main.go

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/akash-lease-admission /akash-lease-admission
USER 65532:65532
ENTRYPOINT ["/akash-lease-admission"]
