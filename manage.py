import argparse
import getpass
import bcrypt
from database import init_db, SessionLocal
from models import AdminUser, CreStaffMember, Store

def main():
    parser = argparse.ArgumentParser(description='Manage this independent CRE database')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init-db')
    user = sub.add_parser('create-user')
    user.add_argument('username')
    user.add_argument('--name')
    user.add_argument('--role', choices=['super_admin', 'customer_relation_executive'], default='customer_relation_executive')
    user.add_argument('--store-id', type=int)
    reset = sub.add_parser('reset-password')
    reset.add_argument('username')
    store = sub.add_parser('add-store')
    store.add_argument('name')
    store.add_argument('--city', default='Indore')
    store.add_argument('--address', required=True)
    store.add_argument('--timings', default='Contact store')
    assign = sub.add_parser('assign-user-store')
    assign.add_argument('username')
    assign.add_argument('--store-id', type=int, required=True)
    staff = sub.add_parser('add-staff')
    staff.add_argument('name')
    staff.add_argument('--type', choices=['manager', 'sales_person'], required=True)
    staff.add_argument('--store-id', type=int, required=True)
    sub.add_parser('list-store-assignments')
    args = parser.parse_args()
    init_db()
    with SessionLocal() as db:
        if args.command in ('create-user', 'reset-password'):
            existing = db.query(AdminUser).filter_by(username=args.username).first()
            if args.command == 'create-user' and existing:
                parser.error('Username already exists; use reset-password to rotate its password.')
            if args.command == 'reset-password' and (not existing or existing.role not in ('super_admin','customer_relation_executive')):
                parser.error('CRE login account not found.')
            selected_store = db.get(Store, args.store_id) if args.command == 'create-user' and args.store_id else None
            if args.command == 'create-user' and args.role == 'customer_relation_executive' and not selected_store:
                parser.error('A valid --store-id is required for a CRE executive.')
            password = getpass.getpass('Password: ')
            if len(password) < 10 or len(password.encode('utf-8')) > 72:
                parser.error('Password must have at least 10 characters and at most 72 UTF-8 bytes.')
            if password != getpass.getpass('Confirm password: '):
                parser.error('Passwords do not match.')
            hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            if existing:
                existing.password_hash = hashed
            else:
                db.add(AdminUser(
                    username=args.username, display_name=args.name, role=args.role,
                    store_id=selected_store.id if selected_store else None, password_hash=hashed,
                ))
        elif args.command == 'add-store':
            db.add(Store(store_name=args.name, city=args.city, store_address=args.address, timings=args.timings))
        elif args.command == 'assign-user-store':
            existing = db.query(AdminUser).filter_by(username=args.username).first()
            selected_store = db.get(Store, args.store_id)
            if not existing or existing.role != 'customer_relation_executive':
                parser.error('CRE executive account not found.')
            if not selected_store:
                parser.error('Store not found.')
            existing.store_id = selected_store.id
        elif args.command == 'add-staff':
            selected_store = db.get(Store, args.store_id)
            name = ' '.join(args.name.split())
            if not selected_store:
                parser.error('Store not found.')
            if not name:
                parser.error('Staff name is required.')
            duplicate = db.query(CreStaffMember).filter(
                CreStaffMember.store_id == selected_store.id,
                CreStaffMember.staff_type == args.type,
                CreStaffMember.name.ilike(name),
            ).first()
            if duplicate:
                parser.error('That staff member already exists for this store.')
            db.add(CreStaffMember(
                name=name[:100], staff_type=args.type, store_id=selected_store.id
            ))
        elif args.command == 'list-store-assignments':
            print('Users:')
            for item in db.query(AdminUser).order_by(AdminUser.username):
                print(f'  {item.username}: {item.role} -> {item.store.store_name if item.store else "All stores / unassigned"}')
            print('Staff:')
            for item in db.query(CreStaffMember).order_by(CreStaffMember.staff_type, CreStaffMember.name):
                print(f'  {item.staff_type} {item.name} -> {item.store.store_name if item.store else "Unassigned"}')
        db.commit()
    print('CRE database operation completed.')
if __name__ == '__main__':
    main()
