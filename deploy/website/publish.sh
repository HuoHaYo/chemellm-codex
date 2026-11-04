#!/bin/bash
# Run on the website host after unpacking the archive into its own staging folder.
set -euo pipefail
stage="$(cd -- "$(dirname -- "$0")" && pwd)"
site_root=/var/www/chemellm-site
config=/etc/nginx/conf.d/chemellm.online.conf
release_id="${1:?Usage: bash publish.sh YYYYMMDD-HHMMSS}"
[[ "$release_id" =~ ^[0-9]{8}-[0-9]{6}$ ]] || { echo 'Invalid release ID' >&2; exit 1; }
[[ -s /etc/letsencrypt/live/chemellm.online/fullchain.pem ]] || { echo 'Issue the TLS certificate first' >&2; exit 1; }
openssl x509 -in /etc/letsencrypt/live/chemellm.online/fullchain.pem -checkend 86400 -noout
cd "$stage/public"
sha256sum --check "$stage/SHA256SUMS"
mkdir -p "$site_root/releases" "$site_root/backups"
release_dir="$site_root/releases/$release_id"
[[ ! -e "$release_dir" ]] || { echo 'Release ID already exists' >&2; exit 1; }
cp -a "$stage/public" "$release_dir"
chmod -R a+rX "$release_dir"
if command -v restorecon >/dev/null 2>&1; then restorecon -RF "$site_root"; fi
backup="$site_root/backups/nginx-$release_id.conf"
cp -p "$config" "$backup"
previous="$(readlink "$site_root/current" || true)"
rollback() {
    cp -p "$backup" "$config"
    if [[ -n "$previous" ]]; then
        ln -s "$previous" "$site_root/rollback-$release_id"
        mv -Tf "$site_root/rollback-$release_id" "$site_root/current"
    fi
    nginx -t && systemctl reload nginx
}
trap 'rollback' ERR
cp "$stage/nginx.conf" "$config"
nginx -t
ln -s "$release_dir" "$site_root/next-$release_id"
mv -Tf "$site_root/next-$release_id" "$site_root/current"
systemctl reload nginx
curl --fail --silent --show-error --max-time 10 --retry 5 --retry-connrefused --retry-delay 1 --resolve chemellm.online:443:127.0.0.1 https://chemellm.online/ -o /dev/null
trap - ERR
install -m 644 "$stage/chemellm-site-renew.service" /etc/systemd/system/chemellm-site-renew.service
install -m 644 "$stage/chemellm-site-renew.timer" /etc/systemd/system/chemellm-site-renew.timer
systemctl daemon-reload
systemctl enable --now chemellm-site-renew.timer
echo "Published $release_id; previous Nginx configuration: $backup"
