#!/usr/bin/env bash
# Create .env from .env.example and replace every placeholder with a random value.
# Usage: ./scripts/gen-env.sh            (refuses to overwrite an existing .env)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  echo ".env already exists. Move it away first if you really want a new one." >&2
  exit 1
fi

rand()    { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c "${1:-28}"; }
hex40()   { openssl rand -hex 20; }
b64_32()  { openssl rand -base64 32; }

cp .env.example .env
tmp=$(mktemp)
while IFS= read -r line; do
  line="${line//__RANDOM64__/$(rand 64)}"
  line="${line//__RANDOM__/$(rand 28)}"
  line="${line//__HEX40__/$(hex40)}"
  line="${line//__B64_32__/$(b64_32)}"
  printf '%s\n' "$line"
done < .env > "$tmp"
mv "$tmp" .env
chmod 600 .env
echo "Created .env with random secrets. Store a copy in your password manager."
