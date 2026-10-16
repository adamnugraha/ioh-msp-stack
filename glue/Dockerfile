FROM python:3.12-slim

# Optional: trust extra certificate authorities (corporate TLS-inspection proxy).
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN update-ca-certificates >/dev/null 2>&1 || true
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PYTHONUNBUFFERED=1

# Install the package from a build folder, then keep only the mapping file in /app,
# so the installed package is the only copy Python can import.
COPY pyproject.toml constraints.txt /src/
COPY msp_glue /src/msp_glue
RUN pip install --no-cache-dir -c /src/constraints.txt /src && rm -rf /src && useradd --system --uid 10001 glue \
 && mkdir -p /data && chown glue /data
WORKDIR /app
COPY mappings.yaml /app/mappings.yaml

USER glue
EXPOSE 80
CMD ["mspctl", "serve", "--port", "80"]
