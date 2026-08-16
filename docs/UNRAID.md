# Unraid installation

ServerSense is designed to run as one Docker container. The Community Applications template is a draft pending submission; it uses the public GitHub Container Registry image.

## Manual Docker setup

Build or pull the ServerSense image, then create a container with:

- Web UI port: container `8080`, host `8080` or another free port.
- `/config` → `/mnt/user/appdata/serversense` read/write.
- `/mnt/user` → `/mnt/user` read-only.
- `/var/local/emhttp` → `/var/local/emhttp` read-only.
- `/etc/unraid-version` → `/etc/unraid-version` read-only.
- `/dev` → `/dev` read-only for SMART access.
- Extra Parameters: `--cap-add=SYS_RAWIO --device-cgroup-rule='b 8:* r' --device-cgroup-rule='b 259:* r'` for read-only SATA/SAS and NVMe access, including SAT passthrough.
- `/var/run/docker.sock` → `/var/run/docker.sock` read-only for container inventory.
- `SERVERSENSE_SECRET_KEY` → a stable random 64-character hex string.
- `SERVERSENSE_ARRAY_PATH=/mnt/user`.

Example command:

```bash
docker run -d \
  --name serversense \
  --restart unless-stopped \
  --security-opt no-new-privileges:true \
  --cap-add SYS_RAWIO \
  -p 8080:8080 \
  -e SERVERSENSE_SECRET_KEY="$(openssl rand -hex 32)" \
  -e SERVERSENSE_ARRAY_PATH=/mnt/user \
  -v /mnt/user/appdata/serversense:/config \
  -v /mnt/user:/mnt/user:ro \
  -v /var/local/emhttp:/var/local/emhttp:ro \
  -v /etc/unraid-version:/etc/unraid-version:ro \
  -v /dev:/dev:ro \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  --device-cgroup-rule='b 8:* r' \
  --device-cgroup-rule='b 259:* r' \
  ghcr.io/jjermany/serversense:latest
```

Save the generated secret in a password manager before running the container. Reuse it on every update; it protects encrypted provider credentials.

After applying or updating the template, verify SMART access with the container's exact,
case-sensitive name and one physical device reported by `disks.ini`:

```bash
docker exec ServerSense smartctl -a -j /dev/sde
```

An `Operation not permitted` message or an SAT response containing only the device header
means the container was not recreated with `SYS_RAWIO` and the device cgroup rules. Do not
enable privileged mode. Edit the container, switch to Advanced View, confirm all three entries
are present under Extra Parameters, and apply the update.

## Updating and backup

Stop the container, back up `/mnt/user/appdata/serversense`, pull/build the new image, and recreate the container with the same paths and secret. Startup automatically runs database migrations. Restoring the appdata directory and the same secret restores the installation.

## Security notes

The Docker socket grants meaningful host visibility even with a read-only filesystem mount. The ServerSense collector only performs container inventory reads; the API and SENSE tool registry provide no Docker control operation. Avoid public port forwarding. Use a trusted VPN or authenticated HTTPS reverse proxy for remote access.
