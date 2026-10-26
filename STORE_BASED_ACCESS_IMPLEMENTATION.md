# Store Based Access Implementation Plan

Status: application implementation complete; final production database migration and store assignment worksheet are pending. This document does not change live access or data by itself.

The CRE application will use a store as its security boundary. A super admin can work across every store. Every CRE executive, manager, salesperson, and customer visit belongs to one store. A store user can view and change only records from the assigned store, even if a different store or visit ID is submitted manually.

## Required outcome

- Super admins see combined dashboard totals, all visits, all reports, and all exports.
- Super admins can filter combined data by store.
- Each CRE executive is assigned to exactly one store and sees only that store.
- A CRE executive creates visits only for the assigned store. The browser does not decide the store.
- Managers and salespeople belong to one store and appear only in that store's forms and filters.
- Products, categories, custom fields, walkout reasons, and competitor options remain global.
- Existing Indore visits retain their IDs, timestamps, customer details, status, staff, and report history.
- Store restrictions apply to reads, writes, reports, exports, summaries, follow-ups, and direct URL requests.

## Final access model

| Capability | Super admin | Store CRE executive |
|---|---|---|
| Dashboard | All stores | Assigned store only |
| Daily summary | All stores or selected store | Assigned store only |
| Create visit | Any selected store | Assigned store only |
| View or search visits | All stores | Assigned store only |
| Edit visit or status | Any store | Assigned store only |
| Reports and walkout register | All stores | Assigned store only |
| Excel export | All stores or selected store | Assigned store only |
| Managers and salespeople | Manage all stores | Manage assigned store staff |
| Products and categories | Manage global values | Manage global values |
| Users and store assignments | Manage | No access |

All authenticated CRE users can manage products, categories, custom fields, walkout reasons, and competitor options. These values are global and changes are visible to every store. Staff management remains store-scoped for CRE executives.

## Store assignment rules

1. `Store.id` is the authorization key. `Store.city` is descriptive and must not be used as the security key.
2. A non-admin login must have one valid `AdminUser.store_id`.
3. A super admin may have `store_id = NULL`; a stored value must not restrict super-admin access.
4. Every active manager and salesperson must have one valid `CreStaffMember.store_id`.
5. Every customer visit must have one valid `CustomerVisit.store_id`.
6. Normal visit editing must not change the visit's store.
7. Cross-store requests must fail on the server, regardless of hidden fields, query parameters, or direct URLs.
8. A manager or salesperson selected for a visit must belong to the visit's store.

If a city receives multiple stores later, the same design continues to work because each login remains assigned to a specific store. Supporting one login across multiple stores would require a separate `user_store_assignments` table and is outside this first implementation.

## Current code baseline

The current schema already contains `AdminUser.store_id`, `CustomerVisit.store_id`, and `Store.city`. `CreStaffMember` does not contain a store assignment. Dashboard and write routes currently query all stores, and the authorization aliases do not distinguish admin from CRE access.

The implementation must update these files:

- `models.py` for staff store ownership and stricter visit ownership.
- `auth.py` for role checks and store-scope helpers.
- `routers/customer_relations.py` for scoped reads and writes.
- `templates/cre_dashboard.html` and `templates/cre_reports.html` for role-aware store controls.
- `templates/cre_staff.html` for staff store assignment.
- `manage.py` for store-aware account and staff administration.
- `tests/test_standalone.py` for access, migration, and data-preservation coverage.
- A new idempotent migration module for the store-scope schema and data migration.

## Database changes

Create an idempotent migration named `store_scope.py` and call it from `database.init_db()` after the existing migrations.

### Admin users

Keep the existing `admin_users.store_id` foreign key.

- `super_admin`: `store_id` may be null.
- `customer_relation_executive`: `store_id` must reference a store before login is allowed.
- Add an index on `admin_users.store_id` if one does not exist.

SQLite cannot add every constraint in place. The application must enforce the role-dependent requirement even if the column remains nullable at the database level.

### Staff members

Add `cre_staff_members.store_id` as a foreign key to `stores.id` and index it.

- Existing staff receive the confirmed Indore store ID during migration.
- New active staff cannot be created without a valid store.
- Staff queries always filter by `store_id` for store users.
- Duplicate names may exist in different stores; validation should reject only duplicates with the same normalized name, staff type, and store.

### Customer visits

Keep the existing indexed `customer_visits.store_id` foreign key and make store ownership mandatory in application writes.

