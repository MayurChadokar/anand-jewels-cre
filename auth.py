import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from database import get_db
from models import AdminUser, Store, UserRole
CRE_ROLES = {UserRole.SUPER_ADMIN.value, UserRole.CUSTOMER_RELATION_EXECUTIVE.value}
def is_super_admin(user):
    return bool(user and user.role == UserRole.SUPER_ADMIN.value)
def user_store_id(user):
    return None if is_super_admin(user) else user.store_id
def authenticate_user(db, username, password):
    user = db.query(AdminUser).filter(AdminUser.username == username).first()
    if not user or user.role not in CRE_ROLES:
        return None
    if not is_super_admin(user) and (not user.store_id or not db.get(Store, user.store_id)):
        return None
    try:
        valid = bcrypt.checkpw(password.encode('utf-8'), user.password_hash.encode('utf-8'))
    except ValueError:
        return None
    return user if valid else None
def login_user(request, user):
    request.session.clear()
    request.session.update(user_id=user.id, username=user.username, user_role=user.role)
def logout_user(request):
    request.session.clear()
def get_current_user_web(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get('user_id')
    user = db.get(AdminUser, user_id) if user_id else None
    return user if user and user.role in CRE_ROLES else None
def require_customer_relations_access(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_web(request, db)
    if user is None:
        raise HTTPException(status_code=302, headers={'Location': '/cre/login'})
    if not is_super_admin(user) and (not user.store_id or not db.get(Store, user.store_id)):
        raise HTTPException(status_code=403, detail='No valid store is assigned to this account.')
    return user
def require_super_admin_access(request: Request, db: Session = Depends(get_db)):
    user = require_customer_relations_access(request, db)
    if not is_super_admin(user):
        raise HTTPException(status_code=403, detail='Super administrator access is required.')
    return user
require_cre_manager_access = require_customer_relations_access
require_cre_owner_access = require_customer_relations_access
