FROM python:3.12.11-slim
WORKDIR /app
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY secretary ./secretary
USER 65532:65532
EXPOSE 8081 9080
CMD ["hypercorn", "--bind", "0.0.0.0:9080", "secretary.restate_services:app"]
