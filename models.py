from sqlalchemy import Boolean, Column, Integer, String, Float, DateTime, UniqueConstraint, CheckConstraint, Text, ForeignKey

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
    visit_prefix = Column(String(8), nullable=True)
    next_visit_number = Column(Integer, nullable=True, default=1)
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

class CustomerVisitStatus(Base):
    __tablename__ = "customer_visit_statuses"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(40, collation="NOCASE"), unique=True, nullable=False, index=True)
    created_by = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

class CreStaffMember(Base):
    __tablename__ = "cre_staff_members"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, index=True)
    staff_type = Column(String(20), nullable=False, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    is_active = Column(Integer, nullable=False, default=1)

    store = relationship("Store")

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
        if self.store_visit_number is None:
            return f'CRE-{self.id:06d}' if self.id is not None else None
        prefix = self.store.visit_prefix if self.store and self.store.visit_prefix else "CRE"
        return f'{prefix}-{self.store_visit_number:06d}'

    id = Column(Integer, primary_key=True, index=True)
    customer_name = Column(String(120), nullable=False)
    mobile_number = Column(String(24), nullable=False, index=True)
    product_type_id = Column(Integer, ForeignKey("customer_product_types.id"), nullable=False)
    category_id = Column(Integer, ForeignKey('customer_product_categories.id'), nullable=True)
    handled_by_id = Column(Integer, ForeignKey("cre_staff_members.id"), nullable=True, index=True)
    assigned_manager_id = Column(Integer, ForeignKey("cre_staff_members.id"), nullable=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id"), nullable=True, index=True)
    store_visit_number = Column(Integer, nullable=True)
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
    feedback = relationship("WalkInFeedback", back_populates="customer", uselist=False)


class WalkInFeedback(Base):
    __tablename__ = "walk_in_feedback"

    id = Column(Integer, primary_key=True, index=True)
    customer_visit_id = Column(Integer, ForeignKey("customer_visits.id"), nullable=False, unique=True, index=True)
    token = Column(String(100), nullable=False, unique=True, index=True)
    rating = Column(Integer, nullable=True)
    comments = Column(Text, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    sent_to = Column(String(20), nullable=True)
    send_status = Column(String(20), nullable=False, default="pending", index=True)
    provider_request_id = Column(String(100), nullable=True)
    provider_response = Column(Text, nullable=True)
    send_attempts = Column(Integer, nullable=False, default=0)
    sent_at = Column(DateTime, nullable=True)
    scheduled_at = Column(DateTime, nullable=True, index=True)
    next_attempt_at = Column(DateTime, nullable=True, index=True)
    processing_started_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    is_read = Column(Boolean, nullable=False, default=False, index=True)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now(), index=True)

    customer = relationship("CustomerVisit", back_populates="feedback")
    send_logs = relationship("FeedbackSendLog", back_populates="feedback")

    __table_args__ = (
        CheckConstraint("rating IS NULL OR rating BETWEEN 1 AND 5", name="ck_feedback_rating"),
    )


class FeedbackSendLog(Base):
    __tablename__ = "feedback_send_logs"

    id = Column(Integer, primary_key=True)
    feedback_id = Column(Integer, ForeignKey("walk_in_feedback.id"), nullable=False, index=True)
    requested_by = Column(String(100), nullable=True)
    source = Column(String(30), nullable=False)
    status = Column(String(20), nullable=False)
    provider_request_id = Column(String(100), nullable=True)
    provider_response = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    feedback = relationship("WalkInFeedback", back_populates="send_logs")


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
