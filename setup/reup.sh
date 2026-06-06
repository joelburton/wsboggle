#!/usr/bin/env bash
# Pull latest wsboggle, rebuild, and restart the systemd service.
# Invoked from another machine with:
#   ssh myserver 'bash wsboggle/setup/reup.sh'
set -euo pipefail

cd /home/admin/wsboggle

git pull --ff-only

make lib

( cd client && npm install && npm run build )

.venv/bin/pip install -e .

sudo systemctl restart wsboggle.service
sudo systemctl --no-pager status wsboggle.service
