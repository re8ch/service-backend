ARG BASE_IMAGE=cube-sandbox-int.tencentcloudcr.com/cube-sandbox/cube-webui:v0.6.0@sha256:9b79ff75dd2a763bb7b01ee4c6c05f785e8bad02989115001df3c7ffeb762747
FROM ${BASE_IMAGE}
COPY dist/ /usr/share/nginx/html/
LABEL org.opencontainers.image.title="CubeSandbox WebUI with browser PTY" \
      org.opencontainers.image.revision="8721dd151971ce3c2966482bbd32904ad98f378e"
