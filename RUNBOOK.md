# Running the ERP at the plant

For whoever installs and looks after the server. It assumes someone who
can use a Linux command line, not a Django developer.

## What runs

One server, four containers, started together by `docker compose`:

| container | what it is | keeps its data in |
|---|---|---|
| `db` | PostgreSQL 16 | the `pgdata` volume |
| `web` | the application (gunicorn, 3 workers) | nothing; it is rebuilt from the code |
| `proxy` | Caddy: HTTPS, and the only thing listening on ports 80/443 | the `caddy_data` volume (certificates) |
| `backup` | a nightly `pg_dump` | `./backups` next to this file |

The database is the whole of the business state. Nothing is uploaded
or stored on disk anywhere else, so a database backup is a complete
backup.

## The server

- Linux (Ubuntu 24.04 LTS is what this was written against), 4 cores,
  8 GB memory, SSD. A plant of this size fits comfortably.
- Docker Engine with the compose plugin: <https://docs.docker.com/engine/install/ubuntu/>.
- A fixed address on the plant network, so the tablets on the floor can
  find it.
- A UPS. PostgreSQL survives a power cut; a disk that dies mid-write
  sometimes does not.

## First installation

```sh
git clone https://github.com/sudiptobiswas24/hello-world.git erp
cd erp
cp .env.example .env
openssl rand -hex 32     # run twice: one for POSTGRES_PASSWORD, one for DJANGO_SECRET_KEY
nano .env                # fill in every line; see below
docker compose up -d --build
docker compose logs -f web   # wait for "Listening at: http://0.0.0.0:8000"
```

The first start builds the database from nothing and takes about five
minutes. Later starts take seconds; after a restore, about twenty.
Until `web` is listening the browser shows **502 Bad Gateway**: that is
Caddy saying the application is not up yet, not a fault. Wait.

The build fetches the base images from Docker Hub, which limits how
often an address may fetch without signing in. If it answers `429 Too
Many Requests`, run `docker login` with a free Docker Hub account and
build again.

Then make the first user, who can make all the others:

```sh
docker compose run --rm web python manage.py createsuperuser
```

### Before people use it

Bring the old system's records in (docs/IMPORT.md: put the files in
`imports/`), then ask whether it is ready:

```sh
docker compose run --rm web python manage.py import_csv parties /app/imports/parties.csv
docker compose run --rm web python manage.py go_live_check
```

`go_live_check` lists what is missing: accounts not set, roles not made,
logins with no role or no employee, a ledger that does not balance,
stock that does not agree with it. It fails while anything would break
the first day. Run it again on the morning of go-live. docs/PILOT.md is
the fortnight before that.

### What goes in `.env`

| setting | what to put |
|---|---|
| `POSTGRES_PASSWORD` | output of `openssl rand -hex 32`. Hex only: it goes inside a URL. |
| `DJANGO_SECRET_KEY` | another `openssl rand -hex 32`. Signs every login. The server refuses to start without one. |
| `ERP_SITE` | what people type in the browser: `erp.deccanpolysacks.in`, or `erp.plant.local`, or `192.168.1.10`. |
| `ERP_TLS` | empty for a public domain name; `tls internal` for a name or address on the plant network. |
| `DJANGO_ALLOWED_HOSTS` | the same name(s) as `ERP_SITE`, comma-separated. Anything else is refused. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | the same, with `https://` in front. Without it, every form submission is refused. |
| `DJANGO_TIME_ZONE` | `Asia/Kolkata`. Shifts start at 08:00 plant time; get this wrong and every shift is read 5½ hours off. |
| `DJANGO_HTTPS` | `true`. See "Plain HTTP" below before changing it. |
| `DJANGO_TRUSTED_PROXIES` | set to `1` by docker-compose.yml for the Caddy in front. Only change it if another proxy is added in front of Caddy (then `2`): the sign-in lock reads each caller's address through them. |
| `BACKUP_AT`, `BACKUP_KEEP_DAYS` | when the nightly backup runs (plant time) and how many days of them to keep. |

### HTTPS on the plant network

Passwords and the operators' PINs cross the network, so it runs over
HTTPS even inside the plant.

- **A public domain name** pointed at the server, with ports 80 and 443
  reachable from the internet: leave `ERP_TLS` empty and Caddy gets and
  renews a real certificate by itself.
- **A name or address only on the plant network**: set
  `ERP_TLS=tls internal`. Caddy makes its own certificate authority.
  Each PC and tablet must trust it once, or the browser will warn on every
  visit:

  ```sh
  docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./plant-root.crt
  ```

  Install `plant-root.crt` as a trusted root certificate on each device
  (Windows: double-click, "Install certificate", "Local machine",
  "Trusted Root Certification Authorities". Android: Settings, Security,
  "Install a certificate", "CA certificate").

### Plain HTTP

`DJANGO_HTTPS=false` makes the application work without HTTPS. Use it
only if installing the certificate on every device is truly not
possible, and know what it means: anyone on the plant network can read
every password and PIN as it is typed. It is never the default.

## Users and what they may do

On every start the `web` container refreshes the role groups defined
in `apps/core/management/commands/setup_roles.py`. Give each person a login
in the admin (`/admin/`, "Users") and put them in the groups that match
their job. The roles keep apart the people who prepare a document and
the people who post it; that separation is the point, so resist giving
everyone everything.

