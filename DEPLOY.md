# Starting fresh & deploying

## 1. Do I need to create tables manually in SQLite?

**No.** The app creates the entire schema itself on every startup
(`database.init_db()` runs `CREATE TABLE IF NOT EXISTS ...` for all 16 tables and
applies any column migrations). You never open the `sqlite3` shell or run DDL by
hand. The database file (`allocations.db`) is created automatically the first
time the app runs.

### Start a brand-new, empty system (your own data only)

**This is the default.** Just run the app on a fresh machine — no demo data is
loaded:

```bash
streamlit run app.py
```

With no `allocations.db` yet this creates all tables **empty**. You then enter
your real data through the **Setup** screens (Roles → Managers → Resources →
Projects → Holidays), promote projects to `READY_TO_USE`, and start allocating.

**Loading the demo dataset (optional).** Sample data is opt-in:

```bash
RA_SEED_SAMPLE_DATA=1 streamlit run app.py   # load demo data if the DB is empty
# or, explicitly:
python reset.py --sample                      # wipe + load the demo dataset
```

**The reset helper:**

```bash
python reset.py --empty     # wipe + create empty tables now (no demo data)
python reset.py --sample    # wipe + load the demo dataset
python reset.py             # just wipe; next start creates empty tables
```

`reset.py` always backs up the current database to `backups/` before wiping.

---

## 2. Deploying to AWS

This app is **Streamlit + a local SQLite file**. SQLite is a single file on a
local disk, so the right shape on AWS is **one small always-on instance with a
persistent disk** — not a stateless/serverless or multi-instance setup (those
would lose or corrupt the file). For 2–3 users that is plenty.

Recommended: **a single EC2 instance** (or AWS Lightsail, which is the same idea
with a simpler console). Below is the EC2 path.

### A. Launch the instance
1. EC2 → Launch instance. Amazon Linux 2023 (or Ubuntu 22.04), type **t3.small**.
2. Create/attach a key pair so you can SSH in.
3. Security group inbound rules:
   - SSH (22) from *your* IP.
   - HTTP (80) and HTTPS (443) from anywhere **if** you use the Nginx step below;
     otherwise open **8501** (Streamlit's default port) from your office IP.
4. Keep the default 8–20 GB EBS volume — your `allocations.db` and `backups/`
   live here and persist across restarts.

### B. Install and copy the app
SSH in, then:

```bash
sudo dnf install -y python3 python3-pip git         # Ubuntu: sudo apt update && sudo apt install -y python3-pip git
git clone <your-repo-url> resource_allocator        # or scp the folder up
cd resource_allocator
pip3 install -r requirements.txt
```

Quick smoke test (Ctrl-C to stop):

```bash
RA_SEED_SAMPLE_DATA=0 python3 -m streamlit run app.py --server.port 8501 --server.address 0.0.0.0
```

Visit `http://<EC2-public-IP>:8501`.

### C. Run it as a service (auto-start, auto-restart)
Create `/etc/systemd/system/resource-allocator.service`:

```ini
[Unit]
Description=Resource Allocation Manager (Streamlit)
After=network.target

[Service]
User=ec2-user
WorkingDirectory=/home/ec2-user/resource_allocator
Environment=RA_SEED_SAMPLE_DATA=0
ExecStart=/usr/bin/python3 -m streamlit run app.py \
  --server.port 8501 --server.address 0.0.0.0 \
  --server.headless true --browser.gatherUsageStats false
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now resource-allocator
sudo systemctl status resource-allocator      # check it's running
```

(On Ubuntu set `User=ubuntu` and the matching `/home/ubuntu/...` path.)

### D. (Recommended) Put it behind Nginx + HTTPS
So users hit `https://your-domain` on 443 instead of `:8501`:

```bash
sudo dnf install -y nginx        # Ubuntu: sudo apt install -y nginx
```

`/etc/nginx/conf.d/allocator.conf`:

```nginx
server {
    listen 80;
    server_name your-domain.com;
    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;     # required: Streamlit uses websockets
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
}
```

```bash
sudo systemctl enable --now nginx
# Free TLS cert:
sudo dnf install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

Now close port 8501 in the security group and keep only 80/443.

### E. Protect the data (backups off the box)
The app already writes timestamped copies to `backups/` on every start and on
demand from the **Settings** page. Push those to S3 so they survive the
instance:

```bash
aws s3 sync /home/ec2-user/resource_allocator/backups s3://YOUR-BUCKET/allocator-backups/
```

Add that line to a cron job (e.g. hourly) via `crontab -e`. Also consider EBS
snapshots for the whole volume.

### Notes / alternatives
- **Concurrency:** WAL mode handles your 2–3 users fine on one instance. Do not
  run multiple instances against the same SQLite file.
- **AWS Lightsail:** same single-instance model, friendlier console + fixed
  monthly price — good choice if you don't need full EC2.
- **ECS Fargate / containers:** possible, but you must mount **EFS** for the DB
  (and still only one task), otherwise the file is ephemeral. More moving parts
  for no real benefit at this scale.
- **Streamlit Community Cloud:** easiest to publish, but storage is ephemeral and
  the app is public — not suitable as your system of record.
- **Outgrowing SQLite?** If you later need many concurrent writers or HA, migrate
  the data layer in `database.py` to Amazon RDS (Postgres). The rest of the app
  is unaffected because all SQL goes through that one module.
```
