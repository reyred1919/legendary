import struct
import tempfile
import zlib
from html.parser import HTMLParser

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.crypto import get_random_string

from .appearance import DEFAULT_ACCENT, DEFAULT_NAME, DEFAULT_PRIMARY
from .models import AppearanceSetting, Attachment, Category, Department, InternalNote, Request, RequestHistory, RequestResponse, RoleAssignment, Service, ServiceFormField, User


def password():
    return f"{get_random_string(24)}aA9!"


def png_chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png_image():
    raw = b"".join(b"\x00" + b"\x00\x55\x88" * 16 for _ in range(16))
    return b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0)) + png_chunk(b"IDAT", zlib.compress(raw)) + png_chunk(b"IEND", b"")


class FormStructure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.labels = []
        self.form_depth = 0
        self.nested_forms = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if tag == "label" and attrs.get("for"):
            self.labels.append(attrs["for"])
        if tag == "form":
            self.nested_forms |= self.form_depth > 0
            self.form_depth += 1

    def handle_endtag(self, tag):
        if tag == "form":
            self.form_depth -= 1


class EnterpriseDesignSystemTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create_user(username="requester-21c", password=password(), full_name="درخواست‌دهنده", must_change_password=False)
        cls.other = User.objects.create_user(username="other-21c", password=password(), must_change_password=False)
        cls.admin = User.objects.create_user(username="admin-21c", password=password(), role=User.Role.ADMIN, must_change_password=False)
        cls.lead = User.objects.create_user(username="lead-21c", password=password(), must_change_password=False)
        cls.supervisor = User.objects.create_user(username="supervisor-21c", password=password(), must_change_password=False)
        cls.executive = User.objects.create_user(username="executive-21c", password=password(), must_change_password=False)
        cls.a = Department.objects.create(code="a-21c", name="اداره الف", status=Department.Status.PUBLISHED)
        cls.b = Department.objects.create(code="b-21c", name="اداره ب", status=Department.Status.PUBLISHED)
        cls.family_a = Category.objects.create(department=cls.a, name="خانواده الف", slug="family-a-21c")
        cls.family_b = Category.objects.create(department=cls.b, name="خانواده ب", slug="family-b-21c")
        cls.service_a = Service.objects.create(code="A-21C", name="خدمت الف", category=cls.family_a, domain="آزمون", full_description="شرح خدمت الف")
        cls.service_b = Service.objects.create(code="B-21C", name="خدمت ب", category=cls.family_b, domain="آزمون", full_description="شرح خدمت ب")
        cls.inactive = Service.objects.create(code="OFF-21C", name="خدمت غیرفعال", category=cls.family_a, domain="آزمون", full_description="شرح", active=False)
        ServiceFormField.objects.create(service=cls.service_a, key="detail", label="جزئیات", field_type=ServiceFormField.FieldType.TEXT, required=True)
        RoleAssignment.objects.create(user=cls.lead, role=RoleAssignment.Role.DEPARTMENT_LEAD, scope_type=RoleAssignment.ScopeType.DEPARTMENT, department=cls.a)
        RoleAssignment.objects.create(user=cls.supervisor, role=RoleAssignment.Role.SUPERVISOR, scope_type=RoleAssignment.ScopeType.GLOBAL)
        RoleAssignment.objects.create(user=cls.executive, role=RoleAssignment.Role.EXECUTIVE_VIEWER, scope_type=RoleAssignment.ScopeType.GLOBAL)
        cls.item = Request.objects.create(requester=cls.requester, requesting_unit="واحد", service=cls.service_a, project="طرح", title="درخواست آزمون", status=Request.Status.SUBMITTED)
        cls.other_item = Request.objects.create(requester=cls.requester, requesting_unit="واحد", service=cls.service_b, project="طرح", title="درخواست اداره ب", status=Request.Status.SUBMITTED)

    def appearance_data(self, **changes):
        return {"app_name": DEFAULT_NAME, "primary_color": DEFAULT_PRIMARY, "accent_color": DEFAULT_ACCENT, "base_font_size": "15", "font_family": "system", **changes}

    def test_appearance_defaults_without_seed_and_permission(self):
        self.assertFalse(AppearanceSetting.objects.exists())
        self.client.force_login(self.requester)
        self.assertContains(self.client.get(reverse("home")), DEFAULT_NAME)
        self.assertEqual(self.client.get(reverse("appearance_settings")).status_code, 403)
        self.assertEqual(self.client.post(reverse("appearance_settings"), self.appearance_data()).status_code, 403)
        self.assertFalse(AppearanceSetting.objects.exists())
        self.client.force_login(self.lead)
        self.assertEqual(self.client.get(reverse("appearance_settings")).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("appearance_settings")), "پیش‌نمایش")

    def test_brand_values_persist_and_reset(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("appearance_settings"), self.appearance_data(app_name="سامانه داخلی", primary_color="#164e7a", accent_color="#096c59", base_font_size="17", font_family="tahoma"))
        self.assertEqual(response.status_code, 302)
        appearance = AppearanceSetting.objects.get(pk=1)
        self.assertEqual((appearance.app_name, appearance.base_font_size, appearance.font_family), ("سامانه داخلی", 17, "tahoma"))
        home = self.client.get(reverse("home"))
        self.assertContains(home, "--brand-base-size:17px")
        self.assertContains(home, "--brand-primary:#164e7a")
        self.client.post(reverse("appearance_settings"), {"reset": "1"})
        appearance.refresh_from_db()
        self.assertEqual((appearance.app_name, appearance.primary_color, appearance.accent_color, appearance.base_font_size, appearance.font_family), (DEFAULT_NAME, DEFAULT_PRIMARY, DEFAULT_ACCENT, 15, "system"))

    def test_invalid_theme_and_font_scale_do_not_save(self):
        self.client.force_login(self.admin)
        for invalid in (self.appearance_data(primary_color="red"), self.appearance_data(accent_color="#ffffff"), self.appearance_data(base_font_size="25"), self.appearance_data(font_family="javascript:evil")):
            response = self.client.post(reverse("appearance_settings"), invalid)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        self.assertFalse(AppearanceSetting.objects.exists())

    def test_png_logo_validation_and_public_delivery(self):
        self.client.force_login(self.admin)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as media_root, override_settings(MEDIA_ROOT=media_root):
            bad = SimpleUploadedFile("brand.svg", b"<svg onload='alert(1)'></svg>", content_type="image/svg+xml")
            response = self.client.post(reverse("appearance_settings"), {**self.appearance_data(), "logo": bad})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(AppearanceSetting.objects.exists())
            good = SimpleUploadedFile("brand.png", png_image(), content_type="image/png")
            response = self.client.post(reverse("appearance_settings"), {**self.appearance_data(), "logo": good})
            self.assertEqual(response.status_code, 302)
            self.client.logout()
            logo = self.client.get(reverse("brand_logo"))
            self.assertEqual(logo.status_code, 200)
            self.assertEqual(logo["Content-Type"], "image/png")
            self.assertEqual(b"".join(logo.streaming_content), png_image())
            self.client.force_login(self.admin)
            changed = self.client.post(reverse("appearance_settings"), self.appearance_data(app_name="نام جدید"))
            self.assertEqual(changed.status_code, 302)
            self.assertTrue(AppearanceSetting.objects.get(pk=1).logo)
            for appearance in AppearanceSetting.objects.all():
                if appearance.logo:
                    appearance.logo.close()
                appearance.delete()

    def test_role_navigation_and_breadcrumb(self):
        self.client.force_login(self.requester)
        form = self.client.get(reverse("request_create", args=[self.service_a.pk]))
        self.assertContains(form, 'aria-current="page"')
        self.assertContains(form, 'aria-label="مسیر صفحه"')
        self.assertNotContains(form, reverse("appearance_settings"))
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("home")), reverse("appearance_settings"))

    def test_request_actions_dynamic_field_and_jalali(self):
        self.client.force_login(self.requester)
        response = self.client.get(reverse("request_create", args=[self.service_a.pk]))
        for text in ("انصراف", "ذخیره پیش‌نویس", "ثبت نهایی درخواست", "جزئیات خدمت", "انتخاب تاریخ"):
            self.assertContains(response, text)
        self.assertContains(response, 'name="action" value="submit" class="btn btn-lg primary"')
        self.assertContains(response, 'name="data_detail"')
        invalid = self.client.post(reverse("request_create", args=[self.service_a.pk]), {"action": "submit", "project": "طرح", "title": "عنوان", "priority": "NORMAL"})
        self.assertContains(invalid, 'aria-invalid="true"')
        draft = self.client.post(reverse("request_create", args=[self.service_a.pk]), {"action": "draft", "project": "طرح", "title": "عنوان", "priority": "NORMAL"})
        self.assertEqual(draft.status_code, 302)
        self.assertEqual(Request.objects.filter(requester=self.requester, status=Request.Status.DRAFT).count(), 1)

    def test_status_journey_and_internal_note_are_scoped(self):
        RequestHistory.objects.create(request=self.item, actor=self.requester, action="REQUEST_SUBMITTED")
        RequestHistory.objects.create(request=self.item, actor=self.lead, action="INTERNAL_NOTE_ADDED")
        InternalNote.objects.create(request=self.item, author=self.lead, body="محرمانه برای مدیر")
        message = RequestResponse.objects.create(request=self.item, author=self.requester, body="پیام کاربر")
        self.client.force_login(self.requester)
        response = self.client.get(reverse("request_detail", args=[self.item.pk]))
        self.assertContains(response, "مسیر درخواست")
        self.assertContains(response, "ثبت نهایی درخواست")
        self.assertContains(response, "پیام کاربر")
        self.assertNotContains(response, "محرمانه برای مدیر")
        self.assertNotContains(response, "ثبت یادداشت داخلی")
        self.client.force_login(self.lead)
        response = self.client.get(reverse("control_request_detail", args=[self.item.pk]))
        self.assertContains(response, "محرمانه برای مدیر")
        self.assertContains(response, "ثبت یادداشت داخلی")
        self.assertContains(response, 'class="requester-message"')

    def test_existing_security_boundaries(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("request_detail", args=[self.item.pk])).status_code, 404)
        self.client.force_login(self.lead)
        self.assertEqual(self.client.get(reverse("control_request_detail", args=[self.other_item.pk])).status_code, 404)
        self.client.force_login(self.supervisor)
        response = self.client.get(reverse("control_request_detail", args=[self.item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "ثبت یادداشت")
        self.assertEqual(self.client.post(reverse("control_action", args=[self.item.pk]), {"kind": "note", "body": "نباید ثبت شود"}).status_code, 404)
        self.client.force_login(self.executive)
        self.assertEqual(self.client.get(reverse("control_requests")).status_code, 404)

    def test_attachment_access_still_uses_request_scope(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            attachment = Attachment.objects.create(request=self.item, uploaded_by=self.requester, file=SimpleUploadedFile("brief.pdf", b"%PDF-test", content_type="application/pdf"), original_name="brief.pdf", size=9, content_type="application/pdf")
            self.client.force_login(self.other)
            self.assertEqual(self.client.get(reverse("attachment_download", args=[attachment.pk])).status_code, 404)
            self.client.force_login(self.lead)
            response = self.client.get(reverse("attachment_download", args=[attachment.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), b"%PDF-test")
            self.client.force_login(self.supervisor)
            self.assertEqual(self.client.get(reverse("attachment_download", args=[attachment.pk])).status_code, 404)

    def test_inactive_service_has_no_create_cta(self):
        self.client.force_login(self.requester)
        response = self.client.get(reverse("service_detail", args=[self.inactive.pk]))
        self.assertContains(response, "ثبت درخواست غیرفعال است")
        self.assertNotContains(response, reverse("request_create", args=[self.inactive.pk]))

    def test_request_list_is_paginated_and_status_has_text(self):
        self.client.force_login(self.requester)
        for number in range(26):
            Request.objects.create(requester=self.requester, requesting_unit="واحد", service=self.service_a, project="طرح", title=f"درخواست {number}", status=Request.Status.SUBMITTED)
        response = self.client.get(reverse("my_requests"))
        self.assertContains(response, "صفحه 1 از 2")
        self.assertContains(response, 'data-status="SUBMITTED"')
        self.assertEqual(len(response.context["items"]), 25)

    def test_key_forms_have_unique_ids_associated_labels_and_no_nested_forms(self):
        pages = [(self.requester, reverse("request_create", args=[self.service_a.pk])),
                 (self.requester, reverse("request_detail", args=[self.item.pk])),
                 (self.lead, reverse("manage_department_detail", args=[self.a.pk])),
                 (self.admin, reverse("appearance_settings"))]
        for user, url in pages:
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            structure = FormStructure()
            structure.feed(response.content.decode())
            self.assertEqual(len(structure.ids), len(set(structure.ids)), url)
            self.assertFalse(structure.nested_forms, url)
            self.assertEqual(structure.form_depth, 0, url)
            self.assertFalse(set(structure.labels) - set(structure.ids), url)
