import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from io import BytesIO
from typing import Optional
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from openpyxl import Workbook
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError
from visit_options import OPTION_KINDS, option_names, valid_option

from auth import (
    authenticate_user,
    login_user,
    logout_user,
    require_cre_manager_access,
    require_cre_owner_access,
    require_customer_relations_access,
)
from database import get_db
from settings import TEMPLATE_DIR
from models import CreVisitOption, AdminUser, CreStaffMember, CustomerFormField, CustomerProductType, CustomerVisit, Store, UserRole
from auth import get_current_user_web


router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals["visit_status"] = lambda visit: display_visit_status(visit)

ACTIVITY_TYPES = (
    "New Jewellery",
    "Old Gold",
    "Repair Taken",
    "Repair Delivered",
    "Ordered Delivered",
)
NEW_JEWELLERY_STATUSES = ("Billed", "Walkout")
NEW_JEWELLERY_EDIT_STATUSES = ("Draft", *NEW_JEWELLERY_STATUSES)
VISIT_STATUSES = ("Draft", "Billed", "Walkout", *ACTIVITY_TYPES[1:])


def display_visit_status(visit):
    if visit.activity_type == "New Jewellery":
        return "Draft" if not visit.status or visit.status == "Draft" else visit.status
    return visit.activity_type


def resolve_visit_status(form):
    value = str(form.get("status", "")).strip()
    # Accept older clients while all current forms use a single Status field.
    if not value:
        activity = str(form.get("activity_type", "")).strip()
        if activity not in ACTIVITY_TYPES:
            raise ValueError("Choose a valid status.")
        value = "Draft" if activity == "New Jewellery" else activity
    if value == "Registered":
        value = "Draft"
    if value not in VISIT_STATUSES:
        raise ValueError("Choose a valid status.")
    if value in ACTIVITY_TYPES[1:]:
        return value, None
    return "New Jewellery", value


def walkout_values(db, form, status, visit=None):
    if status != "Walkout":
        return None, None, None
    remark = str(form.get("remark", "")).strip()
    reason = str(form.get("walkout_reason", "")).strip()
    competitor = str(form.get("competitor_shop", "")).strip()
    if not remark or len(remark) > 1000:
        raise ValueError("Add a walkout remark up to 1000 characters.")
    if not valid_option(db, "reason", reason, visit.walkout_reason if visit else None):
        raise ValueError("Choose a valid walkout reason.")
    if competitor and not valid_option(db, "competitor", competitor, visit.competitor_shop if visit else None):
        raise ValueError("Choose a valid competitor shop.")
    return remark, reason, competitor or None


REPORT_TABS = {"overview", "walkin", "walkout", "competitor", "manager", "sales"}
FIELD_TYPES = ("text", "textarea", "number", "date", "select")
CRE_PANEL_ROLES = {
    UserRole.CUSTOMER_RELATION_EXECUTIVE.value,
    UserRole.SUPER_ADMIN.value,
}
PAGE_SIZE = 20


@router.get("/admin/customer-relations", include_in_schema=False)
async def redirect_legacy_cre_url(request: Request):
    """Move old bookmarks to the dedicated CRE URL while preserving filters."""
    allowed = {
        key: request.query_params[key]
        for key in ("q", "activity", "sales_person", "manager", "page")
        if request.query_params.get(key)
    }
    suffix = f"?{urlencode(allowed)}" if allowed else ""
    return RedirectResponse(f"/cre/dashboard{suffix}", status_code=302)


def _panel_url(message: str = "", error: str = "", path: str = "/cre/dashboard") -> str:
    if error:
        return f"{path}?error={quote(error)}"
    if message:
        return f"{path}?message={quote(message)}"
    return path


def _dashboard_filter_query(q: str, activity: str, sales_person: int, manager: int) -> str:
    values = {}
    if q:
        values["q"] = q
    if activity:
        values["activity"] = activity
    if sales_person:
        values["sales_person"] = sales_person
    if manager:
        values["manager"] = manager
    return urlencode(values)


def _report_date_range(date_from: Optional[date], date_to: Optional[date]):
    """Return an inclusive report range, defaulting to the current month."""
    today = date.today()
    start = date_from or today.replace(day=1)
    end = date_to or today
    if start > end:
        start, end = end, start
    return start, end


def _report_visits_query(
    db: Session,
    start: date,
    end: date,
    store_id: int,
    manager_id: int,
    sales_person_id: int = 0,
    walkout_reason: str = "",
    competitor_shop: str = "",
):
    query = db.query(CustomerVisit).options(
        joinedload(CustomerVisit.store),
        joinedload(CustomerVisit.handled_by),
        joinedload(CustomerVisit.assigned_manager),
    ).filter(
        CustomerVisit.created_at >= datetime.combine(start, time.min),
        CustomerVisit.created_at < datetime.combine(end + timedelta(days=1), time.min),
    )
    if store_id:
        query = query.filter(CustomerVisit.store_id == store_id)
    if manager_id:
        query = query.filter(CustomerVisit.assigned_manager_id == manager_id)
    if sales_person_id:
        query = query.filter(CustomerVisit.handled_by_id == sales_person_id)
    if walkout_reason:
        query = query.filter(CustomerVisit.walkout_reason == walkout_reason)
    if competitor_shop:
        query = query.filter(CustomerVisit.competitor_shop == competitor_shop)
    return query


