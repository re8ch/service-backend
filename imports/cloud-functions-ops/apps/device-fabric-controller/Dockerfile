FROM python:3.12-alpine@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31
RUN addgroup -S controller && adduser -S -G controller -u 65532 controller
COPY controller.py /controller.py
USER 65532:65532
ENTRYPOINT ["python3", "/controller.py"]
