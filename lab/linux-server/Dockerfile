# Lab only: a small Linux "customer server" reachable over SSH (port 22) for Semaphore tests.
FROM alpine:3.22
COPY certs/ /usr/local/share/ca-certificates/
RUN cat /usr/local/share/ca-certificates/*.crt >> /etc/ssl/certs/ca-certificates.crt 2>/dev/null || true; \
    apk add --no-cache openssh python3 sudo bash \
 && adduser -D -s /bin/bash netadmin && echo "netadmin:Lab-Device-Pass1" | chpasswd \
 && echo "netadmin ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/netadmin \
 && ssh-keygen -A && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
EXPOSE 22
CMD ["/usr/sbin/sshd", "-D", "-e"]