def _build_report_data(
    db: Session,
    start: date,
    end: date,
    store_id: int,
    manager_id: int,
    sales_person_id: int = 0,
    walkout_reason: str = "",
    competitor_shop: str = "",
):
    visits = _report_visits_query(
        db, start, end, store_id, manager_id, sales_person_id, walkout_reason, competitor_shop
    ).all()
    billed = [visit for visit in visits if visit.status == "Billed"]
    walkouts = [visit for visit in visits if visit.status == "Walkout"]
    pending = [visit for visit in visits if visit.activity_type == "New Jewellery" and visit.status == "Draft"]
    # The summary must reconcile: every walk-in is either billed, a walkout, or another/pending visitor.
    other_visitors_count = len(visits) - len(billed) - len(walkouts)

    manager_totals = defaultdict(lambda: {"visits": 0, "walkouts": 0, "billed": 0})
    for visit in visits:
        manager_name = visit.assigned_manager.name if visit.assigned_manager else "Not Assigned"
        data = manager_totals[manager_name]
        data["visits"] += 1
        if visit.status == "Walkout":
            data["walkouts"] += 1
        elif visit.status == "Billed":
            data["billed"] += 1
    manager_rows = [
        {
            "name": name,
            **values,
            "conversion_rate": round((values["billed"] / values["visits"] * 100) if values["visits"] else 0, 2),
        }
        for name, values in sorted(manager_totals.items(), key=lambda item: (-item[1]["visits"], item[0].lower()))
    ]

    sales_totals = defaultdict(lambda: {"visits": 0, "walkouts": 0, "billed": 0})
    for visit in visits:
        sales_name = visit.handled_by.name if visit.handled_by else "Not Assigned"
        data = sales_totals[sales_name]
        data["visits"] += 1
        if visit.status == "Walkout":
            data["walkouts"] += 1
        elif visit.status == "Billed":
            data["billed"] += 1
    sales_rows = [
        {
            "name": name,
            **values,
            "conversion_rate": round((values["billed"] / values["visits"] * 100) if values["visits"] else 0, 2),
        }
        for name, values in sorted(sales_totals.items(), key=lambda item: (-item[1]["visits"], item[0].lower()))
    ]

    reason_rows = [
        {"name": name, "count": count}
        for name, count in sorted(
            Counter((visit.walkout_reason or "Not recorded") for visit in walkouts).items(),
            key=lambda item: (-item[1], item[0].lower()),
        )
    ]
    competitor_rows = [
        {"name": name, "count": count}
        for name, count in sorted(
            Counter((visit.competitor_shop or "Not recorded") for visit in walkouts).items(),
            key=lambda item: (-item[1], item[0].lower()),
        )
    ]
    walkin_rows = [
        {
            "date": visit.created_at,
            "customer_name": visit.customer_name,
            "mobile_number": visit.mobile_number,
            "store_name": visit.store.store_name if visit.store else "Not recorded",
            "activity_type": visit.activity_type,
            "status": display_visit_status(visit),
            "sales_person": visit.handled_by.name if visit.handled_by else "Not Assigned",
            "manager": visit.assigned_manager.name if visit.assigned_manager else "Not Assigned",
        }
        for visit in sorted(visits, key=lambda visit: visit.created_at, reverse=True)
    ]
    walkout_rows = [
        {
            "date": visit.created_at,
            "customer_name": visit.customer_name,
            "mobile_number": visit.mobile_number,
            "store_name": visit.store.store_name if visit.store else "Not recorded",
            "reason": visit.walkout_reason or "Not recorded",
            "competitor": visit.competitor_shop or "Not recorded",
            "manager": visit.assigned_manager.name if visit.assigned_manager else "Not Assigned",
            "remark": visit.remark or "-",
        }
        for visit in sorted(walkouts, key=lambda visit: visit.created_at, reverse=True)
    ]
    total = len(visits)
    return {
        "summary": {
            "total": total,
            "billed": len(billed),
            "walkouts": len(walkouts),
            "other_visitors": other_visitors_count,
            "pending": len(pending),
            "billed_percent": round((len(billed) / total * 100) if total else 0, 2),
            "walkout_percent": round((len(walkouts) / total * 100) if total else 0, 2),
            "other_visitors_percent": round((other_visitors_count / total * 100) if total else 0, 2),
        },
        "manager_rows": manager_rows,
        "sales_rows": sales_rows,
        "reason_rows": reason_rows,
        "competitor_rows": competitor_rows,
        "walkin_rows": walkin_rows,
        "walkout_rows": walkout_rows,
    }


def _render_staff_page(request, user, staff, page_title, staff_role, message="", error=""):
    return templates.TemplateResponse("cre_staff.html", {
        "request": request,
        "user": user,
        "user_role": user.role,
        "staff": staff,
        "page_title": page_title,
        "staff_role": staff_role,
        "can_add_staff": True,
        "message": message,
        "error": error,
    })


