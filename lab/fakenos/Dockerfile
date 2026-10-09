# Lab only: simulated Cisco IOS devices (FakeNOS) that answer SSH with a running configuration.
FROM python:3.12-slim
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN update-ca-certificates >/dev/null 2>&1 || true
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt
RUN pip install --no-cache-dir fakenos==1.2.1
WORKDIR /app
COPY entrypoint.py /app/entrypoint.py
CMD ["python", "/app/entrypoint.py"]
