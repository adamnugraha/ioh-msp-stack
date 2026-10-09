<?php
// Local overrides for GLPI constants. Loaded automatically from the config directory.
//
// GLPI refuses to call webhook URLs that contain a port number (for example
// http://msp-glue:8080/...). msp-glue listens on port 80 to avoid this, so the
// default allowlist is enough. If you ever need a URL with a port, extend the
// allowlist here. Each entry is a regular expression matched against the full URL.
//
// define('GLPI_SERVERSIDE_URL_ALLOWLIST', [
//     '~^https?://[a-z0-9.-]+(:\d+)?(/.*)?$~i',
// ]);