def _render_cre_page(template_name, request, user, **context):
    return templates.TemplateResponse(template_name, {
        "request": request,
        "user": user,
        "user_role": user.role,
        **context,
    })


@router.get("/cre", include_in_schema=False)
@router.get("/cre/", include_in_schema=False)
async def cre_root(request: Request, db: Session = Depends(get_db)):
    user = await get_current_user_web(request, db)
    if user and user.role in CRE_PANEL_ROLES:
        return RedirectResponse("/cre/dashboard", status_code=302)
    return RedirectResponse("/cre/login", status_code=302)


@router.get("/cre/login", response_class=HTMLResponse)
async def cre_login_page(request: Request, db: Session = Depends(get_db)):
    user = await get_current_user_web(request, db)
    if user and user.role in CRE_PANEL_ROLES:
        return RedirectResponse("/cre/dashboard", status_code=302)
    return templates.TemplateResponse("cre_login.html", {"request": request, "error": ""})


@router.post("/cre/login")
async def cre_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, username, password)
    if not user or user.role not in CRE_PANEL_ROLES:
        return templates.TemplateResponse(
            "cre_login.html",
            {"request": request, "error": "Invalid CRE username or password."},
            status_code=401,
        )

    request.session.clear()
    login_user(request, user)
    return RedirectResponse("/cre/dashboard", status_code=303)


@router.get("/cre/logout")
async def cre_logout(request: Request):
    logout_user(request)
    return RedirectResponse("/cre/login", status_code=303)


@router.get("/cre/dashboard", response_class=HTMLResponse)
async def cre_dashboard(
    request: Request,
    q: str = Query(default="", max_length=120),
    activity: str = Query(default="", max_length=40),
    sales_person: int = Query(default=0, ge=0),
    manager: int = Query(default=0, ge=0),
    page: int = Query(default=1, ge=1),
    message: str = Query(default="", max_length=120),
    error: str = Query(default="", max_length=160),
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_customer_relations_access),
):
    product_types = db.query(CustomerProductType).order_by(
        func.lower(CustomerProductType.name)
    ).all()
    stores = db.query(Store).order_by(func.lower(Store.store_name)).all()
    form_fields = db.query(CustomerFormField).filter(
        CustomerFormField.is_active == 1
    ).order_by(CustomerFormField.id).all()
    form_field_definitions = [
        {"field": field, "options": (field.options or "").splitlines()}
        for field in form_fields
    ]

    sales_people = db.query(CreStaffMember).filter(
        CreStaffMember.staff_type == "sales_person",
        CreStaffMember.is_active == 1,
    ).order_by(func.lower(CreStaffMember.name)).all()
    managers = db.query(CreStaffMember).filter(
        CreStaffMember.staff_type == "manager",
        CreStaffMember.is_active == 1,
    ).order_by(func.lower(CreStaffMember.name)).all()

    base_query = db.query(CustomerVisit)
    visits_query = base_query.options(
        joinedload(CustomerVisit.product_type),
        joinedload(CustomerVisit.handled_by),
        joinedload(CustomerVisit.assigned_manager),
    )
    if q.strip():
        search = f"%{q.strip()}%"
        visits_query = visits_query.join(CustomerVisit.product_type).filter(
            or_(
                CustomerVisit.customer_name.ilike(search),
                CustomerVisit.mobile_number.ilike(search),
                CustomerProductType.name.ilike(search),
            )
        )
    if activity in ("Draft", "Billed", "Walkout"):
        visits_query = visits_query.filter(CustomerVisit.activity_type == "New Jewellery",
                                           CustomerVisit.status == activity)
    elif activity in ACTIVITY_TYPES:
        visits_query = visits_query.filter(CustomerVisit.activity_type == activity)
    if sales_person:
        visits_query = visits_query.filter(CustomerVisit.handled_by_id == sales_person)
    if manager:
        visits_query = visits_query.filter(CustomerVisit.assigned_manager_id == manager)

    total_filtered = visits_query.count()
    total_pages = max(1, math.ceil(total_filtered / PAGE_SIZE))
    page = min(page, total_pages)
    visits = visits_query.order_by(CustomerVisit.created_at.desc()).offset(
        (page - 1) * PAGE_SIZE
    ).limit(PAGE_SIZE).all()

    visit_rows = []
    serial_number = (page - 1) * PAGE_SIZE + 1
    for row_index, visit in enumerate(visits):
        try:
            values = json.loads(visit.custom_fields or "{}")
        except (TypeError, ValueError):
            values = {}
        custom_values = [
            {"value": values.get(str(field.id), "")}
            for field in form_fields
        ]
        visit_rows.append({
            "visit": visit,
            "custom_values": custom_values,
            "serial_number": serial_number + row_index,
        })

    scope_count = base_query.count()
    draft_count = base_query.filter(
        CustomerVisit.activity_type == "New Jewellery",
        CustomerVisit.status == "Draft",
    ).count()
    billed_count = base_query.filter(
        CustomerVisit.activity_type == "New Jewellery",
        CustomerVisit.status == "Billed",
    ).count()
    walkout_count = base_query.filter(
        CustomerVisit.activity_type == "New Jewellery",
        CustomerVisit.status == "Walkout",
    ).count()
    sales_people_count = len(sales_people)

    return _render_cre_page(
        "cre_dashboard.html",
        request,
        current_user,
        product_types=product_types,
        stores=stores,
        form_fields=form_fields,
        form_field_definitions=form_field_definitions,
        custom_columns=form_fields,
        sales_people=sales_people,
        managers=managers,
        activity_types=ACTIVITY_TYPES,
        visit_statuses=VISIT_STATUSES,
        new_jewellery_statuses=NEW_JEWELLERY_STATUSES,
        walkout_reasons=option_names(db, "reason"),
        competitor_shops=option_names(db, "competitor"),
        visits=visit_rows,
        query=q,
        selected_activity=activity,
        selected_sales_person=sales_person,
        selected_manager=manager,
        page=page,
        total_pages=total_pages,
        page_numbers=range(max(1, page - 2), min(total_pages, page + 2) + 1),
        total_filtered=total_filtered,
        filter_query=_dashboard_filter_query(q, activity, sales_person, manager),
        total_visits=scope_count,
        draft_count=draft_count,
        billed_count=billed_count,
        walkout_count=walkout_count,
        sales_people_count=sales_people_count,
        can_manage=True,
        can_add_customer=bool(product_types and stores),
        message=message,
        error=error,
    )


