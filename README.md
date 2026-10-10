# Procurement Module (Django · Vercel · Neon · Vercel Blob)

PR → DoA approval → RFQ → vendor quotations (DB record + document in Blob) → audit trail.
Notifications are intentionally **not** included.

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DEBUG=1                      # SQLite + local-disk file storage when no env vars are set
python manage.py migrate
python manage.py seed_demo          # departments, categories, vendors, DoA rules, demo users
python manage.py runserver
python manage.py test apps          # 11 tests
```

## Sign-in
| User | Password | Role |
|---|---|---|
| `admin` | `Admin@12345` | Admin — **hard-coded** in `config/settings.py` (`HARDCODED_ADMIN`). Change before go-live. |
| `specialist1`, `manager1`, `cpo1` | `Demo@12345` | created by `seed_demo` (demo only) |

The Admin signs in via the same login page, then uses **Users & roles** to create users and set
their role / department / optional personal approval limit. **Configuration** opens the Django
admin for departments, categories, vendors, **DoA rules** (with overlap validation) and the audit log.
The Admin is a configuration role and cannot approve PRs.

## Deploy (Vercel + Neon + Blob)
1. Neon: create a project; copy the **pooled** string to `DATABASE_URL` and the **direct** string to `DATABASE_URL_UNPOOLED`.
2. Vercel: create a **private** Blob store and connect it to the project (sets `BLOB_READ_WRITE_TOKEN`).
3. Set `SECRET_KEY`, `ALLOWED_HOSTS`, `CRON_SECRET` (see `.env.example`).
4. Migrate from your machine/CI against the direct connection:
   `USE_DIRECT_DB=1 DATABASE_URL_UNPOOLED=... python manage.py migrate`
5. `vercel deploy`. Static files are served by WhiteNoise from app directories (no `collectstatic` step).
6. **Run `python manage.py blob_smoketest` once with the real token.** The Blob REST call is isolated in
   `apps/documents/storage.py::VercelBlobBackend`; its header/query names could not be verified offline.
   If the smoke test fails, adjust that one class.

## Layout
```
config/                 settings, urls, wsgi
api/index.py            Vercel entrypoint
apps/core/              base models, race-safe document numbering, form/template helpers
apps/audit/             append-only AuditLog + record() (captures IP via middleware)
apps/accounts/          User(role), hard-coded admin backend, role guards, user/role management UI
apps/masterdata/        Department, Category, Vendor
apps/doa/               DoARule/Step models, DoAResolver, seed_demo command
apps/requisitions/      PR models, policies (who can see/decide), services (state machine), views
apps/sourcing/          RFQ + Quotation models, services, views, cron endpoint
apps/documents/         Document model, storage backends (Vercel Blob / local), owner registry, views
templates/              Bootstrap 5 templates
```
Rules: views are thin; **all state changes go through `services.py`**; read/permission rules live in `policies.py`.

## Behaviour notes / assumptions
* Single currency (no FX). Add a currency + rate table later if needed.
* PR statuses: Draft → In approval → Approved → RFQ created (or Returned / Rejected / Cancelled). "Submitted" is folded into "In approval" because the DoA chain is resolved at submit.
* A PR gets one RFQ by default; set `ALLOW_MULTIPLE_RFQ_PER_PR=1` to allow several.
* Re-quoting by the same vendor creates a new revision and marks the old one *Superseded*. Quotes after the deadline are flagged *Late*.
* Hourly Vercel Cron (`/internal/cron/close-rfqs`) closes expired RFQs; it needs `CRON_SECRET`.
* **Upload limit ≈ 4 MB** because files pass through Django and Vercel caps request bodies at ~4.5 MB.
  For bigger files, switch to browser-direct Blob uploads (token endpoint + `@vercel/blob/client`); `documents/services.py` already separates "store bytes" from "record metadata".
* Not built yet: delegation of authority during absence, quotation comparison/award, PO, vendor portal, malware scanning.
