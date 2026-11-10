FROM python:3.13.7-alpine3.22@sha256:9ba6d8cbebf0fb6546ae71f2a1c14f6ffd2fdab83af7fa5669734ef30ad48844

RUN python -m pip install --no-cache-dir 'psycopg==3.3.8' 'psycopg-binary==3.3.8'
COPY platform/apps/postgresql-provider/postgresql_provider.py /app/postgresql_provider.py
COPY clusters/re8ch/components/ai-operations/tenant_control_controller.py /app/tenant_control_controller.py
COPY clusters/re8ch/components/ai-operations/tenant_claims.py /app/tenant_claims.py
COPY operations/tenant-broker/contracts.py /app/tenant_claim_contracts.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["python3", "/app/postgresql_provider.py"]
