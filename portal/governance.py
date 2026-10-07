"""Transactional V2.3 credit and approval domain operations."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import (AllocationPeriod, ApprovalAttachment, ApprovalCase, ApprovalClarification, ApprovalDecision, ApprovalPolicy,
                     ApprovalStep, CreditAllocation, CreditLedgerEntry,
                     CreditReservation, PriorityPolicy, Request, RoleAssignment, SeniorApprovalConfiguration)
from .policies import Action, can, is_super_admin, program_ids_for_role
from .utils import history, notify


class GovernanceError(ValueError):
    pass


def active_wallet(program_id, department_id, priority_code, *, day=None, lock=False):
    day=day or timezone.localdate()
    qs=CreditAllocation.objects.filter(program_id=program_id,department_id=department_id,
        priority__code=priority_code,priority__is_active=True,is_active=True,
        period__is_active=True,period__starts_on__lte=day,period__ends_on__gte=day)
    if lock:qs=qs.select_for_update()
    wallets=list(qs.order_by('pk')[:2])
    if len(wallets)!=1:raise GovernanceError('اعتبار فعال و یکتای این طرح، اداره و اولویت تعریف نشده است.')
    return wallets[0]


def wallet_balance(wallet):
    counts=wallet.reservations.aggregate(reserved=Count('pk',filter=Q(status=CreditReservation.Status.RESERVED)),
        consumed=Count('pk',filter=Q(status=CreditReservation.Status.CONSUMED)))
    return wallet.quantity-counts['reserved']-counts['consumed']


def capacity_for(program_id, department_id, priority_code):
    try:
        wallet=active_wallet(program_id,department_id,priority_code)
    except GovernanceError:return None
    return wallet_balance(wallet)


def _ledger(reservation,event,actor):
    CreditLedgerEntry.objects.create(reservation=reservation,event=event,actor=actor,
        metadata={'allocation':reservation.allocation_id,'request':reservation.request.public_id})
    history(reservation.request,actor,'CREDIT_'+event,metadata={'allocation_id':reservation.allocation_id})


def _resolve_credit(req,actor,event):
    try:reservation=CreditReservation.objects.select_for_update().select_related('allocation').get(request=req)
    except CreditReservation.DoesNotExist:return
    # Lock the wallet first on every entry point that changes its balance.
    wallet=CreditAllocation.objects.select_for_update().get(pk=reservation.allocation_id)
    if reservation.status!=CreditReservation.Status.RESERVED:
        if reservation.status==event:return
        raise GovernanceError('تغییر مجدد اعتبار مصرف‌شده یا آزادشده مجاز نیست.')
    reservation.status=event;reservation.resolved_at=timezone.now();reservation.save(update_fields=['status','resolved_at','updated_at'])
    _ledger(reservation,CreditLedgerEntry.Event.CONSUME if event=='CONSUMED' else CreditLedgerEntry.Event.RELEASE,actor)
    return wallet


def _approvers(step):
    if step.target==ApprovalPolicy.Target.PROGRAM_MANAGER:
        return RoleAssignment.objects.filter(role=RoleAssignment.Role.PROGRAM_MANAGER,
            scope_type=RoleAssignment.ScopeType.PROGRAM,program_id=step.request.program_id,
            is_active=True,user__is_active=True).select_related('user')
    config_active=SeniorApprovalConfiguration.objects.filter(pk=1,is_active=True).exists()
    if not config_active:return RoleAssignment.objects.none()
    return RoleAssignment.objects.filter(role=RoleAssignment.Role.SENIOR_APPROVAL_AUTHORITY,
        scope_type=RoleAssignment.ScopeType.GLOBAL,is_active=True,user__is_active=True).select_related('user')


def may_decide(user,step):
    if not user.is_authenticated or not user.is_active or not step.request.program_id:return False
    if step.request.requester_id==user.pk:return False
    if step.case.requested_by_id==user.pk and step.case.trigger==ApprovalPolicy.Trigger.PROVIDER:return False
    if step.target==ApprovalPolicy.Target.PROGRAM_MANAGER:
        return step.request.program_id in program_ids_for_role(user)
    return SeniorApprovalConfiguration.objects.filter(pk=1,is_active=True).exists() and RoleAssignment.objects.filter(
        user=user,role=RoleAssignment.Role.SENIOR_APPROVAL_AUTHORITY,scope_type=RoleAssignment.ScopeType.GLOBAL,is_active=True).exists()


def may_view_case(user,case):
    req=case.request
    if not user.is_authenticated or not user.is_active:return False
    if is_super_admin(user):return True
    if case.requested_by_id==user.pk:return True
    if can(user,Action.REQUEST_VIEW_DEPARTMENT,resource=req):return True
    return any(may_decide(user,step) for step in case.steps.all())


def _notify_step(step):
    for assignment in _approvers(step):
        if may_decide(assignment.user,step):notify(assignment.user,'تأیید در انتظار',f'درخواست {step.request.public_id} در صندوق تأیید شماست.',step.request)


def _start_step(step,actor):
    step.status=ApprovalStep.Status.PENDING;step.started_at=timezone.now();step.save(update_fields=['status','started_at','updated_at'])
    history(step.request,actor,'APPROVAL_REQUESTED',metadata={'case_id':step.case_id,'step_id':step.pk,'target':step.target})
    if step.pauses_sla:history(step.request,actor,'APPROVAL_SLA_PAUSED',metadata={'step_id':step.pk})
    _notify_step(step)


def _make_case(req,actor,trigger,policies,**details):
    snapshot={'request':req.public_id,'requester':req.requester_id,'requester_role':req.requester_role_at_submission,
        'program':req.program_id,'program_name':req.program_name_snapshot,'project':req.project_entity_id,
        'project_name':req.project_name_snapshot,'department':req.department_id,'service':req.service_id,
        'priority':req.priority,'created_at':timezone.now().isoformat()}
    case=ApprovalCase.objects.create(request=req,requested_by=actor,trigger=trigger,context_snapshot=snapshot,**details)
    for index,policy in enumerate(policies,1):
        ApprovalStep.objects.create(case=case,request=req,policy=policy,sequence=index,
            target=policy.target,pauses_sla=policy.pauses_sla,
            target_snapshot={'target':policy.target,'policy':policy.name,'program':req.program_id})
    first=case.steps.first()
    if first:_start_step(first,actor)
    return case


def _record_auto_approval(req,actor):
    now=timezone.now()
    case=ApprovalCase.objects.create(request=req,requested_by=actor,trigger=ApprovalPolicy.Trigger.PRIORITY,
        status=ApprovalCase.Status.APPROVED,decided_at=now,
        context_snapshot={'request':req.public_id,'program':req.program_id,'project':req.project_entity_id,
            'department':req.department_id,'service':req.service_id,'priority':req.priority,
            'requester_role':req.requester_role_at_submission,'auto_approved':True})
    step=ApprovalStep.objects.create(case=case,request=req,sequence=1,target=ApprovalPolicy.Target.PROGRAM_MANAGER,
        status=ApprovalStep.Status.APPROVED,pauses_sla=False,started_at=now,decided_at=now,decided_by=actor,
        target_snapshot={'target':ApprovalPolicy.Target.PROGRAM_MANAGER,'auto_approved':True})
    ApprovalDecision.objects.create(step=step,actor=actor,outcome='AUTO_APPROVED',reason='Program Manager submitted own request')
    history(req,actor,'PROGRAM_APPROVAL_AUTO_APPROVED',metadata={'case_id':case.pk,'step_id':step.pk})


@transaction.atomic
def submit_governed_request(req_id,actor,*,confirm_last=False):
    req=Request.objects.select_for_update().select_related('service__category__department','program','project_entity').get(pk=req_id)
    if req.requester_id!=actor.pk or req.status!=Request.Status.DRAFT:raise GovernanceError('درخواست فقط در حالت پیش‌نویس توسط مالک قابل ثبت است.')
    from .policies import can_use_request_context, can, Action
    from .forms import request_readiness_errors
    from .utils import apply_submission_timing
    if not req.service.is_requestable or not can(actor,Action.REQUEST_CREATE,department=req.department) or not can_use_request_context(actor,req):
        raise GovernanceError('خدمت یا زمینهٔ سازمانی دیگر فعال و مجاز نیست.')
    missing=request_readiness_errors(req)
    if missing:raise GovernanceError('موارد لازم ناقص است: '+', '.join(missing))
    policy=PriorityPolicy.objects.filter(code=req.priority,is_active=True).first()
    if not policy and req.priority not in {Request.Priority.LOW,Request.Priority.URGENT}:
        raise GovernanceError('سیاست اولویت فعال نیست.')
    policies=[]
    if req.requester_role_context==RoleAssignment.Role.PROJECT_MANAGER:
        if not req.program_id:raise GovernanceError('ثبت درخواست مدیر پروژه بدون طرح مجاز نیست.')
        gate_policy=policy or PriorityPolicy.objects.filter(code=req.priority,is_active=True).first()
        if gate_policy:
            policies=list(ApprovalPolicy.objects.filter(trigger=ApprovalPolicy.Trigger.PRIORITY,priority=gate_policy,
                requester_role=RoleAssignment.Role.PROJECT_MANAGER,is_active=True).order_by('sequence','pk'))
        if not policies:raise GovernanceError('تأیید مدیر طرح برای درخواست مدیر پروژه تنظیم نشده است.')
        if not RoleAssignment.objects.filter(role=RoleAssignment.Role.PROGRAM_MANAGER,scope_type=RoleAssignment.ScopeType.PROGRAM,program_id=req.program_id,is_active=True,user__is_active=True).exclude(user=actor).exists():
            raise GovernanceError('مدیر طرح فعال و مستقلی برای تأیید وجود ندارد.')
    if policy and policy.requires_credit:
        if not req.program_id:raise GovernanceError('برای اولویت اعتباری انتخاب طرح الزامی است.')
        wallet=active_wallet(req.program_id,req.department_id,req.priority,lock=True)
        remaining=wallet_balance(wallet)
        if remaining<=0:raise GovernanceError('اعتبار این طرح و اداره برای اولویت انتخاب‌شده تمام شده است.')
        if remaining==1 and not confirm_last:raise GovernanceError('برای رزرو آخرین اعتبار، تأیید صریح لازم است.')
        reservation=CreditReservation.objects.create(request=req,allocation=wallet)
        _ledger(reservation,CreditLedgerEntry.Event.RESERVE,actor)
    req.requester_role_at_submission=req.requester_role_context or 'REQUESTER'
    if req.program_id:req.program_name_snapshot=req.program.name;req.program_code_snapshot=req.program.code
    if req.project_entity_id:req.project_name_snapshot=req.project_entity.name;req.project_code_snapshot=req.project_entity.code
    apply_submission_timing(req);req.status=Request.Status.SUBMITTED;req.provider_hold=bool(policies);req.save()
    history(req,actor,'REQUEST_SUBMITTED','DRAFT','SUBMITTED',{'priority':req.priority,'program':req.program_code_snapshot,'project':req.project_code_snapshot})
    if policies:
        _make_case(req,actor,ApprovalPolicy.Trigger.PRIORITY,policies)
    elif policy and policy.requires_credit:
        _resolve_credit(req,actor,CreditReservation.Status.CONSUMED)
        if policy.requires_approval:
            _record_auto_approval(req,actor)
    if not req.provider_hold:notify(req.assigned_owner,'درخواست جدید',f'درخواست {req.public_id} ثبت شد.',req)
    return req


@transaction.atomic
def request_provider_approval(req_id,actor,*,target,details,attachment=None):
    req=Request.objects.select_for_update().select_related('requester').get(pk=req_id)
    from .policies import Action,can
    if not can(actor,Action.REQUEST_RESPOND,resource=req) or req.provider_hold or req.status in {Request.Status.DRAFT,Request.Status.REJECTED,Request.Status.CANCELLED,Request.Status.COMPLETED}:
        raise GovernanceError('ارجاع این درخواست مجاز نیست.')
    if not req.program_id:raise GovernanceError('ارجاع نیازمند زمینهٔ طرح است.')
    if req.approval_cases.filter(trigger=ApprovalPolicy.Trigger.PROVIDER,status__in=[ApprovalCase.Status.PENDING,ApprovalCase.Status.CLARIFICATION_REQUESTED]).exists():
        raise GovernanceError('یک ارجاع تأیید باز برای این درخواست وجود دارد.')
    policies=list(ApprovalPolicy.objects.filter(trigger=ApprovalPolicy.Trigger.PROVIDER,target=target,is_active=True).order_by('sequence','pk'))
    if not policies:raise GovernanceError('سیاست تأیید برای مقصد انتخاب‌شده فعال نیست.')
    if target==ApprovalPolicy.Target.PROGRAM_MANAGER and not RoleAssignment.objects.filter(role=RoleAssignment.Role.PROGRAM_MANAGER,program_id=req.program_id,is_active=True,user__is_active=True).exclude(user=actor).exclude(user=req.requester).exists():
        raise GovernanceError('مدیر طرح فعالی برای تصمیم وجود ندارد.')
    if target==ApprovalPolicy.Target.SENIOR:
        config=SeniorApprovalConfiguration.objects.filter(pk=1,is_active=True).exists()
        if not config or not RoleAssignment.objects.filter(role=RoleAssignment.Role.SENIOR_APPROVAL_AUTHORITY,is_active=True,user__is_active=True).exclude(user=actor).exclude(user=req.requester).exists():
            raise GovernanceError('مرجع تأیید ارشد فعال و دارای عضو نیست.')
    case=_make_case(req,actor,ApprovalPolicy.Trigger.PROVIDER,policies,**details)
    if attachment:
        from .models import ApprovalAttachment
        ApprovalAttachment.objects.create(case=case,uploaded_by=actor,file=attachment,original_name=attachment.name,size=attachment.size,content_type=getattr(attachment,'content_type','')[:120])
    history(req,actor,'PROVIDER_APPROVAL_REQUESTED',metadata={'case_id':case.pk,'target':target})
    return case


def may_clarify(user,case):
    if not user.is_authenticated or not user.is_active or case.status!=ApprovalCase.Status.CLARIFICATION_REQUESTED or case.requested_by_id!=user.pk:return False
    if case.trigger==ApprovalPolicy.Trigger.PRIORITY:return case.request.requester_id==user.pk
    from .policies import Action,can
    return can(user,Action.REQUEST_RESPOND,resource=case.request)


@transaction.atomic
def provide_clarification(case_id,actor,body,attachment=None):
    source=ApprovalCase.objects.select_related('request').get(pk=case_id)
    req=Request.objects.select_for_update().get(pk=source.request_id)
    case=ApprovalCase.objects.select_for_update().get(pk=case_id)
    if not may_clarify(actor,case) or not body.strip():raise GovernanceError('توضیح برای این تأیید مجاز یا کامل نیست.')
    step=ApprovalStep.objects.select_for_update().get(case=case,status=ApprovalStep.Status.CLARIFICATION_REQUESTED)
    ApprovalClarification.objects.create(case=case,author=actor,body=body.strip())
    if attachment:
        ApprovalAttachment.objects.create(case=case,uploaded_by=actor,file=attachment,
            original_name=attachment.name,size=attachment.size,content_type=getattr(attachment,'content_type','')[:120])
    step.status=ApprovalStep.Status.PENDING;step.save(update_fields=['status','updated_at'])
    case.status=ApprovalCase.Status.PENDING;case.save(update_fields=['status','updated_at'])
    history(req,actor,'APPROVAL_CLARIFICATION_PROVIDED',metadata={'case_id':case.pk,'step_id':step.pk})
    _notify_step(step)
    return case


@transaction.atomic
def decide_step(step_id,actor,outcome,reason=''):
    step=ApprovalStep.objects.select_related('case__request').get(pk=step_id)
    req=Request.objects.select_for_update().get(pk=step.request_id)
    case=ApprovalCase.objects.select_for_update().get(pk=step.case_id)
    step=ApprovalStep.objects.select_for_update().get(pk=step_id)
    if step.status not in {ApprovalStep.Status.PENDING,ApprovalStep.Status.CLARIFICATION_REQUESTED} or case.status not in {ApprovalCase.Status.PENDING,ApprovalCase.Status.CLARIFICATION_REQUESTED}:
        raise GovernanceError('این مرحله قبلاً تصمیم‌گیری شده است.')
    if step.status==ApprovalStep.Status.CLARIFICATION_REQUESTED:
        raise GovernanceError('ابتدا توضیح تکمیلی باید توسط ارجاع‌دهنده ثبت شود.')
    if not may_decide(actor,step):raise GovernanceError('مجوز تصمیم‌گیری در این محدوده وجود ندارد.')
    if outcome not in {ApprovalStep.Status.APPROVED,ApprovalStep.Status.REJECTED,ApprovalStep.Status.CLARIFICATION_REQUESTED}:
        raise GovernanceError('تصمیم نامعتبر است.')
    if outcome!=ApprovalStep.Status.APPROVED and not reason.strip():raise GovernanceError('برای رد یا درخواست توضیح ثبت دلیل الزامی است.')
    if outcome==ApprovalStep.Status.CLARIFICATION_REQUESTED and step.status==outcome:raise GovernanceError('درخواست توضیح قبلاً ثبت شده است.')
    ApprovalDecision.objects.create(step=step,actor=actor,outcome=outcome,reason=reason.strip())
    step.status=outcome;step.decision_reason=reason.strip()
    if outcome!=ApprovalStep.Status.CLARIFICATION_REQUESTED:
        step.decided_at=timezone.now();step.decided_by=actor
    step.save()
    history(req,actor,'APPROVAL_'+outcome,metadata={'case_id':case.pk,'step_id':step.pk,'reason':reason.strip(),'target':step.target})
    if outcome==ApprovalStep.Status.CLARIFICATION_REQUESTED:
        case.status=ApprovalCase.Status.CLARIFICATION_REQUESTED;case.save(update_fields=['status','updated_at'])
        notify(case.requested_by,'توضیح برای تأیید',f'برای {req.public_id} توضیح تکمیلی درخواست شده است.',req)
        return case
    if step.pauses_sla:history(req,actor,'APPROVAL_SLA_RESUMED',metadata={'step_id':step.pk})
    if outcome==ApprovalStep.Status.REJECTED:
        case.status=ApprovalCase.Status.REJECTED;case.decided_at=timezone.now();case.save()
        case.steps.filter(status=ApprovalStep.Status.BLOCKED).update(status=ApprovalStep.Status.CANCELLED)
        if case.trigger==ApprovalPolicy.Trigger.PRIORITY:
            _resolve_credit(req,actor,CreditReservation.Status.RELEASED)
            req.status=Request.Status.REJECTED;req.provider_hold=True;req.save(update_fields=['status','provider_hold','updated_at'])
            history(req,actor,'STATUS_CHANGED','SUBMITTED','REJECTED',{'reason':reason.strip()})
        notify(req.requester,'تأیید رد شد',f'تأیید {req.public_id} رد شد.',req)
        notify(case.requested_by,'تأیید رد شد',f'تأیید {req.public_id} رد شد.',req)
        return case
    nxt=case.steps.filter(status=ApprovalStep.Status.BLOCKED).order_by('sequence').first()
    if nxt:
        case.status=ApprovalCase.Status.PENDING;case.save(update_fields=['status','updated_at']);_start_step(nxt,actor)
    else:
        case.status=ApprovalCase.Status.APPROVED;case.decided_at=timezone.now();case.save()
        if case.trigger==ApprovalPolicy.Trigger.PRIORITY:
            _resolve_credit(req,actor,CreditReservation.Status.CONSUMED)
            req.provider_hold=False;req.save(update_fields=['provider_hold','updated_at'])
            notify(req.assigned_owner,'درخواست تأییدشده',f'درخواست {req.public_id} برای رسیدگی آماده است.',req)
        notify(case.requested_by,'تأیید انجام شد',f'تأیید {req.public_id} انجام شد.',req)
    return case


@transaction.atomic
def cancel_pending_approval(req_id,actor):
    req=Request.objects.select_for_update().get(pk=req_id)
    case=req.approval_cases.select_for_update().filter(trigger=ApprovalPolicy.Trigger.PRIORITY,status__in=[ApprovalCase.Status.PENDING,ApprovalCase.Status.CLARIFICATION_REQUESTED]).first()
    if not case:return
    if actor.pk!=req.requester_id and not is_super_admin(actor):raise GovernanceError('مجوز لغو وجود ندارد.')
    now=timezone.now()
    case.steps.filter(status__in=[ApprovalStep.Status.PENDING,ApprovalStep.Status.CLARIFICATION_REQUESTED]).update(status=ApprovalStep.Status.CANCELLED,decided_at=now)
    case.steps.filter(status=ApprovalStep.Status.BLOCKED).update(status=ApprovalStep.Status.CANCELLED)
    case.status=ApprovalCase.Status.CANCELLED;case.decided_at=now;case.save()
    _resolve_credit(req,actor,CreditReservation.Status.RELEASED)
    req.status=Request.Status.CANCELLED;req.provider_hold=True;req.save(update_fields=['status','provider_hold','updated_at'])
    history(req,actor,'APPROVAL_CANCELLED',metadata={'case_id':case.pk})
    return case