- Preserve every valid existing non-null store assignment.
- Fill a null store only after the Indore store ID is explicitly confirmed.
- Do not mass-update all visits by store name.
- Do not change visit IDs or recreate visit history.
- Normal edit and status forms must never move a visit between stores.

After all rows are reconciled, a later table-rebuild migration may make `customer_visits.store_id` physically non-null. The first rollout should prioritize safe reconciliation and application enforcement.

## Authorization helpers

Add shared helpers instead of repeating store conditions in individual routes.

```python
def is_super_admin(user):
    return user.role == "super_admin"

def required_store_id(user):
    if is_super_admin(user):
        return None
    if not user.store_id:
        raise HTTPException(status_code=403, detail="No store is assigned to this account.")
    return user.store_id

def apply_visit_scope(query, user):
    store_id = required_store_id(user)
    return query if store_id is None else query.filter(CustomerVisit.store_id == store_id)
```

Add equivalent scoped lookup helpers for a single visit and staff member. A missing or cross-store record should return the same not-found response so record IDs are not disclosed.

Authentication must accept only active supported roles. A CRE account without a valid store assignment must not enter the application.

## Route implementation checklist

### Read routes

Apply server-side store scope to all data queries in:

- `/cre/dashboard`
- `/cre/dashboard/summary`
- `/cre/reports`
- `/cre/walkout-register`
- `/cre/reports/export/excel`
- `/cre/walkout-register/export/excel`

The dashboard base query, daily totals, report builder, pagination counts, walkout rows, manager summaries, salesperson summaries, and exports must all receive the same effective store ID.

For super admins, `store_id=0` means all stores and a valid `store_id` filters one store. For store users, ignore or reject a conflicting query parameter and always use `current_user.store_id`.

### Create visit

For a CRE executive:

- Ignore submitted `store_id`.
- Set `visit.store_id = current_user.store_id`.
- Validate that manager and salesperson belong to that store.
- Show the assigned store as read-only text or a disabled control.

For a super admin:

- Require a valid selected store.
- Load managers and salespeople for the selected store.
- Revalidate store and staff relationships on submission.

### Update routes

Use a scoped visit lookup before processing these routes:

- `/cre/customers/{visit_id}/status`
- `/cre/customers/{visit_id}/edit`
- `/cre/reports/walkouts/{visit_id}/followups/{slot}`
- `/cre/reports/walkouts/{visit_id}/edit`
- `/cre/reports/walkouts/{visit_id}/outcome`

Never fetch by `visit_id` alone for a store user. Editing customer details must retain the original `visit.store_id`. Manager and salesperson replacements must belong to that same store.

### Staff routes

Managers and salespeople must be listed and created with a store assignment.

- Super admins can select a store and manage its staff.
- Store executives see only their assigned store's active staff.
- Store executives can create and view staff only for their assigned store.
- Existing inactive staff remain linked for historical visit display.

### Global configuration routes

Products, categories, form fields, walkout reasons, and competitor options remain global. Their management routes are available to every authenticated CRE login, and the interface must make clear that changes affect every store.

## User interface changes

### Super admin

- Add a store filter to the dashboard.
- Keep the reports and exports store filter with an `All stores` option.
- Keep store selection in the Add Walk-in Customer form.
- Filter manager and salesperson choices after selecting a store.
- Show store name in dashboard and report rows.

### Store CRE executive

- Show the assigned store and city in the header or form.
- Remove the `All stores` option.
- Replace the create-form store dropdown with the assigned store label.
- Show only assigned-store managers and salespeople.
- Show People and Configuration navigation. Staff actions remain locked to the assigned store; global configuration changes affect all stores.

UI restrictions are for clarity only. Every restriction must also exist in route validation.

## Administration commands

Extend `manage.py` with non-interactive assignment options while keeping password entry interactive.

```powershell
python manage.py add-store "Store Name" --city "City" --address "Address"
python manage.py create-user cre_indore --name "Indore CRE" --role customer_relation_executive --store-id 1
python manage.py assign-user-store cre_indore --store-id 1
python manage.py add-staff "Manager Name" --type manager --store-id 1
python manage.py add-staff "Sales Name" --type sales_person --store-id 1
python manage.py list-store-assignments
```

Commands must reject unknown stores, prevent assigning a CRE account to no store, and print the resulting assignment without exposing passwords.

## Existing Indore data migration

The reported live total is currently about 512 entries, but migration validation must use the actual count captured immediately before deployment.

### Preflight

