from datetime import datetime, timedelta
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .governance import (GovernanceError, capacity_for, decide_step,
                         request_provider_approval, submit_governed_request, wallet_balance)
from .models import (AllocationPeriod, ApprovalCase, ApprovalDecision, ApprovalPolicy,
                     Category, CreditAllocation, CreditLedgerEntry, CreditReservation,
                     Department, InternalNote, PriorityPolicy, Program, Project,
                     Request, RoleAssignment, SeniorApprovalConfiguration, Service, User)
from .policies import Action, can, request_scope


class PriorityApprovalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin=User.objects.create_user(username='g-admin',role=User.Role.ADMIN,must_change_password=False)
        cls.pm=User.objects.create_user(username='g-pm',role=User.Role.USER,must_change_password=False)
        cls.pjm=User.objects.create_user(username='g-pjm',role=User.Role.USER,must_change_password=False)
        cls.provider=User.objects.create_user(username='g-provider',role=User.Role.USER,must_change_password=False)
        cls.senior=User.objects.create_user(username='g-senior',role=User.Role.USER,must_change_password=False)
        cls.outsider=User.objects.create_user(username='g-outsider',role=User.Role.USER,must_change_password=False)
        cls.program=Program.objects.create(code='g-program',name='Plan')
        cls.program.status=Program.Status.ACTIVE;cls.program.save()
        cls.other_program=Program.objects.create(code='g-other',name='Other')
        cls.other_program.status=Program.Status.ACTIVE;cls.other_program.save()
        cls.project=Project.objects.create(code='g-project',name='Project',program=cls.program)
        cls.project.status=Project.Status.ACTIVE;cls.project.save()
        cls.dept=Department.objects.create(code='g-dept',name='Dept',status=Department.Status.PUBLISHED)
        cls.dept2=Department.objects.create(code='g-dept2',name='Other Dept',status=Department.Status.PUBLISHED)
        cls.category=Category.objects.create(name='Family',slug='g-family',department=cls.dept)
        cls.category2=Category.objects.create(name='Other Family',slug='g-family',department=cls.dept2)
        cls.service=Service.objects.create(code='G-01',name='Service',category=cls.category,domain='test',full_description='test')
        cls.service2=Service.objects.create(code='G-02',name='Other Service',category=cls.category2,domain='test',full_description='test')
        RoleAssignment.objects.create(user=cls.pm,role=RoleAssignment.Role.PROGRAM_MANAGER,scope_type=RoleAssignment.ScopeType.PROGRAM,program=cls.program)
        RoleAssignment.objects.create(user=cls.pjm,role=RoleAssignment.Role.PROJECT_MANAGER,scope_type=RoleAssignment.ScopeType.PROJECT,project=cls.project,program=cls.program)
        RoleAssignment.objects.create(user=cls.provider,role=RoleAssignment.Role.REQUEST_MANAGER,scope_type=RoleAssignment.ScopeType.DEPARTMENT,department=cls.dept)
        RoleAssignment.objects.create(user=cls.senior,role=RoleAssignment.Role.SENIOR_APPROVAL_AUTHORITY,scope_type=RoleAssignment.ScopeType.GLOBAL)
        cls.period=AllocationPeriod.objects.create(name='Current',kind=AllocationPeriod.Kind.CUSTOM,
            starts_on=timezone.localdate()-timedelta(days=1),ends_on=timezone.localdate()+timedelta(days=30))
        cls.urgent=PriorityPolicy.objects.get(code='VERY_URGENT')
        cls.emergency=PriorityPolicy.objects.get(code='EMERGENCY')
        cls.wallet=CreditAllocation.objects.create(program=cls.program,department=cls.dept,priority=cls.urgent,period=cls.period,quantity=2)
        cls.wallet2=CreditAllocation.objects.create(program=cls.program,department=cls.dept2,priority=cls.urgent,period=cls.period,quantity=1)
        cls.emergency_wallet=CreditAllocation.objects.create(program=cls.program,department=cls.dept,priority=cls.emergency,period=cls.period,quantity=1)

    def draft(self,*,requester=None,service=None,priority='VERY_URGENT',role='PROJECT_MANAGER',program=None):
        requester=requester or self.pjm
        return Request.objects.create(requester=requester,requesting_unit='test',service=service or self.service,
            project='Test',title='Priority request',program=program or self.program,
            project_entity=self.project if role=='PROJECT_MANAGER' else None,
            requester_role_context=role,priority=priority,assigned_owner=self.provider if service is None else None)

    def test_project_submit_reserves_and_program_approval_consumes_once(self):
        req=self.draft()
        submitted=submit_governed_request(req.pk,self.pjm)
        self.assertTrue(submitted.provider_hold)
        self.assertEqual(submitted.credit_reservation.status,'RESERVED')
        self.assertEqual(wallet_balance(self.wallet),1)
        case=submitted.approval_cases.get()
        step=case.steps.get()
        self.assertEqual(case.status,'PENDING')
        self.assertFalse(can(self.provider,Action.REQUEST_RESPOND,resource=submitted))
        self.assertFalse(request_scope(self.provider).filter(pk=req.pk).exists())
        self.assertEqual(submitted.operational_elapsed_seconds,0)
        decide_step(step.pk,self.pm,'APPROVED')
        req.refresh_from_db();case.refresh_from_db()
        self.assertFalse(req.provider_hold)
        self.assertEqual(case.status,'APPROVED')
        self.assertEqual(req.credit_reservation.status,'CONSUMED')
        self.assertEqual(wallet_balance(self.wallet),1)
        self.assertEqual(list(req.credit_reservation.ledger_entries.order_by('pk').values_list('event',flat=True)),['RESERVE','CONSUME'])
        with self.assertRaises(GovernanceError):decide_step(step.pk,self.pm,'APPROVED')
        self.assertEqual(CreditLedgerEntry.objects.filter(reservation=req.credit_reservation).count(),2)

    def test_last_credit_confirmation_and_no_oversubscription(self):
        first=self.draft(priority='EMERGENCY')
        with self.assertRaisesMessage(GovernanceError,'آخرین اعتبار'):
            submit_governed_request(first.pk,self.pjm)
        first.refresh_from_db();self.assertEqual(first.status,'DRAFT');self.assertFalse(CreditReservation.objects.filter(request=first).exists())
        submit_governed_request(first.pk,self.pjm,confirm_last=True)
        second=self.draft(priority='EMERGENCY')
        with self.assertRaisesMessage(GovernanceError,'تمام شده'):
            submit_governed_request(second.pk,self.pjm,confirm_last=True)
        self.assertEqual(self.emergency_wallet.reservations.count(),1)

    def test_department_wallets_and_program_manager_share_capacity(self):
        req=self.draft()
        submit_governed_request(req.pk,self.pjm)
        self.assertEqual(capacity_for(self.program.pk,self.dept.pk,'VERY_URGENT'),1)
        self.assertEqual(capacity_for(self.program.pk,self.dept2.pk,'VERY_URGENT'),1)
        own=self.draft(requester=self.pm,service=self.service2,role='PROGRAM_MANAGER')
        submit_governed_request(own.pk,self.pm,confirm_last=True)
        self.assertEqual(own.credit_reservation.status,'CONSUMED')
        self.assertEqual(own.approval_cases.get().steps.get().decisions.get().outcome,'AUTO_APPROVED')
        self.assertTrue(own.history.filter(action='PROGRAM_APPROVAL_AUTO_APPROVED').exists())
        self.assertEqual(capacity_for(self.program.pk,self.dept2.pk,'VERY_URGENT'),0)
        self.assertEqual(capacity_for(self.program.pk,self.dept.pk,'VERY_URGENT'),1)

    def test_rejection_and_cancellation_release_reserved_but_not_consumed(self):
        req=self.draft()
        submit_governed_request(req.pk,self.pjm)
        step=req.approval_cases.get().steps.get()
        decide_step(step.pk,self.pm,'REJECTED','not approved')
        req.refresh_from_db();self.assertEqual(req.status,'REJECTED')
        self.assertEqual(req.credit_reservation.status,'RELEASED')
        self.assertEqual(wallet_balance(self.wallet),2)
        another=self.draft()
        submit_governed_request(another.pk,self.pjm)
        from .governance import cancel_pending_approval
        cancel_pending_approval(another.pk,self.pjm)
        another.refresh_from_db();self.assertEqual(another.status,'CANCELLED')
        self.assertEqual(another.credit_reservation.status,'RELEASED')
        self.assertEqual(wallet_balance(self.wallet),2)
        self.assertIsNone(cancel_pending_approval(another.pk,self.pjm))
        own=self.draft(requester=self.pm,role='PROGRAM_MANAGER')
        submit_governed_request(own.pk,self.pm)
        self.assertEqual(own.credit_reservation.status,'CONSUMED')
        with self.assertRaises(GovernanceError):
            from .governance import _resolve_credit
            _resolve_credit(own,self.pm,'RELEASED')

    def test_self_approval_cross_program_and_inactive_assignment_denied(self):
        req=self.draft()
        submit_governed_request(req.pk,self.pjm)
        step=req.approval_cases.get().steps.get()
        RoleAssignment.objects.create(user=self.pjm,role=RoleAssignment.Role.PROGRAM_MANAGER,scope_type=RoleAssignment.ScopeType.PROGRAM,program=self.program)
        with self.assertRaises(GovernanceError):decide_step(step.pk,self.pjm,'APPROVED')
        with self.assertRaises(GovernanceError):decide_step(step.pk,self.outsider,'APPROVED')
        other_pm=User.objects.create_user(username='g-otherpm',must_change_password=False)
        RoleAssignment.objects.create(user=other_pm,role=RoleAssignment.Role.PROGRAM_MANAGER,scope_type=RoleAssignment.ScopeType.PROGRAM,program=self.other_program)
        with self.assertRaises(GovernanceError):decide_step(step.pk,other_pm,'APPROVED')
        assignment=RoleAssignment.objects.get(user=self.pm,role=RoleAssignment.Role.PROGRAM_MANAGER)
        assignment.is_active=False;assignment.save()
        with self.assertRaises(GovernanceError):decide_step(step.pk,self.pm,'APPROVED')

    def test_provider_to_senior_approval_and_private_attachment(self):
        req=self.draft(requester=self.pm,role='PROGRAM_MANAGER',priority='NORMAL')
        submit_governed_request(req.pk,self.pm)
        upload=SimpleUploadedFile('private.txt',b'confidential',content_type='text/plain')
        case=request_provider_approval(req.pk,self.provider,target='SENIOR',details={'reason':'Oversight','assessment':'Private assessment'},attachment=upload)
        step=case.steps.get()
        self.client.force_login(self.pm)
        self.assertEqual(self.client.get(reverse('approval_detail',args=[case.pk])).status_code,404)
        self.assertEqual(self.client.get(reverse('approval_attachment_download',args=[case.attachments.get().pk])).status_code,404)
        self.assertNotContains(self.client.get(reverse('request_detail',args=[req.pk])),'Private assessment')
        self.client.force_login(self.senior)
        self.assertContains(self.client.get(reverse('approval_detail',args=[case.pk])),'Private assessment')
        self.assertEqual(self.client.get(reverse('approval_attachment_download',args=[case.attachments.get().pk])).status_code,200)
        self.assertContains(self.client.get(reverse('approval_inbox')),'Oversight' if False else req.public_id)
        notification=self.senior.notifications.filter(request=req).latest('pk')
        self.assertEqual(self.client.post(reverse('notification_read',args=[notification.pk])).url,reverse('approval_detail',args=[case.pk]))
        decide_step(step.pk,self.senior,'CLARIFICATION_REQUESTED','Need a detail')
        case.refresh_from_db();self.assertEqual(case.status,'CLARIFICATION_REQUESTED')
        with self.assertRaises(GovernanceError):decide_step(step.pk,self.senior,'APPROVED')
        self.client.force_login(self.provider)
        self.assertEqual(self.client.post(reverse('approval_clarify',args=[case.pk]),{'body':'Further assessment'}).status_code,302)
        case.refresh_from_db();self.assertEqual(case.status,'PENDING')
        self.client.force_login(self.senior)
        self.assertContains(self.client.get(reverse('approval_detail',args=[case.pk])),'Further assessment')
        decide_step(step.pk,self.senior,'APPROVED')
        case.refresh_from_db();self.assertEqual(case.status,'APPROVED')
        self.assertEqual(ApprovalDecision.objects.filter(step=step).count(),2)
        self.assertFalse(CreditReservation.objects.filter(request=req).exists())

    def test_provider_self_approval_and_cross_department_denied(self):
        req=self.draft(requester=self.pjm,role='PROJECT_MANAGER',priority='NORMAL')
        submit_governed_request(req.pk,self.pjm)
        pm_case=req.approval_cases.get()
        decide_step(pm_case.steps.get().pk,self.pm,'APPROVED')
        req.refresh_from_db()
        case=request_provider_approval(req.pk,self.provider,target='PROGRAM_MANAGER',details={'reason':'Check'})
        RoleAssignment.objects.create(user=self.provider,role=RoleAssignment.Role.PROGRAM_MANAGER,scope_type=RoleAssignment.ScopeType.PROGRAM,program=self.program)
        with self.assertRaises(GovernanceError):decide_step(case.steps.get().pk,self.provider,'APPROVED')
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(reverse('provider_approval',args=[req.pk]),{'target':'SENIOR','reason':'forged'}).status_code,404)
        self.assertEqual(self.client.post(reverse('approval_decide',args=[case.steps.get().pk]),{'outcome':'APPROVED'}).status_code,404)
        self.assertEqual(self.client.get(reverse('capacity_dashboard')).status_code,404)

    def test_admin_capacity_and_scoped_read_only(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('capacity_dashboard')),'Plan')
        self.client.force_login(self.pm)
        self.assertContains(self.client.get(reverse('capacity_dashboard')),'Plan')
        self.assertEqual(self.client.get(reverse('manage_credit_allocation_edit',args=[self.wallet.pk])).status_code,404)
        self.client.force_login(self.pjm)
        self.assertContains(self.client.get(reverse('capacity_dashboard')),'Plan')

    def test_capacity_endpoint_is_scoped_and_server_revalidates(self):
        self.client.force_login(self.pjm)
        url=reverse('capacity_balance')
        query={'priority':'VERY_URGENT','service':str(self.service.pk),'role':'PROJECT_MANAGER','project':str(self.project.pk)}
        self.assertEqual(self.client.get(url,query).json()['remaining'],2)
        query['project']='999999'
        self.assertEqual(self.client.get(url,query).status_code,404)
        req=self.draft()
        assignment=RoleAssignment.objects.get(user=self.pjm,role=RoleAssignment.Role.PROJECT_MANAGER)
        assignment.is_active=False;assignment.save()
        with self.assertRaises(GovernanceError):submit_governed_request(req.pk,self.pjm)

    def test_period_overlap_and_quantity_cannot_undercut_commitments(self):
        overlap=AllocationPeriod.objects.create(name='Overlap',kind='CUSTOM',starts_on=timezone.localdate(),ends_on=timezone.localdate()+timedelta(days=3))
        with self.assertRaises(ValidationError):CreditAllocation.objects.create(program=self.program,department=self.dept,priority=self.urgent,period=overlap,quantity=4)
        req=self.draft();submit_governed_request(req.pk,self.pjm)
        self.wallet.quantity=0
        with self.assertRaises(ValidationError):self.wallet.save()

    def test_existing_legacy_priority_and_request_stay_unchanged(self):
        legacy=Request.objects.create(requester=self.outsider,requesting_unit='test',service=self.service,project='Historical',title='Legacy',priority='URGENT',status='SUBMITTED')
        self.assertEqual(legacy.priority,'URGENT')
        self.assertFalse(CreditReservation.objects.filter(request=legacy).exists())
        self.assertEqual(legacy.approval_cases.count(),0)

    def test_multistage_approval_delays_consumption_until_final_step(self):
        ApprovalPolicy.objects.create(name='Senior after PM',trigger='PRIORITY',priority=self.urgent,
            requester_role='PROJECT_MANAGER',target='SENIOR',sequence=2,pauses_sla=False)
        req=self.draft();submit_governed_request(req.pk,self.pjm)
        case=req.approval_cases.get();first,second=case.steps.all()
        decide_step(first.pk,self.pm,'APPROVED')
        second.refresh_from_db();self.assertEqual(second.status,'PENDING')
        self.assertFalse(second.pauses_sla)
        req.refresh_from_db();self.assertTrue(req.provider_hold)
        self.assertEqual(req.credit_reservation.status,'RESERVED')
        self.client.force_login(self.senior)
        self.assertEqual(self.client.post(reverse('approval_decide',args=[second.pk]),{'outcome':'APPROVED'}).status_code,302)
        req.refresh_from_db();self.assertFalse(req.provider_hold)
        self.assertEqual(req.credit_reservation.status,'CONSUMED')

    def test_request_form_offers_configured_priority_and_http_submit_requires_confirmation(self):
        self.client.force_login(self.pjm)
        form=self.client.get(reverse('request_create',args=[self.service.pk]))
        self.assertContains(form,'VERY_URGENT')
        self.assertContains(form,'data-capacity-url')
        req=self.draft(priority='EMERGENCY')
        result=self.client.post(reverse('submit_request',args=[req.pk]),follow=True)
        req.refresh_from_db();self.assertEqual(req.status,'DRAFT')
        self.assertContains(result,'آخرین اعتبار')
        self.client.post(reverse('submit_request',args=[req.pk]),{'confirm_last_credit':'1'})
        req.refresh_from_db();self.assertEqual(req.status,'SUBMITTED')

    def test_internal_notes_and_cross_department_attachment_remain_private(self):
        req=self.draft(requester=self.pm,role='PROGRAM_MANAGER',priority='NORMAL')
        submit_governed_request(req.pk,self.pm)
        InternalNote.objects.create(request=req,author=self.provider,body='Internal only')
        self.client.force_login(self.pm)
        self.assertNotContains(self.client.get(reverse('request_detail',args=[req.pk])),'Internal only')
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('request_detail',args=[req.pk])).status_code,404)

    def test_senior_deactivation_revokes_decision(self):
        req=self.draft(requester=self.pm,role='PROGRAM_MANAGER',priority='NORMAL')
        submit_governed_request(req.pk,self.pm)
        case=request_provider_approval(req.pk,self.provider,target='SENIOR',details={'reason':'Review'})
        assignment=RoleAssignment.objects.get(user=self.senior,role=RoleAssignment.Role.SENIOR_APPROVAL_AUTHORITY)
        assignment.is_active=False;assignment.save()
        with self.assertRaises(GovernanceError):decide_step(case.steps.get().pk,self.senior,'APPROVED')

    def test_provider_referral_cannot_target_only_original_requester(self):
        req=self.draft(requester=self.pm,role='PROGRAM_MANAGER',priority='NORMAL')
        submit_governed_request(req.pk,self.pm)
        with self.assertRaises(GovernanceError):
            request_provider_approval(req.pk,self.provider,target='PROGRAM_MANAGER',details={'reason':'Review'})
        self.assertFalse(req.approval_cases.exists())

    def test_approval_pause_extends_effective_initial_response_deadline(self):
        req=self.draft();submit_governed_request(req.pk,self.pjm)
        step=req.approval_cases.get().steps.get()
        monday=timezone.make_aware(datetime(2026,9,28,10))
        step.started_at=monday;step.decided_at=monday+timedelta(hours=1);step.status='APPROVED';step.save()
        req.expected_initial_response_at=monday+timedelta(hours=2);req.save(update_fields=['expected_initial_response_at'])
        self.assertEqual(req.approval_paused_working_seconds,3600)
        self.assertEqual(req.effective_initial_response_at,monday+timedelta(hours=3))

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.db import connection, close_old_connections
from django.test import TransactionTestCase


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL transaction locking requires PostgreSQL test service')
class PostgreSQLLastCreditRaceTests(TransactionTestCase):
    def test_two_simultaneous_submissions_reserve_one_credit(self):
        admin=User.objects.create_user(username='race-admin',role=User.Role.ADMIN)
        department=Department.objects.create(code='race-dept',name='Race Dept',status='PUBLISHED')
        category=Category.objects.create(name='Race Family',slug='race-family',department=department)
        service=Service.objects.create(code='RACE',name='Race Service',category=category,domain='test',full_description='test')
        program=Program.objects.create(code='race-program',name='Race Program')
        program.status=Program.Status.ACTIVE;program.save()
        period=AllocationPeriod.objects.create(name='Race',kind='CUSTOM',starts_on=timezone.localdate(),ends_on=timezone.localdate()+timedelta(days=3))
        CreditAllocation.objects.create(program=program,department=department,priority=PriorityPolicy.objects.get(code='VERY_URGENT'),period=period,quantity=1)
        users=[];requests=[]
        for i in range(2):
            user=User.objects.create_user(username=f'race-{i}')
            RoleAssignment.objects.create(user=user,role=RoleAssignment.Role.PROGRAM_MANAGER,scope_type=RoleAssignment.ScopeType.PROGRAM,program=program)
            users.append(user)
            requests.append(Request.objects.create(requester=user,requesting_unit='test',service=service,project='race',title='Race',program=program,requester_role_context='PROGRAM_MANAGER',priority='VERY_URGENT'))
        barrier=Barrier(2)
        def submit(index):
            close_old_connections()
            barrier.wait()
            try:
                submit_governed_request(requests[index].pk,users[index],confirm_last=True)
                return 'success'
            except GovernanceError:
                return 'no-credit'
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            result=list(pool.map(submit,range(2)))
        self.assertCountEqual(result,['success','no-credit'])
        self.assertEqual(CreditReservation.objects.count(),1)
        self.assertEqual(Request.objects.filter(status=Request.Status.SUBMITTED).count(),1)