@router.get("/cre/reports", response_class=HTMLResponse)
async def cre_reports(
    request: Request,
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    store_id: int = Query(default=0, ge=0),
    manager_id: int = Query(default=0, ge=0),
    sales_person_id: int = Query(default=0, ge=0),
    walkout_reason: str = Query(default="", max_length=80),
    competitor_shop: str = Query(default="", max_length=100),
    tab: str = Query(default="overview", max_length=20),
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_customer_relations_access),
):
    start, end = _report_date_range(date_from, date_to)
    active_tab = tab if tab in REPORT_TABS else "overview"
    return _render_cre_page(
        "cre_reports.html",
        request,
        current_user,
        report=_build_report_data(
            db, start, end, store_id, manager_id, sales_person_id, walkout_reason, competitor_shop
        ),
        stores=db.query(Store).order_by(func.lower(Store.store_name)).all(),
        managers=db.query(CreStaffMember).filter(
            CreStaffMember.staff_type == "manager",
            CreStaffMember.is_active == 1,
        ).order_by(func.lower(CreStaffMember.name)).all(),
        sales_people=db.query(CreStaffMember).filter(
            CreStaffMember.staff_type == "sales_person",
            CreStaffMember.is_active == 1,
        ).order_by(func.lower(CreStaffMember.name)).all(),
        date_from=start,
        date_to=end,
        selected_store_id=store_id,
        selected_manager_id=manager_id,
        selected_sales_person_id=sales_person_id,
        selected_walkout_reason=walkout_reason,
        selected_competitor_shop=competitor_shop,
        active_tab=active_tab,
        walkout_reasons=option_names(db, "reason", include_history=True),
        competitor_shops=option_names(db, "competitor", include_history=True),
    )


