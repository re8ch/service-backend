FROM python:3.13.7-alpine3.22@sha256:9ba6d8cbebf0fb6546ae71f2a1c14f6ffd2fdab83af7fa5669734ef30ad48844
WORKDIR /app
COPY apps/network-decomplexer-policy-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/network-decomplexer-policy-api/app.py .
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["python", "/app/app.py"]
