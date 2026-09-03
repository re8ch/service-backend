ARG BASE_IMAGE=registry.re8ch.com/helm/connectool-ci-base@sha256:cf8baa9d2874a910fd9e144edba15a09ea263710fb48bfb04c69f4b9b5dd830e
FROM ${BASE_IMAGE}

USER root

ARG COSIGN_VERSION=v2.4.3
ARG CRANE_VERSION=v0.20.3
ARG ORAS_VERSION=1.2.3
ARG HELM_VERSION=v3.18.6

RUN sed -i 's|https://dl-cdn.alpinelinux.org/alpine|https://mirrors.aliyun.com/alpine|g' /etc/apk/repositories \
    && apk add --no-cache bash ca-certificates curl git kustomize py3-pip python3 tar

RUN curl --fail --silent --show-error --location --retry 8 --retry-all-errors \
      --connect-timeout 30 --output /usr/local/bin/cosign \
      "https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/cosign-linux-amd64" \
    && chmod 0755 /usr/local/bin/cosign \
    && curl --fail --silent --show-error --location --retry 8 --retry-all-errors \
      --connect-timeout 30 --output /tmp/crane.tar.gz \
      "https://github.com/google/go-containerregistry/releases/download/${CRANE_VERSION}/go-containerregistry_Linux_x86_64.tar.gz" \
    && tar -tzf /tmp/crane.tar.gz crane >/dev/null \
    && tar -xzf /tmp/crane.tar.gz -C /usr/local/bin crane \
    && curl --fail --silent --show-error --location --retry 8 --retry-all-errors \
      --connect-timeout 30 --output /tmp/oras.tar.gz \
      "https://github.com/oras-project/oras/releases/download/v${ORAS_VERSION}/oras_${ORAS_VERSION}_linux_amd64.tar.gz" \
    && tar -tzf /tmp/oras.tar.gz oras >/dev/null \
    && tar -xzf /tmp/oras.tar.gz -C /usr/local/bin oras \
    && curl --fail --silent --show-error --location --retry 8 --retry-all-errors \
      --connect-timeout 30 --output /tmp/helm.tar.gz \
      "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" \
    && tar -tzf /tmp/helm.tar.gz linux-amd64/helm >/dev/null \
    && tar -xzf /tmp/helm.tar.gz -C /tmp linux-amd64/helm \
    && install -m 0755 /tmp/linux-amd64/helm /usr/local/bin/helm \
    && chmod 0755 /usr/local/bin/crane /usr/local/bin/oras \
    && rm -rf /tmp/crane.tar.gz /tmp/oras.tar.gz /tmp/helm.tar.gz /tmp/linux-amd64

RUN pip install --break-system-packages --disable-pip-version-check --no-cache-dir \
      --index-url https://mirrors.aliyun.com/pypi/simple/ \
      PyYAML==6.0.2 pytest==8.3.5 jsonschema==4.23.0

ENTRYPOINT []
CMD ["/bin/sh"]
