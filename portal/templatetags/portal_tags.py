import jdatetime
from django import template
from django.utils import timezone
register=template.Library()
def digits(value): return str(value).translate(str.maketrans("0123456789","۰۱۲۳۴۵۶۷۸۹"))
@register.filter
def jdate(value):
    if not value:return ""
    try:
        if hasattr(value,"hour"):
            if timezone.is_aware(value): value=timezone.localtime(value)
            result=jdatetime.datetime.fromgregorian(datetime=value).strftime("%Y/%m/%d %H:%M")
        else: result=jdatetime.date.fromgregorian(date=value).strftime("%Y/%m/%d")
        return digits(result)
    except Exception:return value
@register.filter
def duration_fa(value):
    try:
        seconds=max(0,int(value)); days,seconds=divmod(seconds,86400); hours,minutes=divmod(seconds,3600); minutes//=60
        parts=[]
        if days: parts.append(f"{digits(days)} روز")
        if hours: parts.append(f"{digits(hours)} ساعت")
        if minutes or not parts: parts.append(f"{digits(minutes)} دقیقه")
        return " و ".join(parts[:2])
    except Exception:return ""

@register.inclusion_tag("components/request_journey.html")
def request_journey(item, events):
    status=item.status
    index={"DRAFT":0,"SUBMITTED":1,"UNDER_REVIEW":1,"NEED_INFO":1,"ON_HOLD":1,"ACCEPTED":2,"IN_PROGRESS":3,"COMPLETED":4,"REJECTED":1,"CANCELLED":1}.get(status,0)
    stopped=status in {"REJECTED","CANCELLED"}
    stages=[{"label":label,"state":"is-stopped" if stopped and number==index else "is-current" if number==index else "is-complete" if number<index else ""} for number,label in enumerate(("ثبت درخواست","بررسی","پذیرش","اجرا","تکمیل"))]
    if item.provider_hold and not stopped:stages[1]["label"]="در انتظار تأیید"
    return {"stages":stages,"events":events}

@register.filter
def history_label(action):
    labels={"REQUEST_CREATED":"ایجاد پیش‌نویس","DRAFT_UPDATED":"ویرایش پیش‌نویس","REQUEST_SUBMITTED":"ثبت نهایی درخواست","REQUESTER_RESPONDED":"پیام درخواست‌دهنده","MANAGER_RESPONDED":"پاسخ رسیدگی‌کننده","INFORMATION_REQUESTED":"درخواست اطلاعات تکمیلی","STATUS_CHANGED":"تغییر وضعیت","OWNER_CHANGED":"تغییر مسئول رسیدگی","CLOCK_PAUSED":"توقف ساعت عملیاتی","CLOCK_RESUMED":"ادامه ساعت عملیاتی","INTERNAL_NOTE_ADDED":"ثبت یادداشت داخلی","CREDIT_RESERVE":"رزرو اعتبار","CREDIT_CONSUME":"مصرف اعتبار","CREDIT_RELEASE":"آزادسازی اعتبار","APPROVAL_REQUESTED":"درخواست تأیید","APPROVAL_APPROVED":"تأیید مرحله","APPROVAL_REJECTED":"رد مرحله","APPROVAL_CLARIFICATION_REQUESTED":"درخواست توضیح","APPROVAL_CANCELLED":"لغو تأیید","APPROVAL_SLA_PAUSED":"توقف ساعت عملیاتی برای تأیید","APPROVAL_SLA_RESUMED":"ادامه ساعت عملیاتی پس از تأیید","PROGRAM_APPROVAL_AUTO_APPROVED":"تأیید خودکار مدیر طرح","PROVIDER_APPROVAL_REQUESTED":"ارجاع تأیید از سوی ارائه‌دهنده","submit":"ثبت درخواست","review":"شروع بررسی"}
    return labels.get(action, f"رویداد: {action.replace('_', ' ')}" if action else "رویداد")

@register.simple_tag(takes_context=True)
def query_replace(context, **values):
    params=context["request"].GET.copy()
    for key,value in values.items(): params[key]=value
    return params.urlencode()
