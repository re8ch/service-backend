FROM python:3.12-alpine
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY core.py app.py ./
USER 65532:65532
ENTRYPOINT ["python", "/app/app.py"]
