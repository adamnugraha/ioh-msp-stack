# msp-glue and the MSP Console, in one image.
#
# Stage 1 builds the console's pages (React, glue/console-ui) with Node; only the built files go into
# the image, so it holds no Node and no npm packages. Stage 2 is the Python service.

FROM node:22-alpine AS pages
WORKDIR /src/console-ui
# Optional: certificate authorities of a TLS-inspecting proxy, as .crt files in certs/ (none by default)
COPY certs/ /usr/local/share/extra-ca/
RUN cat /usr/local/share/extra-ca/*.crt > /usr/local/share/extra-ca.pem 2>/dev/null || true
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/extra-ca.pem
# Exact versions from package-lock.json, checked against their recorded hashes
COPY console-ui/package.json console-ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY console-ui/ ./
# Writes /src/msp_glue/console/ui (vite.config.ts)
RUN npm run build

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
COPY --from=pages /src/msp_glue/console/ui /src/msp_glue/console/ui
RUN test -f /src/msp_glue/console/ui/index.html \
 && pip install --no-cache-dir -c /src/constraints.txt /src && rm -rf /src && useradd --system --uid 10001 glue \
 && mkdir -p /data && chown glue /data
WORKDIR /app
COPY mappings.yaml /app/mappings.yaml

USER glue
EXPOSE 80
CMD ["mspctl", "serve", "--port", "80"]
