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

# Debian security updates. The upstream image is rebuilt less often than Debian publishes fixes
# (in October 2026 Trivy found 77 fixed HIGH findings in it). Best effort: when the build cannot
# reach https://deb.debian.org, the image builds without them and the build log says so. Nothing
# is installed until every update has downloaded, so a broken link never leaves half an update.
RUN set -e; \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources || true; \
    if apt-get update -qq && apt-get -y -qq --download-only upgrade; then \
      DEBIAN_FRONTEND=noninteractive apt-get -y -qq -o Dpkg::Options::=--force-confold upgrade; \
    else \
      echo "Debian security updates skipped: https://deb.debian.org was not reachable"; \
    fi; \
    apt-get clean; rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/msp-requirements.txt
RUN pip install --no-cache-dir -r /tmp/msp-requirements.txt

COPY nautobot_config.py /opt/nautobot/nautobot_config.py

# Create the folders that are mounted as volumes, owned by the nautobot user,
# so new Docker volumes inherit the right owner.
RUN mkdir -p /opt/nautobot/media /opt/nautobot/git \
 && chown -R nautobot:nautobot /opt/nautobot/media /opt/nautobot/git

USER nautobot
