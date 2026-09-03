ARG BASE_IMAGE=registry.re8ch.com/helm/connectool-ci-base@sha256:cf8340822df5dfb3cd4e2fb04a09b76a513b6c406a1262fb99870c1214a0f823
FROM ${BASE_IMAGE}

USER root

ARG COSIGN_VERSION=v2.4.3
ARG CRANE_VERSION=v0.20.3
ARG ORAS_VERSION=1.2.3
ARG HELM_VERSION=v3.18.6
ARG KUSTOMIZE_VERSION=v5.6.0
ARG TARGETARCH

RUN sed -i 's|https://dl-cdn.alpinelinux.org/alpine|https://mirrors.aliyun.com/alpine|g' /etc/apk/repositories \
    && apk add --no-cache bash ca-certificates curl git tar

RUN curl --fail --silent --show-error --location --retry 8 --retry-all-errors \
      --connect-timeout 30 --output /usr/local/bin/cosign \
      "https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/cosign-linux-${TARGETARCH}" \
    && chmod 0755 /usr/local/bin/cosign \
    && case "${TARGETARCH}" in amd64) crane_arch=x86_64 ;; arm64) crane_arch=arm64 ;; *) exit 1 ;; esac \
    && curl --fail --silent --show-error --location --retry 8 --retry-all-errors \
      --connect-timeout 30 --output /tmp/crane.tar.gz \
      "https://github.com/google/go-containerregistry/releases/download/${CRANE_VERSION}/go-containerregistry_Linux_${crane_arch}.tar.gz" \
    && tar -tzf /tmp/crane.tar.gz crane >/dev/null \
    && tar -xzf /tmp/crane.tar.gz -C /usr/local/bin crane \
    && curl --fail --silent --show-error --location --retry 8 --retry-all-errors \
      --connect-timeout 30 --output /tmp/oras.tar.gz \
      "https://github.com/oras-project/oras/releases/download/v${ORAS_VERSION}/oras_${ORAS_VERSION}_linux_${TARGETARCH}.tar.gz" \
    && tar -tzf /tmp/oras.tar.gz oras >/dev/null \
    && tar -xzf /tmp/oras.tar.gz -C /usr/local/bin oras \
    && curl --fail --silent --show-error --location --retry 8 --retry-all-errors \
      --connect-timeout 30 --output /tmp/kustomize.tar.gz \
      "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/${KUSTOMIZE_VERSION}/kustomize_${KUSTOMIZE_VERSION}_linux_${TARGETARCH}.tar.gz" \
    && tar -tzf /tmp/kustomize.tar.gz kustomize >/dev/null \
    && tar -xzf /tmp/kustomize.tar.gz -C /usr/local/bin kustomize \
    && curl --fail --silent --show-error --location --retry 8 --retry-all-errors \
      --connect-timeout 30 --output /tmp/helm.tar.gz \
      "https://get.helm.sh/helm-${HELM_VERSION}-linux-${TARGETARCH}.tar.gz" \
    && tar -tzf /tmp/helm.tar.gz "linux-${TARGETARCH}/helm" >/dev/null \
    && tar -xzf /tmp/helm.tar.gz -C /tmp "linux-${TARGETARCH}/helm" \
    && install -m 0755 "/tmp/linux-${TARGETARCH}/helm" /usr/local/bin/helm \
    && chmod 0755 /usr/local/bin/crane /usr/local/bin/kustomize /usr/local/bin/oras \
    && rm -rf /tmp/crane.tar.gz /tmp/kustomize.tar.gz /tmp/oras.tar.gz /tmp/helm.tar.gz "/tmp/linux-${TARGETARCH}"

RUN python -m pip install --disable-pip-version-check --no-cache-dir \
      --index-url https://mirrors.aliyun.com/pypi/simple/ \
      PyYAML==6.0.2 pytest==8.3.5 jsonschema==4.23.0

ENTRYPOINT []
CMD ["/bin/sh"]
