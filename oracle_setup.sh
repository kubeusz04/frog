#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin git curl
sudo systemctl enable --now docker

sudo iptables -C INPUT -p tcp --dport 8000 -j ACCEPT 2>/dev/null || \
  sudo iptables -I INPUT -p tcp --dport 8000 -j ACCEPT

if command -v netfilter-persistent >/dev/null 2>&1; then
  sudo netfilter-persistent save || true
fi

if [ ! -f youtube-cookies.txt ]; then
  cat > youtube-cookies.txt <<'EOF'
# Netscape HTTP Cookie File
# Paste filtered YouTube cookies here.
EOF
fi

sudo docker compose up -d --build

echo
echo "Frog backend is starting. Test locally with:"
echo "curl http://localhost:8000/api/health"
