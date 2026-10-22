"""One-way snapshot migration. The source is opened read-only, never imported as code."""
import argparse
from contextlib import closing
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine
from database import Base
import models
TABLES = ('stores', 'admin_users', 'customer_product_types', 'customer_form_fields', 'cre_staff_members', 'customer_visits')
CRE_ROLES = ('super_admin', 'customer_relation_executive')
def digest(rows):
    return hashlib.sha256(json.dumps([dict(row) for row in rows],sort_keys=True,ensure_ascii=False,default=str).encode('utf-8')).hexdigest()
def migrate(source, destination):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if destination == source:
        raise ValueError('Source and destination must be different databases.')
    if destination.exists():
        raise FileExistsError('Destination exists. Migration never overwrites a database; choose a new path.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cre-migration-') as work:
        snapshot_path = Path(work) / 'snapshot.db'
        # SQLite backup includes committed WAL contents and gives a consistent snapshot.
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(str(snapshot_path))) as snapshot:
                original.backup(snapshot)
        with closing(sqlite3.connect(str(snapshot_path))) as snapshot:
            snapshot.row_factory = sqlite3.Row
            available = {row[0] for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = set(TABLES) - available
            if missing:
                raise ValueError('Missing source tables: ' + ', '.join(sorted(missing)))
            rows = {table:list(snapshot.execute('SELECT * FROM "' + table + '" ORDER BY id')) for table in TABLES if table != 'admin_users'}
            referenced = {row['created_by'] for table in ('customer_product_types','customer_form_fields','cre_staff_members','customer_visits') for row in rows[table] if row['created_by'] is not None}
            rows['admin_users'] = [row for row in snapshot.execute('SELECT * FROM admin_users ORDER BY id') if row['role'] in CRE_ROLES or row['id'] in referenced]
            if not any(row['role'] in CRE_ROLES for row in rows['admin_users']):
                raise ValueError('No CRE login accounts found in source. Create a fresh CRE database instead.')
        fd, pending_name = tempfile.mkstemp(prefix='.cre-import-',suffix='.db',dir=str(destination.parent))
        os.close(fd)
        pending=Path(pending_name)
        db_engine = create_engine('sqlite:///' + pending.as_posix())
        try:
            Base.metadata.create_all(db_engine)
            db_engine.dispose()
            counts={}
            hashes={}
            with closing(sqlite3.connect(str(pending))) as target:
                target.row_factory=sqlite3.Row
                target.execute('PRAGMA foreign_keys=ON')
                target.execute('BEGIN')
                for table in TABLES:
                    target_columns = {row['name'] for row in target.execute('PRAGMA table_info("'+table+'")')}
                    source_columns = set(rows[table][0].keys()) if rows[table] else set()
                    unknown = source_columns - target_columns
                    if unknown:
                        raise ValueError('Unsupported source columns in '+table+': '+', '.join(sorted(unknown)))
                    if rows[table]:
                        columns=list(rows[table][0].keys())
                        sql='INSERT INTO "'+table+'" ('+','.join('"'+col+'"' for col in columns)+') VALUES ('+','.join('?' for _ in columns)+')'
                        target.executemany(sql,[tuple(row[col] for col in columns) for row in rows[table]])
                        copied=list(target.execute('SELECT '+','.join('"'+col+'"' for col in columns)+' FROM "'+table+'" ORDER BY id'))
                        if digest(copied) != digest(rows[table]):
                            raise ValueError('Data comparison failed for '+table)
                    counts[table]=len(rows[table])
                    hashes[table]=digest(rows[table])
                if target.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('Foreign key validation failed.')
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Database integrity check failed.')
                target.commit()
            # Atomic publication without overwriting any existing target.
            os.link(str(pending), str(destination))
            report={'migrated_at_utc':datetime.now(timezone.utc).isoformat(), 'tables':counts,
                    'row_checksums_sha256':hashes, 'foreign_keys':'passed', 'integrity':'ok',
                    'source_access':'read-only SQLite backup', 'mode':'one-time independent copy'}
            destination.with_suffix('.migration.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            return report
        finally:
            db_engine.dispose()
            pending.unlink(missing_ok=True)
def main():
    from settings import DATA_DIR
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--destination',type=Path,default=DATA_DIR/'cre.db')
    args=parser.parse_args()
    try:
        result=migrate(args.source,args.destination)
    except (ValueError,FileExistsError,sqlite3.Error) as error:
        parser.exit(1,str(error)+'\n')
    print(json.dumps(result,indent=2))
if __name__ == '__main__': main()
