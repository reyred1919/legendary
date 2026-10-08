"""Read-only analytics and scoped report endpoints."""
from datetime import timedelta
from io import BytesIO
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from openpyxl import Workbook

from .models import Category, Department, Program, Project, Request, RoleAssignment, Service, User
from .policies import department_ids_for_role, program_ids_for_role, program_request_scope, project_ids_for_role, request_scope
from .reporting import (DIMENSIONS, OPEN, SNAPSHOT_FILTERS, aging, analytics_scope, approval_metrics, completed_during,
                        credit_metrics, facts_for, global_access, group, matrix, metrics, report_filters, trend, window_from_params)
from .utils import audit


def options_for(scope):
    return dict(departments=Department.objects.filter(pk__in=scope.values("department_id")).distinct().order_by("name"),
        programs=Program.objects.filter(pk__in=scope.exclude(program_id=None).values("program_id")).distinct().order_by("name"),
        projects=Project.objects.filter(pk__in=scope.exclude(project_entity_id=None).values("project_entity_id")).distinct().order_by("name"),
        families=Category.objects.filter(pk__in=scope.values("service__category_id")).select_related("department").distinct().order_by("name"),
        services=Service.objects.filter(pk__in=scope.values("service_id")).select_related("category__department").distinct().order_by("name"),
        requesters=User.objects.filter(pk__in=scope.values("requester_id")).distinct().order_by("username"),
        owners=User.objects.filter(pk__in=scope.exclude(assigned_owner_id=None).values("assigned_owner_id")).distinct().order_by("username"),
        units=scope.exclude(requesting_unit="").values_list("requesting_unit",flat=True).distinct().order_by("requesting_unit"),
        statuses=Request.Status.choices[1:],priorities=Request.Priority.choices)


def params_only(params, updates=None):
    keys=set(DIMENSIONS)|set(SNAPSHOT_FILTERS)|{"period","start","end"}
    result={key:params.get(key) for key in keys if params.get(key)}
    result.update(updates or {})
    return {key:value for key,value in result.items() if value is not None and value!=""}


def report_url(params,updates=None,route="analytics_requests"):
    query=urlencode(params_only(params,updates))
    return reverse(route)+(('?'+query) if query else '')


def link_rows(rows,dimension,params):
    codes=dict(Department.objects.filter(pk__in=[r["id"] for r in rows]).values_list("pk","code")) if dimension=="department" else {}
    snap={"department":"snapshot_department","program":"snapshot_program","project":"snapshot_project","family":"snapshot_family","service":"snapshot_service"}.get(dimension)
    for row in rows:
        value=codes.get(row["id"]) if dimension=="department" else row["id"]
        updates={dimension:value} if value else {}
        if snap:updates[snap]=row["name"]
        row["url"]=report_url(params,updates)
    return rows


