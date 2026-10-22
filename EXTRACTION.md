# CRE extraction record

Prepared 22 October 2026. Destination: `C:\syscraft\anand\anand-cre`.

The original backend source was only read. Existing source file hashes were
compared after extraction: no changes made by this extraction. The original
database was opened in read-only mode through SQLite backup; its application
and database initialization code were never imported or run by migration.
The existing backend remains on port 8000; the new CRE uses port 8001.

## Migrated snapshot

| Table | Rows |
|---|---:|
| stores | 4 |
| admin_users (CRE logins and required identities) | 4 |
| customer_product_types | 1 |
| customer_form_fields | 2 |
| cre_staff_members | 2 |
| customer_visits | 6 |

Source/target row checksums matched. Foreign key and integrity checks passed.
The machine-readable record is `data/cre.migration.json`. The migrated database
is `data/cre.db`; no test data was inserted into it. Password hashes were retained.
No automatic synchronization or traffic cutover has been configured.

## Validation

Nine automated tests passed using the independent virtual environment. Tests
cover consistent WAL snapshots; no overwrite; rejected broken relationships;
rejected unknown schema; login/roles/cookie/logout; all CRE screens and static
assets; customer creation/edit/status/search/report/Excel; required-field and
relationship validation; staff/product management and field restoration.
Tests use disposable synthetic databases and shut down their temporary server.
Dependency check: no broken requirements.

## Included administration

Use `manage.py` to create CRE login users, reset their passwords and add local
stores. Existing migrated CRE/super-admin credentials work independently. Main
backend accounts and stores do not automatically synchronize with CRE.
