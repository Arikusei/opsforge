#!/usr/bin/env bash
set -euo pipefail
mkdir -p nginx/ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout nginx/ssl/privkey.pem \
  -out nginx/ssl/fullchain.pem \
  -subj "/CN=localhost/O=OpsForge"
echo "Self-signed certs written to nginx/ssl/"
