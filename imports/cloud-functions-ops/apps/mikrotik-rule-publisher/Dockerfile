FROM python:3.11-alpine

WORKDIR /app
COPY apps/mikrotik-rule-publisher/server.py /app/server.py
COPY tools/mikrotik_rules/build_split_lists.py /app/build_split_lists.py
COPY config/mikrotik-routing /config
COPY dist/mikrotik /seed

RUN adduser -D -u 10001 appuser \
    && mkdir -p /data/public/rsc \
    && chown -R appuser:appuser /data /seed
USER appuser

EXPOSE 8080
CMD ["python", "/app/server.py", "--root", "/data/public/rsc", "--port", "8080"]
