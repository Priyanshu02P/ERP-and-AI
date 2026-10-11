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
python manage.py test apps          # 38 tests
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
0. Python is pinned to 3.12 via `.python-version`; the Postgres driver is `psycopg2-binary` (wheels exist for all common platforms).
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
apps/evaluation/        Bid comparison + weighted evaluation (models, scoring.py, comparison.py, services, views)
apps/purchasing/         POs: direct/award/renewal/recurring (models, schedule.py, services, policies, views, cron)
apps/documents/         Document model, storage backends (Vercel Blob / local), owner registry, views
templates/              Bootstrap 5 templates
```
Rules: views are thin; **all state changes go through `services.py`**; read/permission rules live in `policies.py`.

## Bid comparison & evaluation
Open an RFQ, then **Compare & evaluate bids** (`/evaluation/rfq/<id>/compare/`).

* **Quotation capture** now records the terms needed to compare bids: discount, freight/insurance, non-recoverable taxes, setup/tooling, Incoterms, MOQ, validity, warranty, payment terms, vendor claims (specs / certificates / capacity, tri-state: *not stated* is never treated as *yes*) and exceptions to RFQ terms.
* **Bid Comparison page** (read-only, available as soon as there is a quote): side-by-side matrix with **total landed cost** (lines - discount + freight + taxes + setup), % above lowest comparable bid, terms, claims, documents, and a unit-price-by-line grid. Investigation flags (triggers, not proof): `LOW_BID` (< 70% of median, needs 3+ bids), `INCOMPLETE`, `LATE`, `EXPIRED`, `ABOVE_ESTIMATE` (> 10% over PR estimate), `DEVIATIONS`, `UNCONFIRMED`, `SINGLE_BID`. Thresholds: `LOW_BID_RATIO`, `ABOVE_ESTIMATE_TOLERANCE` env vars.
* **Evaluation page** (starts only after the RFQ is *closed for responses*):
  1. *Mandatory gates* (specs, certificates, capacity) confirmed pass/fail by the evaluator; failed, unassessed, incomplete-pricing or expired bids are ineligible and are not ranked.
  2. *Evaluated cost* = landed cost + documented adjustments (inspection, expected defects, financing ...).
  3. *Weighted scorecard* on eligible bids: cost score = 100 x lowest eligible evaluated cost / bidder's evaluated cost; five criteria scored 0-100 by the evaluator; weights editable per evaluation (default 30/25/20/10/10/5, must total 100).
  4. Recommendation + rationale (+ evidence attachments) -> **submit** -> a *different* Manager/CPO **finalises or returns** it (four-eyes). Finalising **freezes the results** in a snapshot, so later data changes cannot alter a signed-off evaluation.
* Quality / delivery / risk scores are entered by the evaluator with evidence. Historical supplier KPIs (defect rate, OTD, compliance trends) are **not** imported yet; wire them in as score suggestions once PO/receipt history lives in this system.
* Not built: the Supplier Performance dashboard for Manager/CPO.

## Purchase orders (`/purchasing/`)
A PO reaches the system by one of four routes; `basis` records which, and every PO (except auto-generated releases) is approved through **DoA rules with doc_type = PO**, so the same Admin-maintained rules engine governs PRs and POs.

| Route | RFQ needed? | Rule |
|---|---|---|
| **RFQ award** | yes (done) | *Create purchase order* on a finalised evaluation: vendor, prices, payment terms and net charges are copied from the recommended quotation and are not editable. The PO's approval is the award approval. |
| **Direct, below threshold** | no | Whole-term value, plus recent direct POs to the **same vendor and category** within the window, is within the threshold. A business justification is required. |
| **Direct, with approval (exemption)** | no | Above the threshold: record an exemption reason; the CPO must approve. If the DoA chain already has a CPO level, that level also grants the exemption; otherwise a CPO level is appended. |
| **Renewal** | no | *Renew* copies vendor, lines and term length to a draft with new dates. RFQ-exempt unless the whole-term value rises more than the tolerance, which triggers the same exemption approval. |

* **Thresholds are data**: Configuration -> *Purchasing policy* (direct-buy threshold, anti-splitting window days, renewal tolerance %, renewals look-ahead days). Defaults: 5,000 / 30 / 5% / 60.
* **Anti-splitting**: three 3,000 orders to one vendor and category inside the window are not three "below threshold" buys; the one that crosses the threshold needs the exemption.
* **Recurring purchases**: tick *Recurring*, choose weekly / monthly / quarterly / yearly plus start and end dates. The agreement is approved **once for its whole-term commitment** (per-release amount x number of releases; that is the value tested against the threshold and the DoA, so a "small" monthly order cannot dodge control). After *Activate agreement*, the daily cron (`/internal/cron/recurring-pos`, or `python manage.py generate_recurring_pos`) creates a pre-authorised, already-issued **release PO** for each due date. Generation is idempotent, catches up missed days (max 12 per run), month-end dates do not drift, and the agreement closes itself after its last release. Pause / Resume (occurrences due while paused are skipped, not back-ordered) and End early are available.
* **Renewals due** page lists agreements/POs ending within the look-ahead window (or in the last 90 days) without a renewal. Nothing renews automatically; there are no notifications by design, so this page is the reminder.
* Issuing a PO closes its source PR. Approved/issued POs can be cancelled only by a Manager/CPO with a reason. A printable PO document is at `/purchasing/<id>/print/`.
* Not built yet: goods receipt / invoice matching, partial releases of one PO, splitting an award across vendors.

## Behaviour notes / assumptions
* Single currency (no FX). Add a currency + rate table later if needed.
* PR statuses: Draft → In approval → Approved → RFQ created (or Returned / Rejected / Cancelled). "Submitted" is folded into "In approval" because the DoA chain is resolved at submit.
* A PR gets one RFQ by default; set `ALLOW_MULTIPLE_RFQ_PER_PR=1` to allow several.
* Re-quoting by the same vendor creates a new revision and marks the old one *Superseded*. Quotes after the deadline are flagged *Late*.
* Hourly Vercel Cron (`/internal/cron/close-rfqs`) closes expired RFQs; it needs `CRON_SECRET`.
* **Upload limit ≈ 4 MB** because files pass through Django and Vercel caps request bodies at ~4.5 MB.
  For bigger files, switch to browser-direct Blob uploads (token endpoint + `@vercel/blob/client`); `documents/services.py` already separates "store bytes" from "record metadata".
* Not built yet: delegation of authority during absence, quotation comparison/award, PO, vendor portal, malware scanning.
