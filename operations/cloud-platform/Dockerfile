FROM python:3.12-alpine
RUN addgroup -S re8ch && adduser -S -G re8ch -u 65532 re8ch && apk add --no-cache kubectl openssh-client
WORKDIR /app
COPY operations/cloud-platform/runtime.py operations/cloud-platform/server.py ./
COPY inventory/ /etc/re8ch-platform/inventory/
RUN pip install --no-cache-dir PyYAML==6.0.2
USER 65532:65532
EXPOSE 19443
ENTRYPOINT ["python", "/app/server.py"]