def context_for(request,forced=None):
    scope=analytics_scope(request.user)
    params=params_only(request.GET)
    if forced:
        for key,value in forced.items():params[key]=Department.objects.get(pk=value).code if key=="department" else str(value)
    window=window_from_params(request.GET)
    filters=report_filters(request.user,request.GET,forced)
    facts=facts_for(scope,window,filters)
    current=metrics(facts);previous=metrics(facts_for(scope,window.previous(),filters))
    comparisons={key:round(current[key]-previous[key],1) if current[key] is not None and previous[key] is not None else None for key in ("total","completed","completion_rate","first_sla","completion_sla")}
    groups={key:link_rows(group(facts,key),key,params) for key in ("department","program","project","family","service","owner","status","priority")}
    cells=matrix(facts);codes=dict(Department.objects.filter(pk__in=[r["department_id"] for r in cells]).values_list("pk","code"))
    for row in cells:
        updates={"department":codes.get(row["department_id"]),"snapshot_department":row["department"],"snapshot_program":row["program"]}
        if row["program_id"]:updates["program"]=row["program_id"]
        row["url"]=report_url(params,updates)
    approvals=approval_metrics(facts);credits=credit_metrics(request.user,filters,window)
    timeline=trend(facts,window);throughput=completed_during(scope,window,filters)
    interval=1 if (window.end-window.start).days<45 else 7 if (window.end-window.start).days<180 else 30
    for row in timeline:
        start=timezone.datetime.fromisoformat(row["date"]).date()
        row["completed"]=sum(throughput[start+timedelta(days=n)] for n in range(interval))
    current["completed_in_period"]=sum(throughput.values());current["net_backlog_change"]=current["new"]-current["completed_in_period"]
    flow=[dict(name=label,total=sum(predicate(f) for f in facts)) for label,predicate in (
        ("ثبت",lambda f:f.status==Request.Status.SUBMITTED and not f.request.provider_hold),
        ("بررسی",lambda f:f.status in {Request.Status.UNDER_REVIEW,Request.Status.NEED_INFO,Request.Status.ACCEPTED,Request.Status.ON_HOLD}),
        ("تأیید",lambda f:f.request.provider_hold),("اجرا",lambda f:f.status==Request.Status.IN_PROGRESS),
        ("تکمیل",lambda f:f.status==Request.Status.COMPLETED))]
    resolution=[dict(name=label,total=sum(f.resolution_seconds is not None and lo<=f.resolution_seconds/86400<hi for f in facts)) for label,lo,hi in (("کمتر از ۲ روز",0,2),("۲ تا ۵ روز",2,5),("۵ تا ۱۰ روز",5,10),("۱۰ روز و بیشتر",10,float('inf')))]
    chart=dict(trend=timeline,department=groups["department"],status=groups["status"],sla=[row for row in (
        {"name":"پاسخ اولیه","total":current["first_sla"],"samples":current["first_sla_count"]},
        {"name":"تکمیل","total":current["completion_sla"],"samples":current["completion_sla_count"]}) if row["total"] is not None],
        service=groups["service"][:10],family=groups["family"][:10],program=groups["program"][:12],project=groups["project"][:12],
        resolution=resolution,aging=aging(facts),owner=groups["owner"],flow=flow,priority=groups["priority"],matrix=cells,
        credit=[{"name":f'{r["program"]} · {r["department"]} · {r["priority"]}',"total":r["utilization"],
                 "quantity":r["quantity"],"reserved":r["reserved"],"consumed":r["consumed"],"released":r["released"],"remaining":r["remaining"]} for r in credits[:10]])
    chart["credit"]=[row for row in chart["credit"] if row["total"] is not None]
    chart["approval"]=[{"name":"در انتظار","total":approvals["pending"]},{"name":"تأییدشده","total":approvals["approved"]},{"name":"ردشده","total":approvals["rejected"]}]
    return dict(scope=scope,window=window,filters=filters,facts=facts,kpis=current,previous=previous,comparisons=comparisons,
        groups=groups,matrix_rows=cells,approvals=approvals,credits=credits,chart_data=chart,aging_rows=chart["aging"],flow_rows=flow,trend_rows=timeline,
        period_options=(("today","امروز"),("7d","۷ روز اخیر"),("30d","۳۰ روز اخیر"),("month","ماه جاری"),("quarter","فصل جاری"),("6m","۶ ماه اخیر"),("ytd","ابتدای سال تا امروز"),("year","سال جاری"),("custom","بازه دلخواه")),
        chart_titles=(
            ("trend","روند درخواست و تکمیل"),("flow","گردش وضعیت درخواست"),("sla","سلامت SLA"),
            ("department","بار کاری اداره‌ها"),("program","تقاضا بر اساس طرح"),("project","تقاضا بر اساس پروژه"),
            ("owner","بار مدیران درخواست"),("service","تقاضای خدمات"),("family","خانواده‌های خدمت"),
            ("aging","سن درخواست‌های باز"),("matrix","طرح × اداره"),("priority","توزیع اولویت"),
            ("status","توزیع وضعیت فعلی"),("resolution","توزیع زمان حل"),
            ("approval","وضعیت تأییدها"),("credit","بهره‌برداری اعتبار"),
        ),
        report_sections=(("summary","خلاصه"),("demand","تقاضا"),("sla","SLA"),("department","اداره"),("program","طرح"),("service","خدمت"),("credit","اعتبار"),("approval","تأیید"),("appendix","پیوست درخواست‌ها")),
        filter_query=urlencode(params),selected=params,request_urls={key:report_url(params,{"metric":key}) for key in ("total","new","open","completed","pending","overdue","at_risk")},**options_for(scope))


@login_required
@require_GET
def dashboard(request,kind="executive",code=None,pk=None):
    forced=None
    if kind=="department":
        obj=get_object_or_404(Department,code=code);forced={"department":obj.pk};title=f"تحلیل ادارهٔ {obj.name}"
    elif kind=="program":
        obj=get_object_or_404(Program,pk=pk);forced={"program":obj.pk};title=f"تحلیل طرح {obj.name}"
    elif kind=="service":
        obj=get_object_or_404(Service,pk=pk);forced={"service":obj.pk};title=f"تحلیل خدمت {obj.name}"
    else:title="هوش مدیریتی"
    ctx=context_for(request,forced)
    if forced:
        field={"department":"department_id","program":"program_id","service":"service_id"}[kind]
        scoped=ctx["scope"].filter(**{field:obj.pk}).exists()
        departments=department_ids_for_role(request.user,RoleAssignment.Role.DEPARTMENT_LEAD)|department_ids_for_role(request.user,RoleAssignment.Role.REQUEST_MANAGER)
        program_ids=program_ids_for_role(request.user)|set(Project.objects.filter(pk__in=project_ids_for_role(request.user)).values_list("program_id",flat=True))
        direct=obj.pk in departments if kind=="department" else obj.pk in program_ids if kind=="program" else obj.category.department_id in departments
        if not (scoped or direct or global_access(request.user)):raise Http404
    clear_url=reverse("analytics_dashboard")
    if kind=="department":clear_url=reverse("analytics_department",args=[code])
    elif kind=="program":clear_url=reverse("analytics_program",args=[pk])
    elif kind=="service":clear_url=reverse("analytics_service",args=[pk])
    ctx.update(title=title,kind=kind,clear_url=clear_url)
    return render(request,"control/analytics.html",ctx)


