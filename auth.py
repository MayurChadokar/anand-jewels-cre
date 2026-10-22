import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from database import get_db
from models import AdminUser, UserRole
CRE_ROLES = {UserRole.SUPER_ADMIN.value, UserRole.CUSTOMER_RELATION_EXECUTIVE.value}
def authenticate_user(db, username, password):
    user = db.query(AdminUser).filter(AdminUser.username == username).first()
    if not user or user.role not in CRE_ROLES:
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
async def get_current_user_web(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get('user_id')
    user = db.get(AdminUser, user_id) if user_id else None
    return user if user and user.role in CRE_ROLES else None
async def require_customer_relations_access(request: Request, db: Session = Depends(get_db)):
    user = await get_current_user_web(request, db)
    if user is None:
        raise HTTPException(status_code=302, headers={'Location': '/cre/login'})
    return user
require_cre_manager_access = require_customer_relations_access
require_cre_owner_access = require_customer_relations_access
