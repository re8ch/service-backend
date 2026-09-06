FROM docker.1ms.run/library/alpine:3.22

RUN apk add --no-cache python3 kubectl openssh-client ca-certificates curl github-cli
COPY clusters/re8ch/components/ai-operations/ai_operations_mcp.py /app/ai_operations_mcp.py
COPY clusters/re8ch/components/ai-operations/grafana_exploration.py /app/grafana_exploration.py
COPY clusters/re8ch/components/ai-operations/grafana_capabilities.json /app/grafana_capabilities.json
COPY clusters/re8ch/components/ai-operations/harbor_integration.py /app/harbor_integration.py
COPY clusters/re8ch/components/ai-operations/harbor_supply_chain.py /app/harbor_supply_chain.py
COPY clusters/re8ch/components/ai-operations/tenant_infrastructure.py /app/tenant_infrastructure.py
COPY clusters/re8ch/components/ai-operations/tenant_control_controller.py /app/tenant_control_controller.py
COPY clusters/re8ch/components/ai-operations/tenant_claims.py /app/tenant_claims.py
COPY operations/tenant-broker/contracts.py /app/tenant_claim_contracts.py
RUN addgroup -S re8ch && adduser -S -G re8ch -h /var/lib/re8ch-ai-operations re8ch \
    && mkdir -p /etc/re8ch-ai-operations/kubeconfigs /var/lib/re8ch-ai-operations \
    && chown -R re8ch:re8ch /var/lib/re8ch-ai-operations
USER re8ch
EXPOSE 19443
ENTRYPOINT ["python3", "/app/ai_operations_mcp.py"]
