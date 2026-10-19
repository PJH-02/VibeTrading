# VibeTrading server deployment

1. Stop collectors before changing storage.
2. Inspect the actual device and UUID with lsblk -f and findmnt. Never format a device based on a guessed name.
3. Only for a confirmed new empty disk, format it as ext4 and add its UUID to /etc/fstab with nofail,x-systemd.device-timeout=30.
4. Create /srv/vibetrading-data/runtime_data and /srv/vibetrading-data/nats on that mounted filesystem and grant them to the container UID 10001.
5. Copy compose.env.example to /etc/vibetrading/compose.env, fill the host paths, and create the two key files mode 0600. Do not put keys in Git or .env.
6. Copy maintenance.env.example to the app env file and set the private Ncloud Storage endpoint, bucket, and kr region.
7. Run docker compose config, then docker compose run --rm nats-init, then docker compose run --rm maintenance python main.py archive-check.
8. Install vibetrading.service, enable it, and verify systemctl status vibetrading after a reboot.

The service refuses to start when /srv/vibetrading-data is not a mountpoint, preventing an accidental fallback to the OS disk. Ncloud Storage is private Standard storage; the archive identity needs list/upload/get only and no delete permission.