@router.get("/cre/reports/export/excel")
async def export_cre_report_excel(
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    store_id: int = Query(default=0, ge=0),
    manager_id: int = Query(default=0, ge=0),
    sales_person_id: int = Query(default=0, ge=0),
    walkout_reason: str = Query(default="", max_length=80),
    competitor_shop: str = Query(default="", max_length=100),
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_customer_relations_access),
):
    start, end = _report_date_range(date_from, date_to)
    report = _build_report_data(
        db, start, end, store_id, manager_id, sales_person_id, walkout_reason, competitor_shop
    )
    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Walk-in Summary"
    summary_sheet.append(["CRE walk-in report", f"{start:%d %b %Y} - {end:%d %b %Y}"])
    summary_sheet.append(["Metric", "Count", "Percentage"])
    summary = report["summary"]
    summary_sheet.append(["Billed", summary["billed"], summary["billed_percent"] / 100])
    summary_sheet.append(["Other visitors", summary["other_visitors"], summary["other_visitors_percent"] / 100])
    summary_sheet.append(["Walkouts", summary["walkouts"], summary["walkout_percent"] / 100])
    summary_sheet.append(["Total walk-ins", summary["total"], 1 if summary["total"] else 0])
    for cell in summary_sheet[2]:
        cell.font = cell.font.copy(bold=True)
    for row in (3, 4, 5, 6):
        summary_sheet.cell(row=row, column=3).number_format = "0.00%"
    summary_sheet.column_dimensions["A"].width = 24
    summary_sheet.column_dimensions["B"].width = 16
    summary_sheet.column_dimensions["C"].width = 16

    def add_sheet(title, headers, rows):
        sheet = workbook.create_sheet(title)
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        for cell in sheet[1]:
            cell.font = cell.font.copy(bold=True)
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = min(32, max(14, max(len(str(cell.value or "")) for cell in column) + 2))

    add_sheet(
        "Manager summary",
        ["Manager", "Visits", "Walkouts", "Billed", "Conversion %"],
        [[row["name"], row["visits"], row["walkouts"], row["billed"], row["conversion_rate"] / 100] for row in report["manager_rows"]],
    )
    manager_sheet = workbook["Manager summary"]
    for cell in manager_sheet["E"][1:]:
        cell.number_format = "0.00%"
    add_sheet(
        "Sales person summary",
        ["Sales person", "Visits", "Walkouts", "Billed", "Conversion %"],
        [[row["name"], row["visits"], row["walkouts"], row["billed"], row["conversion_rate"] / 100] for row in report["sales_rows"]],
    )
    sales_sheet = workbook["Sales person summary"]
    for cell in sales_sheet["E"][1:]:
        cell.number_format = "0.00%"
    add_sheet("Walkout reasons", ["Reason", "Walkouts"], [[row["name"], row["count"]] for row in report["reason_rows"]])
    add_sheet("Competitors", ["Competitor", "Walkouts"], [[row["name"], row["count"]] for row in report["competitor_rows"]])

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    filename = f"cre-walkin-report-{start.isoformat()}-to-{end.isoformat()}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/cre/customers")
async def add_customer_visit(
    request: Request,
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_customer_relations_access),
):
    form = await request.form()
    customer_name = " ".join(str(form.get("customer_name", "")).split())
    mobile_number = str(form.get("mobile_number", "")).strip()
    product_type_id = form.get("product_type_id", "")
    try:
        activity_type, status = resolve_visit_status(form)
        remark, walkout_reason, competitor_shop = walkout_values(db, form, status)
    except ValueError as error:
        return RedirectResponse(_panel_url(error=str(error)), status_code=303)
    handled_by_id = form.get("handled_by_id", "")
    assigned_manager_id = form.get("assigned_manager_id", "")
    store_id = form.get("store_id", "")
    digits = re.sub(r"\D", "", mobile_number)

    if not customer_name:
        return RedirectResponse(_panel_url(error="Enter the customer's name."), status_code=303)
    if not re.fullmatch(r"\+?[\d\s().-]+", mobile_number) or len(digits) < 7 or len(digits) > 15:
        return RedirectResponse(_panel_url(error="Enter a valid mobile number."), status_code=303)
    if activity_type not in ACTIVITY_TYPES:
        return RedirectResponse(_panel_url(error="Choose a valid customer activity."), status_code=303)

    try:
        product_type_id = int(product_type_id)
    except (TypeError, ValueError):
        return RedirectResponse(_panel_url(error="Choose a product type, or add one first."), status_code=303)
    product_type = db.query(CustomerProductType).filter(
        CustomerProductType.id == product_type_id
    ).first()
    if not product_type:
        return RedirectResponse(_panel_url(error="Choose a product type, or add one first."), status_code=303)

    try:
        handled_by_id = int(handled_by_id) if handled_by_id else None
        assigned_manager_id = int(assigned_manager_id) if assigned_manager_id else None
        store_id = int(store_id)
    except (TypeError, ValueError):
        return RedirectResponse(_panel_url(error="Select the store, sales person, and assigned manager."), status_code=303)
    handled_by = db.query(CreStaffMember).filter(
        CreStaffMember.id == handled_by_id,
        CreStaffMember.staff_type == "sales_person",
        CreStaffMember.is_active == 1,
    ).first()
    assigned_manager = db.query(CreStaffMember).filter(
        CreStaffMember.id == assigned_manager_id,
        CreStaffMember.staff_type == "manager",
        CreStaffMember.is_active == 1,
    ).first()
    if (handled_by_id and not handled_by) or (assigned_manager_id and not assigned_manager):
        return RedirectResponse(_panel_url(error="Select an active sales person and manager."), status_code=303)
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        return RedirectResponse(_panel_url(error="Select a valid store."), status_code=303)

    form_fields = db.query(CustomerFormField).filter(
        CustomerFormField.is_active == 1
    ).order_by(CustomerFormField.id).all()
    custom_values = {}
    for field in form_fields:
        value = str(form.get(f"custom_{field.id}", "")).strip()
        if field.is_required and not value:
            return RedirectResponse(
                _panel_url(error=f"{field.label} is required."), status_code=303
            )
        if not value:
            continue
        if field.field_type == "select" and value not in (field.options or "").splitlines():
            return RedirectResponse(_panel_url(error=f"Choose a valid value for {field.label}."), status_code=303)
        if field.field_type == "number":
            try:
                float(value)
            except ValueError:
                return RedirectResponse(_panel_url(error=f"Enter a number for {field.label}."), status_code=303)
        if field.field_type == "date":
            try:
                date.fromisoformat(value)
            except ValueError:
                return RedirectResponse(_panel_url(error=f"Enter a valid date for {field.label}."), status_code=303)
        custom_values[str(field.id)] = value

    visit = CustomerVisit(
        customer_name=customer_name[:120],
        mobile_number=mobile_number[:24],
        product_type_id=product_type.id,
        handled_by_id=handled_by.id if handled_by else None,
        assigned_manager_id=assigned_manager.id if assigned_manager else None,
        store_id=store.id,
        activity_type=activity_type,
        status=status,
        remark=remark,
        walkout_reason=walkout_reason,
        competitor_shop=competitor_shop,
        custom_fields=json.dumps(custom_values, ensure_ascii=False),
        created_by=current_user.id,
    )
    db.add(visit)
    db.commit()
    return RedirectResponse(_panel_url(message="Customer visit added."), status_code=303)


