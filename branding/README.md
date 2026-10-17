# Branding

Put your organisation's logo here, and the MSP Console shows it at the top of the left rail and in
the top bar on phones:

* `logo.svg` (preferred) or `logo.png`, with a transparent background
* made for a dark background: the console's rail is dark navy
* about 4:1 or narrower; it is shown 32 pixels high
* readable by everyone (`chmod 644 branding/logo.svg`), because the web server runs as its own user

The console preview shows it today when the folder is mounted into the preview image:

```bash
docker run -d -p 8080:8080 -v /opt/ioh-msp-stack/branding:/usr/share/nginx/html/brand:ro \
  adityanugraha/ioh-msp-stack:console-preview
```

From kit 1.2.0 the console itself serves it from this folder.

The logo file is not part of the kit and is never committed: this repository is public, and a
company's logo belongs to its brand owner. Ask your brand team for the official file. Without a file
the console shows its own neutral mark.
