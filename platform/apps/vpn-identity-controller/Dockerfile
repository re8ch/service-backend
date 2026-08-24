FROM python:3.12-alpine

WORKDIR /app
COPY apps/vpn-identity-controller/controller.py /app/controller.py

RUN adduser -D -u 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data

USER appuser
EXPOSE 8080
CMD ["python", "/app/controller.py", "serve"]
