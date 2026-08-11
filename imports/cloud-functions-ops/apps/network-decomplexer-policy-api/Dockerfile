FROM python:3.13.7-alpine3.22
WORKDIR /app
COPY apps/network-decomplexer-policy-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/network-decomplexer-policy-api/app.py .
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["python", "/app/app.py"]