| role | for | cannot |
|---|---|---|
| Production Supervisor | work orders, issues, production, downtime, crews, maintenance jobs | change specifications, bills, routings or costing; see pay or the ledger |
| Station | the shared login on a station tablet | anything but the station |
| Production Planner | forecasts, MRP, planned orders, releasing work orders, changeover rules | record production; see pay or the ledger |
| Quality Inspector | inspections, readings, calibrations, coating checks, test certificates, incoming inspection | set the plans and limits the readings are judged against |
| Quality Manager | all of quality, complaints and corrective actions, third-party releases | |
| GST Officer | GSTR-1, GSTR-3B, ITC-04, e-invoice and e-way bill payloads | book or change an invoice or a bill |
| Bookkeeper, Controller | the ledger; the Controller posts and closes | |
| Sales Rep, AR Manager | orders and invoices; the AR Manager posts and collects | |
| Purchasing Clerk, AP Manager | orders and bills; the AP Manager approves, posts and pays | |
| Warehouse Staff | receipts and deliveries, naming each new batch as it comes in | |
| HR Admin | employees and leave | |
| Payroll Officer | pay components (PF, ESI, PT rules), what each person is paid, working out each month's payroll | post it to the ledger or pay it: the Controller does both, and pays PF, ESI and tax over |
| Employee Self Service | leave requests and purchase requisitions | read anyone's pay, or the ledger. They do see everyone's leave requests: limiting a person to their own is not built yet |

A login alone reads nothing: every screen needs the role that shows it.

Floor operators do not need logins of their own. A station tablet is
logged in once with a user in the Station role, and each operator then
identifies themselves at the station screen (`/station/<station code>/`)
with their employee PIN.

## Email

Invoices, quotations, customer statements, payment reminders, delivery
challans, purchase orders and job-work challans go by email, and each
record's page lists what went out and to whom. Until a mail server is named, sending is refused with a message
saying so, and nothing is recorded as sent. To turn it on, add to
`.env` and run `docker compose up -d`:

```
EMAIL_HOST=smtp.gmail.com        # or the company's mail provider
EMAIL_PORT=587
EMAIL_HOST_USER=accounts@deccanpolysacks.in
EMAIL_HOST_PASSWORD=<an app password, not the mailbox password>
DEFAULT_FROM_EMAIL=accounts@deccanpolysacks.in
```

## Morning checks

Nothing in the application runs by itself. Each login's home page shows
what fell due for them (maintenance and calibration due, licences to
renew, bills past their MSME days, attendance unmarked, deliveries not
signed for, freight unbilled, invoices and bills past due, drafts nobody
posted, stock under its reorder level, meters unread, complaint actions
overdue). To have the same mailed each morning, give the server one cron
line, at six:

```
0 6 * * * cd /srv/erp && docker compose run --rm web python manage.py morning_checks
```

One mail a person with something waiting and an address; nobody else
hears. Without a mail server (above) the command prints the lines
instead, and says so. `--dry-run` prints without sending.

## Backups

Every night at `BACKUP_AT` the `backup` container writes
`backups/erp_YYYY-MM-DD_HHMM.dump`, and deletes those older than
`BACKUP_KEEP_DAYS`. Check that it is working:

```sh
ls -lh backups/
docker compose logs backup | tail
```

For a backup right now (before an update, before an import):

```sh
docker compose run --rm backup now
```

Files attached to records (a PO copy, an LR scan) are not in the
database: they live in the `media` volume. Back it up with the dumps
(`docker compose cp web:/app/media backups/media`, or copy the volume
with the same tool that copies `backups/`); a restore without it leaves
every record saying "attached" with nothing to open.

**These backups sit on the same disk as the database.** They protect
against a bad import or a mistaken deletion. They do not protect against
the disk failing, fire or theft. Copy `backups/` off the machine every
day: to another computer, a NAS, or cloud storage with `rclone`. Which one
is the plant's choice. Having none is the one choice that is wrong.

### Restoring

Restoring replaces everything with the backup, and everything entered
since then is lost.

```sh
docker compose stop web
docker compose run --rm backup restore /backups/erp_2026-10-03_0200.dump
docker compose start web
```

It asks for `yes` before it touches anything, and refuses a file that is
not a readable backup.

### Proving a backup restores

A backup that has never been restored is a hope, not a backup. Once a
month, restore the latest into a scratch database and look at it:

```sh
docker compose exec db createdb -U erp erp_check
docker compose exec -T db pg_restore -U erp -d erp_check --no-owner < backups/<newest file>.dump
docker compose exec db psql -U erp -d erp_check -c "select count(*) from accounting_journalentry"
docker compose exec db dropdb -U erp erp_check
```

## Updating to a new version

```sh
docker compose run --rm backup now
git pull
docker compose up -d --build
docker compose logs -f web
```

Database changes apply by themselves when `web` starts. If anything
looks wrong afterwards, restore the backup you just took, check out the
previous version (`git checkout <previous tag>`), and rebuild.

## Day to day

| to | run |
|---|---|
| see whether everything is up | `docker compose ps` |
| read the application's log | `docker compose logs --tail 200 web` |
| restart the application | `docker compose restart web` |
| run a command (MRP, an import) | `docker compose run --rm web python manage.py <command>` |
| open a database prompt | `docker compose exec db psql -U erp` |

## What it does not do

- It sends no email until a mail server is set up (see "Email").
- It files nothing with the GST portal. GSTR-1, GSTR-3B and ITC-04 are
  prepared for review and filing by whoever files today. E-invoice and
  e-way bill payloads are produced for upload; the system does not
  connect to the IRP or the e-way bill portal.
- It takes advances (down payments from customers, prepayments to
  vendors) without GST. That is right for goods. An advance for a
  service, such as job work, owes GST when it is received; until that
  is built, raise the tax on a separate invoice.

## Security notes

`python manage.py check --deploy` reports two warnings, both left on
purpose:

- `SECURE_HSTS_INCLUDE_SUBDOMAINS` is off. Turning it on would force
  HTTPS on every other site under the company's domain, which may not
  all have it.
- `SECURE_HSTS_PRELOAD` is off. Preloading is very hard to undo.
