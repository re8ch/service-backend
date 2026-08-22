FROM python:3.12-alpine
RUN apk add --no-cache git openssh-client && addgroup -S re8ch && adduser -S -G re8ch -u 65532 re8ch
WORKDIR /app
COPY operations/workspace-controller/mirror.py ./mirror.py
COPY workspace/workspace.lock.yaml /config/workspace.lock.yaml
RUN pip install --no-cache-dir PyYAML==6.0.2
USER 65532:65532
ENTRYPOINT ["python", "/app/mirror.py"]