1. Put the application into a short maintenance window or stop writes.
2. Create a verified SQLite backup using the SQLite backup API.
3. Record `PRAGMA integrity_check` and `PRAGMA foreign_key_check` results.
4. Record counts for visits, users, staff, walkout records, and follow-ups.
5. Record visit counts grouped by `store_id` and by store name.
6. Identify the Indore store by ID after reviewing its name, city, and address.
7. Stop if multiple possible Indore stores exist or any visit references an unknown store.

### Migration behavior

1. Add the staff store column and indexes.
2. Preserve valid visit store assignments exactly as they are.
3. Assign only null historical visits to the confirmed Indore store.
4. Assign existing Indore managers and salespeople to the confirmed Indore store.
5. Assign each existing non-admin CRE account to a confirmed store.
6. Create missing stores for the other cities without duplicating existing rows.
7. Produce a JSON migration report containing counts and IDs but no customer names, phone numbers, or passwords.
8. Run integrity, foreign-key, orphan, and count checks before commit.

The migration must use one transaction for schema-compatible data changes. It must fail without partial assignment if any required mapping is missing or ambiguous.

### Post-migration reconciliation

- Total visit count equals the preflight count.
- Every visit has a valid store.
- Every active manager and salesperson has a valid store.
- Every non-admin CRE account has a valid store.
- The sum of store-wise visit counts equals the total visit count.
- Existing visit IDs, creation timestamps, status values, walkout records, follow-ups, and custom fields are unchanged.
- Indore totals match the pre-migration baseline after accounting for any previously valid non-Indore assignments.

## Store assignment worksheet

Complete this table before implementation or production migration. Use database store IDs after the stores are confirmed or created.

| Store ID | Store name | City | Existing or new | CRE usernames | Managers | Salespeople |
|---:|---|---|---|---|---|---|
| TBD | TBD | Indore | Existing | TBD | TBD | TBD |
| TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| TBD | TBD | TBD | TBD | TBD | TBD | TBD |

Also confirm:

- Whether any login needs access to more than one store. Default: no.
- Whether store CRE users may add staff. Current implementation: yes, for their assigned store only.
- Whether super admins may transfer a visit to another store. Default: no normal UI transfer.
- Whether products and categories are shared. Default: yes.

## Automated test requirements

### Access isolation

- Indore CRE sees Indore visits and cannot see another store's visits.
- Another store CRE sees only that store's visits.
- Super admin sees the combined total and can filter each store.
- Search, pagination, daily summary, reports, walkout register, and Excel exports remain scoped.
- Changing `store_id` in a URL does not expand a CRE user's access.
- Posting another store's visit ID to edit, status, follow-up, or outcome routes fails.

### Write validation

- A CRE-created visit always uses the login's store, even with a forged form value.
- A manager or salesperson from another store is rejected.
- A normal edit cannot move a visit between stores.
- Super-admin creation requires a valid store and matching staff.
- A CRE login without a store assignment is rejected.

### Migration safety

- Existing valid store assignments are preserved.
- Null historical visits receive only the confirmed Indore store.
- Ambiguous or missing mappings roll back completely.
- Re-running the migration is safe and does not duplicate stores or assignments.
- Counts, foreign keys, and integrity checks pass after migration.

## Production rollout

1. Complete the store assignment worksheet.
2. Implement schema and authorization helpers.
3. Implement scoped reads, exports, and writes.
4. Implement store-aware staff and user administration.
5. Add and pass automated tests.
6. Copy the production database to staging and perform a dry run.
7. Compare staging counts and sample reports with production.
8. Schedule the maintenance window and stop production writes.
9. Create and verify the final backup.
10. Deploy code and run the migration with explicit store mappings.
11. Run reconciliation checks before reopening login access.
12. Test one super-admin login and one login from each store.
13. Keep the backup and migration report according to the business retention policy.

## Rollback

Rollback is required if migration validation fails, counts differ unexpectedly, a login receives the wrong store, or a cross-store access test fails.

1. Keep the application closed to writes.
2. Stop the new server version.
3. Preserve the failed database and migration report for diagnosis.
4. Restore the verified pre-migration database backup.
5. Deploy the previous application version.
6. Re-run integrity and baseline count checks.
7. Reopen access only after the previous behavior is confirmed.

Do not attempt to repair a partially validated production database manually while users are entering data.

## Completion criteria

The implementation is complete only when all four stores are configured, every CRE and active staff member is assigned, all existing visits reconcile, the automated isolation tests pass, and manual login checks confirm that each store sees only its own data while the super admin sees everything.
