"""Copy an updated CRE database into a new validated store-scoped database."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile


def _parse_assignments(values, key_name):
    assignments = {}
    for value in values:
        key, separator, store = value.partition('=')
        if not separator or not key.strip():
            raise ValueError(f'Invalid {key_name} assignment: {value}. Use KEY=STORE_ID.')
        try:
            store_id = int(store)
        except ValueError as error:
            raise ValueError(f'Invalid store ID in {key_name} assignment: {value}.') from error
        assignments[key.strip()] = store_id
    return assignments


def _counts(connection):
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    return {table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in tables}


def migrate(source, destination, existing_store_id, user_assignments=None, staff_assignments=None):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if source == destination:
        raise ValueError('Source and destination must be different databases.')
    if destination.exists():
        raise FileExistsError('Destination exists. Choose a new database path.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    user_assignments = user_assignments or {}
    staff_assignments = staff_assignments or {}
    source_hash_before = hashlib.sha256(source.read_bytes()).hexdigest()

    file_descriptor, pending_name = tempfile.mkstemp(
        prefix='.store-scope-', suffix='.db', dir=str(destination.parent)
    )
    os.close(file_descriptor)
    pending = Path(pending_name)
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
            original_counts = _counts(original)
            with closing(sqlite3.connect(str(pending))) as target:
                original.backup(target)

        with closing(sqlite3.connect(str(pending))) as target:
            target.execute('PRAGMA foreign_keys=ON')
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Source snapshot failed integrity validation.')
            required_tables = {'stores', 'admin_users', 'cre_staff_members', 'customer_visits'}
            available = {row[0] for row in target.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            missing = required_tables - available
            if missing:
                raise ValueError('Missing required tables: ' + ', '.join(sorted(missing)))
            if not target.execute('SELECT 1 FROM stores WHERE id=?', (existing_store_id,)).fetchone():
                raise ValueError('The confirmed existing store ID was not found.')

            target.execute('BEGIN')
            billed_update_trigger = target.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='cre_billed_no_update'"
            ).fetchone()
            if billed_update_trigger:
                target.execute('DROP TRIGGER cre_billed_no_update')
            staff_columns = {row[1] for row in target.execute('PRAGMA table_info(cre_staff_members)')}
            if 'store_id' not in staff_columns:
                target.execute(
                    'ALTER TABLE cre_staff_members ADD COLUMN store_id INTEGER REFERENCES stores(id)'
                )
            target.execute(
                'CREATE INDEX IF NOT EXISTS ix_cre_staff_members_store_id ON cre_staff_members (store_id)'
            )
            target.execute(
                'CREATE INDEX IF NOT EXISTS ix_admin_users_store_id ON admin_users (store_id)'
            )
            target.execute(
                'UPDATE customer_visits SET store_id=? WHERE store_id IS NULL',
                (existing_store_id,),
            )
            target.execute(
                'UPDATE cre_staff_members SET store_id=? WHERE store_id IS NULL',
                (existing_store_id,),
            )
            target.execute(
                "UPDATE admin_users SET store_id=? WHERE role='customer_relation_executive' AND store_id IS NULL",
                (existing_store_id,),
            )
            if billed_update_trigger:
                target.execute(billed_update_trigger[0])

            for username, store_id in user_assignments.items():
                if not target.execute('SELECT 1 FROM stores WHERE id=?', (store_id,)).fetchone():
                    raise ValueError(f'Unknown store ID {store_id} for user {username}.')
                updated = target.execute(
                    "UPDATE admin_users SET store_id=? WHERE username=? AND role='customer_relation_executive'",
                    (store_id, username),
                ).rowcount
                if updated != 1:
                    raise ValueError(f'CRE user assignment not found: {username}.')

            for staff_id_text, store_id in staff_assignments.items():
                try:
                    staff_id = int(staff_id_text)
                except ValueError as error:
                    raise ValueError(f'Invalid staff ID: {staff_id_text}.') from error
                if not target.execute('SELECT 1 FROM stores WHERE id=?', (store_id,)).fetchone():
                    raise ValueError(f'Unknown store ID {store_id} for staff {staff_id}.')
                updated = target.execute(
                    'UPDATE cre_staff_members SET store_id=? WHERE id=?', (store_id, staff_id)
                ).rowcount
                if updated != 1:
                    raise ValueError(f'Staff assignment not found: {staff_id}.')

            checks = {
                'visits_without_store': target.execute(
                    'SELECT count(*) FROM customer_visits WHERE store_id IS NULL'
                ).fetchone()[0],
                'active_staff_without_store': target.execute(
                    'SELECT count(*) FROM cre_staff_members WHERE is_active=1 AND store_id IS NULL'
                ).fetchone()[0],
                'cre_users_without_store': target.execute(
                    "SELECT count(*) FROM admin_users WHERE role='customer_relation_executive' AND store_id IS NULL"
                ).fetchone()[0],
            }
            if any(checks.values()):
                raise ValueError('Store assignment validation failed: ' + json.dumps(checks, sort_keys=True))
            foreign_keys = target.execute('PRAGMA foreign_key_check').fetchall()
            if foreign_keys:
                raise ValueError('Foreign key validation failed.')
            migrated_counts = _counts(target)
            if migrated_counts != original_counts:
                raise ValueError('Table row counts changed during store migration.')
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Migrated database failed integrity validation.')
            store_counts = dict(target.execute(
                'SELECT store_id, count(*) FROM customer_visits GROUP BY store_id ORDER BY store_id'
            ))
            target.commit()

        if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash_before:
            raise ValueError('Source database changed during migration.')
        os.link(str(pending), str(destination))
        report = {
            'migrated_at_utc': datetime.now(timezone.utc).isoformat(),
            'source_access': 'read-only SQLite backup',
            'source_sha256': source_hash_before,
            'existing_store_id': existing_store_id,
            'table_counts': original_counts,
            'visit_counts_by_store': {str(key): value for key, value in store_counts.items()},
            'foreign_keys': 'passed',
            'integrity': 'ok',
        }
        destination.with_suffix('.store-migration.json').write_text(
            json.dumps(report, indent=2), encoding='utf-8'
        )
        return report
    finally:
        pending.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--existing-store-id', type=int, required=True)
    parser.add_argument('--user-store', action='append', default=[], metavar='USERNAME=STORE_ID')
    parser.add_argument('--staff-store', action='append', default=[], metavar='STAFF_ID=STORE_ID')
    args = parser.parse_args()
    try:
        report = migrate(
            args.source,
            args.destination,
            args.existing_store_id,
            _parse_assignments(args.user_store, 'user'),
            _parse_assignments(args.staff_store, 'staff'),
        )
    except (FileExistsError, ValueError, sqlite3.Error) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
