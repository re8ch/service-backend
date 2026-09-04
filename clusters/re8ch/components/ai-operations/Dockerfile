FROM docker.1ms.run/library/alpine:3.22

RUN apk add --no-cache python3 kubectl openssh-client ca-certificates curl github-cli
COPY ai_operations_mcp.py /app/ai_operations_mcp.py
COPY grafana_exploration.py /app/grafana_exploration.py
COPY harbor_integration.py /app/harbor_integration.py
COPY tenant_infrastructure.py /app/tenant_infrastructure.py
RUN addgroup -S re8ch && adduser -S -G re8ch -h /var/lib/re8ch-ai-operations re8ch \
    && mkdir -p /etc/re8ch-ai-operations/kubeconfigs /var/lib/re8ch-ai-operations \
    && chown -R re8ch:re8ch /var/lib/re8ch-ai-operations
USER re8ch
EXPOSE 19443
ENTRYPOINT ["python3", "/app/ai_operations_mcp.py"]