@router.post("/cre/customers/{visit_id}/status")
async def update_new_jewellery_status(
    visit_id: int,
    status: str = Form(..., max_length=40),
    remark: str = Form(default="", max_length=1000),
    walkout_reason: str = Form(default="", max_length=80),
    competitor_shop: str = Form(default="", max_length=100),
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_customer_relations_access),
):
    visit = db.query(CustomerVisit).filter(CustomerVisit.id == visit_id).first()
    if not visit:
        return RedirectResponse(_panel_url(error="Customer visit was not found."), status_code=303)
    try:
        activity_type, stored_status = resolve_visit_status({"status": status})
        remark, walkout_reason, competitor_shop = walkout_values(db, {
            "remark": remark, "walkout_reason": walkout_reason,
            "competitor_shop": competitor_shop}, stored_status, visit)
    except ValueError as error:
        return RedirectResponse(_panel_url(error=str(error)), status_code=303)
    visit.activity_type = activity_type
    visit.status = stored_status
    visit.remark = remark
    visit.walkout_reason = walkout_reason
    visit.competitor_shop = competitor_shop
    db.commit()
    return RedirectResponse(_panel_url(message=f"Status updated to {status}."), status_code=303)


@router.post("/cre/customers/{visit_id}/edit")
async def edit_customer_visit(
    visit_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_customer_relations_access),
):
    visit = db.query(CustomerVisit).filter(CustomerVisit.id == visit_id).first()
    if not visit:
        return RedirectResponse(_panel_url(error="Customer visit was not found."), status_code=303)

    form = await request.form()
    customer_name = " ".join(str(form.get("customer_name", "")).split())
    mobile_number = str(form.get("mobile_number", "")).strip()
    digits = re.sub(r"\D", "", mobile_number)
    if not customer_name or len(customer_name) > 120:
        return RedirectResponse(_panel_url(error="Enter a customer name up to 120 characters."), status_code=303)
    if len(mobile_number) > 24 or not re.fullmatch(r"\+?[\d\s().-]+", mobile_number) or not 7 <= len(digits) <= 15:
        return RedirectResponse(_panel_url(error="Enter a valid mobile number."), status_code=303)

    try:
        activity_type, status = resolve_visit_status(form)
    except ValueError as error:
        return RedirectResponse(_panel_url(error=str(error)), status_code=303)

    try:
        product_type_id = int(form.get("product_type_id", ""))
        handled_by_id = int(form.get("handled_by_id") or 0)
        assigned_manager_id = int(form.get("assigned_manager_id") or 0)
        store_id = int(form.get("store_id", ""))
    except (TypeError, ValueError):
        return RedirectResponse(_panel_url(error="Choose a store, product, sales person, and manager."), status_code=303)

    product_type = db.query(CustomerProductType).filter(
        CustomerProductType.id == product_type_id
    ).first()
    handled_by = db.query(CreStaffMember).filter(
        CreStaffMember.id == handled_by_id,
        CreStaffMember.staff_type == "sales_person",
        or_(CreStaffMember.is_active == 1, CreStaffMember.id == visit.handled_by_id),
    ).first()
    assigned_manager = db.query(CreStaffMember).filter(
        CreStaffMember.id == assigned_manager_id,
        CreStaffMember.staff_type == "manager",
        or_(CreStaffMember.is_active == 1, CreStaffMember.id == visit.assigned_manager_id),
    ).first()
    store = db.query(Store).filter(Store.id == store_id).first()
    if not product_type or not store or (handled_by_id and not handled_by) or (assigned_manager_id and not assigned_manager):
        return RedirectResponse(_panel_url(error="Choose a valid store, product, sales person, and manager."), status_code=303)

    remark = str(form.get("remark", "")).strip()
    if len(remark) > 1000:
        return RedirectResponse(_panel_url(error="Remark must be 1000 characters or fewer."), status_code=303)
    try:
        walkout_remark, walkout_reason, competitor_shop = walkout_values(db, form, status, visit)
        if status == "Walkout":
            remark = walkout_remark
    except ValueError as error:
        return RedirectResponse(_panel_url(error=str(error)), status_code=303)

    try:
        custom_values = json.loads(visit.custom_fields or "{}")
        if not isinstance(custom_values, dict):
            custom_values = {}
    except (TypeError, ValueError):
        custom_values = {}

    form_fields = db.query(CustomerFormField).filter(
        CustomerFormField.is_active == 1
    ).order_by(CustomerFormField.id).all()
    for field in form_fields:
        value = str(form.get(f"custom_{field.id}", "")).strip()
        if field.is_required and not value:
            return RedirectResponse(_panel_url(error=f"{field.label} is required."), status_code=303)
        if len(value) > 500:
            return RedirectResponse(_panel_url(error=f"{field.label} must be 500 characters or fewer."), status_code=303)
        if field.field_type == "select" and value and value not in (field.options or "").splitlines():
            return RedirectResponse(_panel_url(error=f"Choose a valid value for {field.label}."), status_code=303)
        if field.field_type == "number" and value:
            try:
                if not math.isfinite(float(value)):
                    raise ValueError
            except ValueError:
                return RedirectResponse(_panel_url(error=f"Enter a valid number for {field.label}."), status_code=303)
        if field.field_type == "date" and value:
            try:
                date.fromisoformat(value)
            except ValueError:
                return RedirectResponse(_panel_url(error=f"Enter a valid date for {field.label}."), status_code=303)
        if value:
            custom_values[str(field.id)] = value
        else:
            custom_values.pop(str(field.id), None)

    visit.customer_name = customer_name
    visit.mobile_number = mobile_number
    visit.product_type_id = product_type.id
    visit.handled_by_id = handled_by.id if handled_by else None
    visit.assigned_manager_id = assigned_manager.id if assigned_manager else None
    visit.store_id = store.id
    visit.activity_type = activity_type
    visit.status = status
    visit.remark = remark or None
    visit.walkout_reason = walkout_reason or None
    visit.competitor_shop = competitor_shop or None
    visit.custom_fields = json.dumps(custom_values, ensure_ascii=False)
    db.commit()
    return RedirectResponse(_panel_url(message="Customer visit updated."), status_code=303)


