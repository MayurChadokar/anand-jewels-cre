import argparse
import getpass
import bcrypt
from database import init_db, SessionLocal
from models import AdminUser, Store

def main():
    parser = argparse.ArgumentParser(description='Manage this independent CRE database')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init-db')
    user = sub.add_parser('create-user')
    user.add_argument('username')
    user.add_argument('--name')
    user.add_argument('--role', choices=['super_admin', 'customer_relation_executive'], default='customer_relation_executive')
    reset = sub.add_parser('reset-password')
    reset.add_argument('username')
    store = sub.add_parser('add-store')
    store.add_argument('name')
    store.add_argument('--city', default='Indore')
    store.add_argument('--address', required=True)
    store.add_argument('--timings', default='Contact store')
    args = parser.parse_args()
    init_db()
    with SessionLocal() as db:
        if args.command in ('create-user', 'reset-password'):
            existing = db.query(AdminUser).filter_by(username=args.username).first()
            if args.command == 'create-user' and existing:
                parser.error('Username already exists; use reset-password to rotate its password.')
            if args.command == 'reset-password' and (not existing or existing.role not in ('super_admin','customer_relation_executive')):
                parser.error('CRE login account not found.')
            password = getpass.getpass('Password: ')
            if len(password) < 10 or len(password.encode('utf-8')) > 72:
                parser.error('Password must have at least 10 characters and at most 72 UTF-8 bytes.')
            if password != getpass.getpass('Confirm password: '):
                parser.error('Passwords do not match.')
            hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            if existing:
                existing.password_hash = hashed
            else:
                db.add(AdminUser(username=args.username, display_name=args.name, role=args.role, password_hash=hashed))
        elif args.command == 'add-store':
            db.add(Store(store_name=args.name, city=args.city, store_address=args.address, timings=args.timings))
        db.commit()
    print('CRE database operation completed.')
if __name__ == '__main__':
    main()