def metric_facts(facts,metric):
    rules={"total":lambda f:True,"new":lambda f:True,"open":lambda f:f.status in OPEN,"completed":lambda f:f.status==Request.Status.COMPLETED,
           "pending":lambda f:f.request.provider_hold,"overdue":lambda f:f.overdue,"at_risk":lambda f:f.at_risk}
    if metric not in rules:raise Http404
    return [f for f in facts if rules[metric](f)]


def selection(request):
    scope=analytics_scope(request.user);window=window_from_params(request.GET);filters=report_filters(request.user,request.GET)
    return window,filters,metric_facts(facts_for(scope,window,filters),request.GET.get("metric","total"))


@login_required
@require_GET
def request_list(request):
    window,filters,facts=selection(request)
    page=Paginator(facts,25).get_page(request.GET.get("page"))
    ids=[f.request.pk for f in page]
    demand=set(program_request_scope(request.user).filter(pk__in=ids).values_list("pk",flat=True))
    provider=set(request_scope(request.user).filter(pk__in=ids).values_list("pk",flat=True))
    rows=[]
    for f in page:
        detail=reverse("request_detail",args=[f.request.pk]) if f.request.pk in demand else reverse("control_request_detail",args=[f.request.pk]) if f.request.pk in provider else None
        rows.append(dict(fact=f,detail=detail))
    return render(request,"control/analytics_requests.html",dict(rows=rows,page=page,window=window,filters=filters,metric=request.GET.get("metric","total"),
        filter_query=urlencode(params_only(request.GET,{"metric":request.GET.get("metric","total")})),export_url=report_url(request.GET,{"metric":request.GET.get("metric","total")},"analytics_excel")))


def safe_cell(value):
    return "'"+value if isinstance(value,str) and value.lstrip().startswith(("=","+","-","@")) else value


@login_required
@require_GET
def export_excel(request):
    window,filters,facts=selection(request)
    book=Workbook(write_only=True);sheet=book.create_sheet("Requests")
    sheet.append(["شناسه","تاریخ ثبت","اداره","طرح","پروژه","خانواده","خدمت","اولویت","وضعیت","درخواست‌کننده","مسئول","پاسخ اولیه (ثانیه)","زمان حل (ثانیه)","SLA پاسخ","SLA تکمیل","انتظار تأیید طرح (ثانیه)","انتظار تأیید ارشد (ثانیه)","ایجاد","تکمیل"])
    for f in facts:
        r=f.request
        values=[r.public_id,timezone.localtime(r.submitted_at).isoformat(),f.department,f.program,f.project,f.family,f.service,r.get_priority_display(),r.get_status_display(),r.requester.full_name or r.requester.username,
            (r.assigned_owner.full_name or r.assigned_owner.username) if r.assigned_owner_id else "",f.first_seconds,f.resolution_seconds,
            "بله" if f.first_sla is True else "خیر" if f.first_sla is False else "نامشخص",
            "بله" if f.completion_sla is True else "خیر" if f.completion_sla is False else "نامشخص",f.program_wait,f.senior_wait,
            timezone.localtime(r.created_at).isoformat(),timezone.localtime(r.completed_at).isoformat() if r.completed_at else ""]
        sheet.append([safe_cell(v) for v in values])
    output=BytesIO();book.save(output)
    audit(request.user,"ANALYTICS_EXCEL_EXPORTED",request.user,{"rows":len(facts),"filters":params_only(request.GET)})
    response=HttpResponse(output.getvalue(),content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"]='attachment; filename="management-report.xlsx"'
    return response


@login_required
@require_GET
def export_pdf(request):
    from .reporting_pdf import render_report
    ctx=context_for(request)
    mode=request.GET.get("mode","standard")
    if mode not in {"standard","custom"}:raise Http404
    permitted={"summary","demand","sla","department","program","service","credit","approval","appendix"}
    sections=[value for value in request.GET.getlist("sections") if value in permitted]
    if mode=="custom" and not sections:raise Http404
    if mode=="standard":sections=list(permitted-{"appendix"})
    output=render_report(ctx,sections,title="گزارش مدیریتی" if mode=="standard" else "گزارش سفارشی")
    audit(request.user,"EXECUTIVE_REPORT_GENERATED" if mode=="standard" else "CUSTOM_REPORT_GENERATED",request.user,{"mode":mode,"sections":sections,"rows":len(ctx["facts"]),"filters":params_only(request.GET)})
    response=HttpResponse(output,content_type="application/pdf");response["Content-Disposition"]='attachment; filename="management-report.pdf"'
    return response
