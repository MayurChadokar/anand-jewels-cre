from sqlalchemy import Column, Integer, String, Float, DateTime, UniqueConstraint, CheckConstraint, Text, ForeignKey

from sqlalchemy.sql import func

from sqlalchemy.orm import relationship

from database import Base

from enum import Enum

class UserRole(Enum):
    SUPER_ADMIN = "super_admin"
    CONTACT_MANAGER = "contact_manager"
    CUSTOMER_RELATION_EXECUTIVE = "customer_relation_executive"
    CRE_MANAGER = "cre_manager"
    CRE_SALES_PERSON = "cre_sales_person"
    STORE_ADMIN = "store_admin"

class AdminUser(Base):
    __tablename__ = "admin_users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String(100), nullable=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="super_admin")  # Store as string to avoid SQLAlchemy enum issues
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=True)  # Associated store for store_admin
    created_at = Column(DateTime, default=func.now(), nullable=False)
    
    store = relationship("Store", backref="admins")
    
    @property
    def role_enum(self):
        """Get role as UserRole enum"""
        return UserRole(self.role) if self.role else UserRole.SUPER_ADMIN
    
    def set_role(self, role: UserRole):
        """Set role from UserRole enum"""
        self.role = role.value

class Store(Base):
    __tablename__ = "stores"
    
    id = Column(Integer, primary_key=True, index=True)
    store_name = Column(String, nullable=False)
    city = Column(String, nullable=False, default="Indore")
    phone_number = Column(String, nullable=True)
    store_address = Column(Text, nullable=False)
    store_image = Column(String, nullable=True)  # URL/Path to store image
    youtube_link = Column(String, nullable=True)  # YouTube video link
    map_link = Column(String, nullable=True, default="Na")  # Google Maps or location link
    timings = Column(String, nullable=False)  # e.g., "Mon-Sat: 10:00 AM - 8:00 PM, Sun: 11:00 AM - 6:00 PM"
    created_at = Column(DateTime, default=func.now(), nullable=False)
    
    def __repr__(self):
        return f"<Store(name='{self.store_name}', address='{self.store_address}')>"

class CustomerProductType(Base):
    __tablename__ = "customer_product_types"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(80), unique=True, nullable=False, index=True)
    created_by = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

class CustomerFormField(Base):
    __tablename__ = "customer_form_fields"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String(80), unique=True, nullable=False, index=True)
    field_type = Column(String(20), nullable=False, default="text")
    options = Column(Text, nullable=True)
    is_required = Column(Integer, nullable=False, default=0)
    is_active = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class CustomerProductCategory(Base):
    __tablename__ = 'customer_product_categories'
    id = Column(Integer, primary_key=True)
    name = Column(String(80, collation='NOCASE'), unique=True, nullable=False)
    created_by = Column(Integer, ForeignKey('admin_users.id'), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

class CreStaffMember(Base):
    __tablename__ = "cre_staff_members"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, index=True)
    staff_type = Column(String(20), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    is_active = Column(Integer, nullable=False, default=1)

class CreVisitOption(Base):
    __tablename__ = "cre_visit_options"
    __table_args__ = (UniqueConstraint("kind", "name"),)

    id = Column(Integer, primary_key=True)
    kind = Column(String(20), nullable=False, index=True)
    name = Column(String(100, collation="NOCASE"), nullable=False)
    is_active = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey("admin_users.id"), nullable=True)


class CustomerVisit(Base):
    __tablename__ = "customer_visits"

    @property
    def visit_number(self):
        return f'CRE-{self.id:06d}' if self.id is not None else None

    id = Column(Integer, primary_key=True, index=True)
    customer_name = Column(String(120), nullable=False)
    mobile_number = Column(String(24), nullable=False, index=True)
    product_type_id = Column(Integer, ForeignKey("customer_product_types.id"), nullable=False)
    category_id = Column(Integer, ForeignKey('customer_product_categories.id'), nullable=True)
    handled_by_id = Column(Integer, ForeignKey("cre_staff_members.id"), nullable=True, index=True)
    assigned_manager_id = Column(Integer, ForeignKey("cre_staff_members.id"), nullable=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=True, index=True)
    activity_type = Column(String(40), nullable=False, index=True)
    status = Column(String(16), nullable=True, index=True)
    walkout_reason = Column(String(80), nullable=True, index=True)
    competitor_shop = Column(String(100), nullable=True, index=True)
    remark = Column(Text, nullable=True)
    custom_fields = Column(Text, nullable=True, default="{}")
    created_by = Column(Integer, ForeignKey("admin_users.id"), nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    product_type = relationship("CustomerProductType")
    category = relationship('CustomerProductCategory')
    handled_by = relationship("CreStaffMember", foreign_keys=[handled_by_id])
    assigned_manager = relationship("CreStaffMember", foreign_keys=[assigned_manager_id])
    store = relationship("Store", foreign_keys=[store_id])
    creator = relationship("AdminUser", foreign_keys=[created_by])
    walkout_record = relationship("CreWalkoutRecord", uselist=False)
    followups = relationship("CreWalkoutFollowup", order_by="CreWalkoutFollowup.slot")


class CreWalkoutRecord(Base):
    __tablename__ = 'cre_walkout_records'
    visit_id = Column(Integer, ForeignKey('customer_visits.id'), primary_key=True)
    reason = Column(String(80), nullable=True)
    competitor = Column(String(100), nullable=True)
    remark = Column(Text, nullable=True)
    recorded_at = Column(DateTime, default=func.now(), nullable=False)


class CreWalkoutFollowup(Base):
    __tablename__ = 'cre_walkout_followups'
    __table_args__ = (
        UniqueConstraint('visit_id', 'slot'),
        CheckConstraint('slot BETWEEN 1 AND 3'),
    )
    id = Column(Integer, primary_key=True)
    visit_id = Column(Integer, ForeignKey('customer_visits.id'), nullable=False, index=True)
    slot = Column(Integer, nullable=False)
    note = Column(Text, nullable=False)
    created_by = Column(Integer, ForeignKey('admin_users.id'), nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    author = relationship('AdminUser')
