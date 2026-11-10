FROM alpine:3.22

RUN apk add --no-cache python3 py3-psycopg2 ca-certificates \
    && addgroup -S provider && adduser -S -G provider -h /var/lib/postgresql-provider provider
COPY platform/apps/postgresql-provider/postgresql_provider.py /app/postgresql_provider.py
COPY clusters/re8ch/components/ai-operations/tenant_control_controller.py /app/tenant_control_controller.py
USER provider
EXPOSE 8080
ENTRYPOINT ["python3", "/app/postgresql_provider.py"]
