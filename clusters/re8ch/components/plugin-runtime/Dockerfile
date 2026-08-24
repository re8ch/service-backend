FROM alpine:3.22

# Domain CLIs are executed only from separately pinned executor images.  The
# generic runtime stays small and does not download mutable binaries at build
# time.
RUN apk add --no-cache python3 ca-certificates
COPY runtime.py /app/runtime.py
RUN addgroup -S -g 10001 plugin && adduser -S -D -H -u 10001 -G plugin -h /var/lib/re8ch-plugin-runtime plugin \
    && mkdir -p /etc/re8ch-plugins/definitions /var/lib/re8ch-plugin-runtime \
    && chown -R plugin:plugin /var/lib/re8ch-plugin-runtime
USER 10001:10001
EXPOSE 8088
ENTRYPOINT ["python3", "/app/runtime.py"]