def _create_staff(db: Session, name: str, staff_type: str, created_by: int):
    name = " ".join(name.split())
    if not name:
        return "Enter the staff member's name."
    if db.query(CreStaffMember).filter(
        CreStaffMember.staff_type == staff_type,
        func.lower(CreStaffMember.name) == name.lower(),
    ).first():
        return "That name is already in the list."

    db.add(CreStaffMember(name=name[:100], staff_type=staff_type, created_by=created_by))
    db.commit()
    return ""


@router.get("/cre/sales-people", response_class=HTMLResponse)
async def sales_people_page(
    request: Request,
    message: str = Query(default="", max_length=120),
    error: str = Query(default="", max_length=160),
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_cre_manager_access),
):
    staff = db.query(CreStaffMember).filter(
        CreStaffMember.staff_type == "sales_person",
        CreStaffMember.is_active == 1,
    ).order_by(func.lower(CreStaffMember.name)).all()
    return _render_staff_page(request, current_user, staff, "Sales People", "sales_person", message, error)


@router.post("/cre/sales-people")
async def add_sales_person(
    name: str = Form(..., max_length=100),
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_cre_manager_access),
):
    error = _create_staff(db, name, "sales_person", current_user.id)
    if error:
        return RedirectResponse(_panel_url(error=error, path="/cre/sales-people"), status_code=303)
    return RedirectResponse(_panel_url(message="Sales person added to the list.", path="/cre/sales-people"), status_code=303)


@router.get("/cre/managers", response_class=HTMLResponse)
async def managers_page(
    request: Request,
    message: str = Query(default="", max_length=120),
    error: str = Query(default="", max_length=160),
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_cre_owner_access),
):
    staff = db.query(CreStaffMember).filter(
        CreStaffMember.staff_type == "manager",
        CreStaffMember.is_active == 1,
    ).order_by(func.lower(CreStaffMember.name)).all()
    return _render_staff_page(request, current_user, staff, "Managers", "manager", message, error)


@router.post("/cre/managers")
async def add_manager(
    name: str = Form(..., max_length=100),
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_cre_owner_access),
):
    error = _create_staff(db, name, "manager", current_user.id)
    if error:
        return RedirectResponse(_panel_url(error=error, path="/cre/managers"), status_code=303)
    return RedirectResponse(_panel_url(message="Manager added to the list.", path="/cre/managers"), status_code=303)


@router.get("/cre/products", response_class=HTMLResponse)
async def products_page(
    request: Request,
    message: str = Query(default="", max_length=120),
    error: str = Query(default="", max_length=160),
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_cre_manager_access),
):
    products = db.query(CustomerProductType).order_by(func.lower(CustomerProductType.name)).all()
    return _render_cre_page(
        "cre_products.html", request, current_user,
        products=products, message=message, error=error,
    )


@router.post("/cre/products")
async def add_product(
    name: str = Form(..., max_length=80),
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_cre_manager_access),
):
    name = " ".join(name.split())
    path = "/cre/products"
    if not name:
        return RedirectResponse(_panel_url(error="Enter a product name.", path=path), status_code=303)
    if db.query(CustomerProductType).filter(func.lower(CustomerProductType.name) == name.lower()).first():
        return RedirectResponse(_panel_url(error="That product already exists.", path=path), status_code=303)
    db.add(CustomerProductType(name=name, created_by=current_user.id))
    db.commit()
    return RedirectResponse(_panel_url(message="Product added to the customer form.", path=path), status_code=303)


@router.get("/cre/fields", response_class=HTMLResponse)
async def fields_page(
    request: Request,
    message: str = Query(default="", max_length=120),
    error: str = Query(default="", max_length=160),
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_cre_manager_access),
):
    fields = db.query(CustomerFormField).order_by(CustomerFormField.id).all()
    return _render_cre_page(
        "cre_fields.html", request, current_user,
        fields=fields,
        active_field_count=sum(1 for field in fields if field.is_active),
        field_types=FIELD_TYPES,
        message=message,
        error=error,
    )


