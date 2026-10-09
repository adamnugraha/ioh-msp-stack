# Nautobot with the Golden Config and Nornir apps baked in.
# Build:  docker compose build nautobot
ARG NAUTOBOT_IMAGE=networktocode/nautobot:3.2.6-py3.12
FROM ${NAUTOBOT_IMAGE}

USER 0

# Optional: trust extra certificate authorities (for example a corporate
# TLS-inspection proxy). Drop *.crt files into ./certs before building.
COPY certs/ /usr/local/share/ca-certificates/extra/
RUN update-ca-certificates >/dev/null 2>&1 || true
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

COPY requirements.txt /tmp/msp-requirements.txt
RUN pip install --no-cache-dir -r /tmp/msp-requirements.txt

COPY nautobot_config.py /opt/nautobot/nautobot_config.py

# Create the folders that are mounted as volumes, owned by the nautobot user,
# so new Docker volumes inherit the right owner.
RUN mkdir -p /opt/nautobot/media /opt/nautobot/git \
 && chown -R nautobot:nautobot /opt/nautobot/media /opt/nautobot/git

USER nautobot
