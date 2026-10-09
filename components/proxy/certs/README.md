# Certificates for the reverse proxy

Leave this folder empty to use Caddy's internal certificate authority (the default,
`CADDY_TLS=internal` in `.env`).

To use IOH certificates, get two files from IOH's certificate authority:

* `fullchain.pem`: the server certificate for `*.<MSP_DOMAIN>` (or one listing every name in
  `../Caddyfile`), followed by the intermediate certificates
* `privkey.pem`: its private key, without a pass phrase

Then, from the kit folder:

```bash
./scripts/install.sh certs ~/fullchain.pem ~/privkey.pem
```

The command checks that the key belongs to the certificate, that the certificate covers every
name and is not about to expire, copies both files here, sets
`CADDY_TLS="/certs/fullchain.pem /certs/privkey.pem"` in `.env`, restarts the proxy and checks
that every name now serves the new certificate. If anything is wrong it changes nothing, or puts
the previous certificates back.

Back to Caddy's own certificates: `./scripts/install.sh certs internal`.

The files in this folder are never committed to Git (see `.gitignore`); the nightly backup
includes them.