@router.post("/cre/fields")
async def add_form_field(
    label: str = Form(..., max_length=80),
    field_type: str = Form(..., max_length=20),
    options: str = Form(default="", max_length=1000),
    is_required: str = Form(default=""),
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_cre_manager_access),
):
    label = " ".join(label.split())
    options = "\n".join(dict.fromkeys(
        " ".join(value.split()) for value in options.split(",") if value.strip()
    ))
    path = "/cre/fields"
    if not label:
        return RedirectResponse(_panel_url(error="Enter a field name.", path=path), status_code=303)
    if field_type not in FIELD_TYPES:
        return RedirectResponse(_panel_url(error="Choose a valid field type.", path=path), status_code=303)
    if field_type == "select" and not options:
        return RedirectResponse(_panel_url(error="Add comma-separated options for this dropdown field.", path=path), status_code=303)
    if db.query(CustomerFormField).filter(func.lower(CustomerFormField.label) == label.lower()).first():
        return RedirectResponse(_panel_url(error="A field with that name already exists.", path=path), status_code=303)

    db.add(CustomerFormField(
        label=label,
        field_type=field_type,
        options=options or None,
        is_required=1 if is_required == "on" else 0,
        created_by=current_user.id,
    ))
    db.commit()
    return RedirectResponse(_panel_url(message="Field added to the customer form.", path=path), status_code=303)


@router.post("/cre/fields/{field_id}/remove")
async def remove_form_field(
    field_id: int,
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_cre_manager_access),
):
    path = "/cre/fields"
    field = db.query(CustomerFormField).filter(CustomerFormField.id == field_id).first()
    if not field:
        return RedirectResponse(_panel_url(error="Form field was not found.", path=path), status_code=303)
    if not field.is_active:
        return RedirectResponse(_panel_url(message="This field is already removed.", path=path), status_code=303)

    field.is_active = 0
    db.commit()
    return RedirectResponse(
        _panel_url(message="Field removed from new forms. Existing customer data is preserved.", path=path),
        status_code=303,
    )


@router.post("/cre/fields/{field_id}/restore")
async def restore_form_field(
    field_id: int,
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_cre_manager_access),
):
    path = "/cre/fields"
    field = db.query(CustomerFormField).filter(CustomerFormField.id == field_id).first()
    if not field:
        return RedirectResponse(_panel_url(error="Form field was not found.", path=path), status_code=303)
    if field.is_active:
        return RedirectResponse(_panel_url(message="This field is already active.", path=path), status_code=303)

    field.is_active = 1
    db.commit()
    return RedirectResponse(
        _panel_url(message="Field restored to the customer form.", path=path),
        status_code=303,
    )


@router.get("/cre/walkout-options", response_class=HTMLResponse)
async def walkout_options_page(
    request: Request, message: str = "", error: str = "",
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_cre_manager_access),
):
    groups = [dict(kind=kind, title=values[0], max_length=values[1],
                   options=db.query(CreVisitOption).filter_by(kind=kind)
                   .order_by(CreVisitOption.is_active.desc(), CreVisitOption.name).all())
              for kind, values in OPTION_KINDS.items()]
    return templates.TemplateResponse("cre_walkout_options.html", dict(
        request=request, user=current_user, user_role=current_user.role,
        groups=groups, message=message, error=error))


@router.post("/cre/walkout-options/{kind}")
async def add_walkout_option(
    kind: str, name: str = Form(..., max_length=100),
    db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_cre_manager_access),
):
    path = "/cre/walkout-options"
    name = " ".join(name.split())
    if kind not in OPTION_KINDS or not name or len(name) > OPTION_KINDS[kind][1]:
        return RedirectResponse(_panel_url(error="Enter a valid name within the character limit.", path=path), status_code=303)
    existing = db.query(CreVisitOption).filter_by(kind=kind, name=name).first()
    if existing:
        return RedirectResponse(_panel_url(error="This name already exists. Restore it if removed.", path=path), status_code=303)
    db.add(CreVisitOption(kind=kind, name=name, created_by=current_user.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(_panel_url(error="This name already exists.", path=path), status_code=303)
    return RedirectResponse(_panel_url(message="Option added.", path=path), status_code=303)


@router.post("/cre/walkout-options/{option_id}/{action}")
async def toggle_walkout_option(
    option_id: int, action: str, db: Session = Depends(get_db),
    current_user: AdminUser = Depends(require_cre_manager_access),
):
    path = "/cre/walkout-options"
    option = db.get(CreVisitOption, option_id)
    if not option or action not in ("remove", "restore"):
        return RedirectResponse(_panel_url(error="Option or action was not found.", path=path), status_code=303)
    option.is_active = int(action == "restore")
    db.commit()
    return RedirectResponse(_panel_url(message="Option restored." if option.is_active else "Option removed from new selections. Existing visits are unchanged.", path=path), status_code=303)
