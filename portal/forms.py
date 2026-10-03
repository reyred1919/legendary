from pathlib import Path
from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.utils import timezone
from .models import AllocationPeriod, ApprovalPolicy, AppearanceSetting, Attachment, Category, CreditAllocation, Department, PriorityPolicy, Program, Project, Request, RoleAssignment, SeniorApprovalConfiguration, Service, ServiceFormField, User

class LoginForm(AuthenticationForm):
    username=forms.CharField(label="نام کاربری",widget=forms.TextInput(attrs={"autofocus":True,"autocomplete":"username"}))
    password=forms.CharField(label="رمز عبور",strip=False,widget=forms.PasswordInput(attrs={"autocomplete":"current-password"}))

class AppearanceForm(forms.ModelForm):
    class Meta:
        model=AppearanceSetting
        fields=["app_name","logo","primary_color","accent_color","base_font_size","font_family"]
        labels={"app_name":"نام سامانه","logo":"لوگو (PNG، حداکثر ۱ مگابایت)","primary_color":"رنگ اصلی","accent_color":"رنگ تأکیدی","base_font_size":"اندازه پایه قلم","font_family":"قلم"}
        widgets={"primary_color":forms.TextInput(attrs={"type":"color"}),"accent_color":forms.TextInput(attrs={"type":"color"})}


class ProgramForm(forms.ModelForm):
    class Meta:
        model=Program; fields=["code","name","description","status"]
        labels={"code":"کد پایدار طرح","name":"نام طرح","description":"توضیحات","status":"وضعیت"}
        widgets={"description":forms.Textarea(attrs={"rows":4})}
    def clean_status(self):
        value=self.cleaned_data["status"]
        if not self.instance.pk:
            if value!=Program.Status.DRAFT:raise forms.ValidationError("طرح جدید را ابتدا به صورت پیش‌نویس بسازید و سپس فعال کنید.")
            return value
        allowed={Program.Status.DRAFT:{Program.Status.ACTIVE},Program.Status.ACTIVE:{Program.Status.DISABLED,Program.Status.ARCHIVED},Program.Status.DISABLED:{Program.Status.ACTIVE,Program.Status.ARCHIVED},Program.Status.ARCHIVED:set()}
        if value!=self.instance.status and value not in allowed[self.instance.status]:raise forms.ValidationError("این تغییر وضعیت در چرخهٔ عمر طرح مجاز نیست.")
        return value


class ProjectForm(forms.ModelForm):
    class Meta:
        model=Project; fields=["code","name","program","description","status"]
        labels={"code":"کد پایدار پروژه","name":"نام پروژه","program":"طرح والد","description":"توضیحات","status":"وضعیت"}
        widgets={"description":forms.Textarea(attrs={"rows":4})}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["program"].queryset=Program.objects.exclude(status=Program.Status.ARCHIVED)
        if self.instance.pk:
            self.fields["program"].disabled=True
    def clean_status(self):
        value=self.cleaned_data["status"]
        if not self.instance.pk and value!=Project.Status.DRAFT:raise forms.ValidationError("پروژهٔ جدید را ابتدا به صورت پیش‌نویس بسازید و سپس فعال کنید.")
        if self.instance.pk:
            allowed={Project.Status.DRAFT:{Project.Status.ACTIVE},Project.Status.ACTIVE:{Project.Status.DISABLED,Project.Status.ARCHIVED},Project.Status.DISABLED:{Project.Status.ACTIVE,Project.Status.ARCHIVED},Project.Status.ARCHIVED:set()}
            if value!=self.instance.status and value not in allowed[self.instance.status]:raise forms.ValidationError("این تغییر وضعیت در چرخهٔ عمر پروژه مجاز نیست.")
        if value==Project.Status.ACTIVE:
            program=self.cleaned_data.get("program") or self.instance.program
            if program and program.status!=Program.Status.ACTIVE:raise forms.ValidationError("برای فعال‌سازی پروژه، طرح والد باید فعال باشد.")
        return value


class DemandManagerAssignmentForm(forms.Form):
    user=forms.ModelChoiceField(label="کاربر",queryset=User.objects.filter(is_active=True).order_by("full_name","username"))

class RequestBaseForm(forms.ModelForm):
    class Meta:
        model=Request; fields=["program","project_entity","requester_role_context","project","title","priority","desired_delivery_date"]
        labels={"project":"نام طرح یا پروژه","title":"عنوان درخواست","priority":"اولویت","desired_delivery_date":"تاریخ مورد انتظار درخواست‌دهنده"}
        widgets={"desired_delivery_date":forms.HiddenInput(attrs={"class":"jalali-iso","data-min-today":"true"})}
    def __init__(self,*a,service=None,user=None,allow_incomplete=False,**kw):
        super().__init__(*a,**kw); self.service=service; self.allow_incomplete=allow_incomplete
        from .models import PriorityPolicy
        choices=[(row.code,row.name) for row in PriorityPolicy.objects.filter(is_active=True)]
        if self.instance.pk and self.instance.priority not in dict(choices):
            choices.append((self.instance.priority,self.instance.get_priority_display()))
        self.fields['priority'].choices=[('', 'انتخاب اولویت')]+choices
        self.user=user
        assignments=RoleAssignment.objects.filter(user=user,is_active=True) if user and user.is_authenticated else RoleAssignment.objects.none()
        self.program_ids=set(assignments.filter(scope_type=RoleAssignment.ScopeType.PROGRAM,role=RoleAssignment.Role.PROGRAM_MANAGER).values_list("program_id",flat=True))
        self.project_ids=set(assignments.filter(scope_type=RoleAssignment.ScopeType.PROJECT,role=RoleAssignment.Role.PROJECT_MANAGER).values_list("project_id",flat=True))
        self.context_roles=[]
        if self.program_ids:self.context_roles.append(RoleAssignment.Role.PROGRAM_MANAGER)
        if self.project_ids:self.context_roles.append(RoleAssignment.Role.PROJECT_MANAGER)
        if self.instance.pk and self.instance.program_id and self.instance.requester_role_context and not self.context_roles:
            self.context_roles=[self.instance.requester_role_context]
        self.context_field_names=[]
        if not self.context_roles:
            for name in ("program","project_entity","requester_role_context"):self.fields.pop(name,None)
        else:
            role_labels={RoleAssignment.Role.PROGRAM_MANAGER:"مدیر طرح",RoleAssignment.Role.PROJECT_MANAGER:"مدیر پروژه"}
            role_field=self.fields["requester_role_context"]
            role_field.label="نقش درخواست‌دهنده در این درخواست"
            role_field.choices=[(role,role_labels[role]) for role in self.context_roles]
            role_field.initial=self.instance.requester_role_context or (self.context_roles[0] if len(self.context_roles)==1 else None)
            if len(self.context_roles)==1:role_field.widget=forms.HiddenInput()
            program_field=self.fields["program"]
            program_field.label="طرح"
            program_field.queryset=Program.objects.filter(pk__in=self.program_ids,status=Program.Status.ACTIVE)
            program_field.initial=self.instance.program_id or (next(iter(self.program_ids)) if len(self.program_ids)==1 else None)
            project_field=self.fields["project_entity"]
            project_field.label="پروژه"
            selected_role=(self.data.get("requester_role_context") if self.is_bound else self.instance.requester_role_context) or role_field.initial
            if selected_role==RoleAssignment.Role.PROJECT_MANAGER:
                self.fields.pop("program")
                project_field.queryset=Project.objects.filter(pk__in=self.project_ids,status=Project.Status.ACTIVE,program__status=Program.Status.ACTIVE).select_related("program")
                project_field.required=True
                if self.instance.project_entity_id:project_field.initial=self.instance.project_entity_id
            else:
                program_value=(self.data.get("program") if self.is_bound else self.instance.program_id) or (next(iter(self.program_ids)) if len(self.program_ids)==1 else None)
                if program_value and str(program_value).isdigit():
                    project_field.queryset=Project.objects.filter(program_id=int(program_value),status=Project.Status.ACTIVE,program__status=Program.Status.ACTIVE).select_related("program")
                else:project_field.queryset=Project.objects.none()
                project_field.required=False
                project_field.empty_label="بدون انتخاب پروژه"
                if self.instance.project_entity_id:project_field.initial=self.instance.project_entity_id
            self.context_field_names=[name for name in ("requester_role_context","program","project_entity") if name in self.fields]
            self.fields.pop("project")
        if not service.supports_desired_date: self.fields.pop("desired_delivery_date",None)
        if allow_incomplete:
            for name in ("project","title","priority","desired_delivery_date"):
                if name in self.fields: self.fields[name].required=False
        for f in service.form_fields.filter(active=True):
            required=f.required and not allow_incomplete; attrs={"placeholder":f.placeholder,"data-help":f.help_text}
            if f.field_type==ServiceFormField.FieldType.FILE and self.instance.pk and self.instance.attachments.exists(): required=False
            if f.field_type==ServiceFormField.FieldType.TEXTAREA: field=forms.CharField(widget=forms.Textarea(attrs=attrs),required=required)
            elif f.field_type==ServiceFormField.FieldType.NUMBER: field=forms.DecimalField(widget=forms.NumberInput(attrs=attrs),required=required)
            elif f.field_type==ServiceFormField.FieldType.DATE: field=forms.DateField(widget=forms.HiddenInput(attrs={"class":"jalali-iso dynamic-date"}),required=required)
            elif f.field_type in {ServiceFormField.FieldType.SELECT,ServiceFormField.FieldType.RADIO}: field=forms.ChoiceField(choices=[("","انتخاب کنید")]+[(x,x) for x in f.options],widget=forms.RadioSelect if f.field_type==ServiceFormField.FieldType.RADIO else forms.Select,required=required)
            elif f.field_type==ServiceFormField.FieldType.MULTISELECT: field=forms.MultipleChoiceField(choices=[(x,x) for x in f.options],widget=forms.CheckboxSelectMultiple,required=required)
            elif f.field_type==ServiceFormField.FieldType.CHECKBOX: field=forms.BooleanField(required=required)
            elif f.field_type==ServiceFormField.FieldType.EMAIL: field=forms.EmailField(widget=forms.EmailInput(attrs=attrs),required=required)
            elif f.field_type==ServiceFormField.FieldType.PHONE: field=forms.RegexField(regex=r"^[+\d۰-۹٠-٩()\-\s]{7,30}$",widget=forms.TextInput(attrs={**attrs,"type":"tel","autocomplete":"tel"}),required=required)
            elif f.field_type==ServiceFormField.FieldType.FILE: field=forms.FileField(required=required)
            else: field=forms.CharField(widget=forms.TextInput(attrs=attrs),required=required)
            field.label=f.label; field.help_text=f.help_text; self.fields[f"data_{f.key}"]=field
            if f.field_type==ServiceFormField.FieldType.FILE:
                field.widget.attrs.update({"data-file-preview":"true","data-max-size":str(settings.MAX_UPLOAD_SIZE),"accept":",".join("."+extension for extension in sorted(settings.ALLOWED_UPLOAD_EXTENSIONS))})
            if self.instance.pk and f.key in self.instance.request_data: self.initial[f"data_{f.key}"]=self.instance.request_data[f.key]
    def clean(self):
        cleaned=super().clean()
        if not self.context_roles:return cleaned
        role=cleaned.get("requester_role_context")
        project=cleaned.get("project_entity")
        if role not in self.context_roles:
            self.add_error("requester_role_context","نقش سازمانی فعال و مجاز برای این درخواست انتخاب نشده است.")
            return cleaned
        if role==RoleAssignment.Role.PROGRAM_MANAGER:
            program=cleaned.get("program")
            if not program or program.pk not in self.program_ids:
                self.add_error("program","فقط یکی از طرح‌های فعال تحت مدیریت شما قابل انتخاب است.")
            elif project and (project.program_id!=program.pk or not project.is_requestable):
                self.add_error("project_entity","پروژه باید فعال و متعلق به طرح انتخاب‌شده باشد.")
        elif role==RoleAssignment.Role.PROJECT_MANAGER:
            if not project or project.pk not in self.project_ids or not project.is_requestable:
                self.add_error("project_entity","فقط پروژهٔ فعال تحت مدیریت شما قابل انتخاب است.")
            else:cleaned["program"]=project.program
        return cleaned
    def save(self,commit=True):
        obj=super().save(commit=False)
        if self.context_roles:
            obj.program=self.cleaned_data.get("program")
            obj.project_entity=self.cleaned_data.get("project_entity")
            obj.requester_role_context=self.cleaned_data.get("requester_role_context","")
        if commit:obj.save()
        return obj
    def clean_desired_delivery_date(self):
        value=self.cleaned_data.get("desired_delivery_date")
        if value and value<timezone.localdate(): raise forms.ValidationError("تاریخ مورد انتظار نمی‌تواند در گذشته باشد.")
        return value
    def save(self,commit=True):
        obj=super().save(False); obj.service=self.service
        def serializable(value):
            if hasattr(value,"isoformat"): return value.isoformat()
            return str(value) if value.__class__.__name__=="Decimal" else value
        obj.request_data={k[5:]:serializable(v) for k,v in self.cleaned_data.items() if k.startswith("data_") and not hasattr(v,"read") and v not in (None,"")}
        if commit: obj.save()
        return obj
    def clean(self):
        cleaned=super().clean()
        for key,value in cleaned.items():
            if key.startswith("data_") and hasattr(value,"size"):
                if value.size>settings.MAX_UPLOAD_SIZE: self.add_error(key,"حجم فایل بیشتر از حد مجاز است.")
                if Path(value.name).suffix.lower().lstrip(".") not in settings.ALLOWED_UPLOAD_EXTENSIONS: self.add_error(key,"نوع فایل مجاز نیست.")
        return cleaned
    def dynamic_files(self): return [v for k,v in self.cleaned_data.items() if k.startswith("data_") and hasattr(v,"read")]

    def mark_errors_for_accessibility(self):
        for name in self.errors:
            if name in self.fields:
                widget=self.fields[name].widget
                widget.attrs["aria-invalid"]="true"
                error_id=f"id_{name}_error"
                widget.attrs["aria-describedby"]=" ".join(filter(None,(widget.attrs.get("aria-describedby"),error_id)))

def request_readiness_errors(obj):
    errors=[]
    if not obj.program_id and not obj.project.strip(): errors.append("نام طرح یا پروژه")
    if not obj.title.strip(): errors.append("عنوان درخواست")
    if obj.service.supports_desired_date and obj.desired_delivery_date and obj.desired_delivery_date < timezone.localdate(): errors.append("تاریخ مورد انتظار معتبر")
    for field in obj.service.form_fields.filter(active=True,required=True):
        if field.field_type==ServiceFormField.FieldType.FILE:
            if not obj.attachments.exists(): errors.append(field.label)
        elif obj.request_data.get(field.key) in (None,"",[]): errors.append(field.label)
    return errors

class MessageForm(forms.Form):
    body=forms.CharField(label="پیام",widget=forms.Textarea(attrs={"rows":4}),required=True)
    file=forms.FileField(label="پیوست",required=False)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["file"].widget.attrs.update({"data-file-preview":"true","data-max-size":str(settings.MAX_UPLOAD_SIZE),"accept":",".join("."+extension for extension in sorted(settings.ALLOWED_UPLOAD_EXTENSIONS))})
    def clean_file(self):
        f=self.cleaned_data.get("file")
        if not f:return f
        if f.size>settings.MAX_UPLOAD_SIZE: raise forms.ValidationError("حجم فایل بیشتر از حد مجاز است.")
        if Path(f.name).suffix.lower().lstrip(".") not in settings.ALLOWED_UPLOAD_EXTENSIONS: raise forms.ValidationError("نوع فایل مجاز نیست.")
        return f

class InternalNoteForm(forms.Form): body=forms.CharField(label="یادداشت داخلی",widget=forms.Textarea(attrs={"rows":3}))
class ManagerActionForm(forms.Form):
    status=forms.ChoiceField(label="وضعیت",choices=Request.Status.choices,required=False)
    owner=forms.ModelChoiceField(label="مسئول رسیدگی",queryset=None,required=False)
    reason=forms.CharField(label="دلیل تغییر وضعیت",required=False,widget=forms.Textarea(attrs={"rows":3,"placeholder":"برای توقف، درخواست اطلاعات، رد یا لغو الزامی است."}))
    def __init__(self,*a,request_obj=None,**kw):
        from .models import User
        from .policies import eligible_owners
        super().__init__(*a,**kw); self.fields["owner"].queryset=User.objects.none()
        if request_obj:
            self.fields["owner"].queryset=eligible_owners(request_obj.department)
            allowed={request_obj.status,*request_obj.allowed_transitions()}; self.fields["status"].choices=[x for x in Request.Status.choices if x[0] in allowed]
            self.request_obj=request_obj
    def clean(self):
        cleaned=super().clean(); status=cleaned.get("status"); reason=(cleaned.get("reason") or "").strip()
        needs_reason={Request.Status.NEED_INFO,Request.Status.ON_HOLD,Request.Status.REJECTED,Request.Status.CANCELLED}
        current_status=getattr(getattr(self,"request_obj",None),"status",None)
        if status and status!=current_status and status in needs_reason and not reason:
            self.add_error("reason","ثبت دلیل برای این تغییر وضعیت الزامی است.")
        cleaned["reason"]=reason; return cleaned

def save_upload(req,user,file,response=None):
    if not file:return None
    return Attachment.objects.create(request=req,uploaded_by=user,file=file,original_name=file.name,size=file.size,content_type=getattr(file,"content_type","")[:120],response=response)


class DepartmentForm(forms.ModelForm):
    class Meta:
        model=Department
        fields=["code","name","short_name","description","intro_text","icon_name","cover_image","display_order"]
        labels={"code":"کد پایدار","name":"نام اداره","short_name":"نام کوتاه","description":"توضیح کوتاه","intro_text":"متن معرفی","icon_name":"نام آیکون","cover_image":"تصویر یا GIF معرفی","display_order":"ترتیب نمایش"}
        widgets={"description":forms.Textarea(attrs={"rows":3}),"intro_text":forms.Textarea(attrs={"rows":4})}

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if self.instance.pk:
            self.fields["code"].disabled=True

    def clean_cover_image(self):
        image=self.cleaned_data.get("cover_image")
        if not image or not hasattr(image,"size"):return image
        if image.size>3*1024*1024:raise forms.ValidationError("حجم تصویر نباید بیشتر از ۳ مگابایت باشد.")
        extension=Path(image.name).suffix.lower()
        if extension not in {".png",".jpg",".jpeg",".webp",".gif"}:raise forms.ValidationError("فقط PNG، JPEG، WebP یا GIF مجاز است.")
        head=image.read(16);image.seek(0)
        valid=(extension==".png" and head.startswith(b"\x89PNG\r\n\x1a\n")) or (extension in {".jpg",".jpeg"} and head.startswith(b"\xff\xd8\xff")) or (extension==".gif" and head.startswith((b"GIF87a",b"GIF89a"))) or (extension==".webp" and head[:4]==b"RIFF" and head[8:12]==b"WEBP")
        if not valid:raise forms.ValidationError("محتوای فایل با نوع تصویر مجاز هم‌خوانی ندارد.")
        return image


class DepartmentLifecycleForm(forms.Form):
    status=forms.ChoiceField(label="وضعیت مقصد",choices=Department.Status.choices)

    def __init__(self,*args,department=None,actor=None,**kwargs):
        from .policies import is_super_admin
        super().__init__(*args,**kwargs)
        allowed={
            Department.Status.DRAFT:{Department.Status.PUBLISHED},
            Department.Status.PUBLISHED:{Department.Status.DISABLED,Department.Status.TEMPORARILY_DISABLED,Department.Status.ARCHIVED},
            Department.Status.DISABLED:{Department.Status.PUBLISHED,Department.Status.TEMPORARILY_DISABLED,Department.Status.ARCHIVED},
            Department.Status.TEMPORARILY_DISABLED:{Department.Status.PUBLISHED,Department.Status.DISABLED,Department.Status.ARCHIVED},
            Department.Status.ARCHIVED:set(),
        }.get(department.status,set())
        if not is_super_admin(actor): allowed.discard(Department.Status.ARCHIVED)
        self.fields["status"].choices=[item for item in Department.Status.choices if item[0] in allowed]
        self.department=department

    def clean_status(self):
        status=self.cleaned_data["status"]
        if status==Department.Status.PUBLISHED and not self.department.service_families.filter(active=True,lifecycle_status=Category.Status.ACTIVE,services__active=True,services__lifecycle_status=Service.Status.ACTIVE).exists():
            raise forms.ValidationError("برای انتشار، حداقل یک خانواده و خدمت فعال لازم است.")
        return status


class DepartmentMembershipForm(forms.Form):
    user=forms.ModelChoiceField(label="کاربر",queryset=User.objects.none())
    role=forms.ChoiceField(label="نقش",choices=())

    def __init__(self,*args,actor=None,**kwargs):
        from .policies import is_super_admin
        super().__init__(*args,**kwargs)
        self.fields["user"].queryset=User.objects.filter(is_active=True).order_by("full_name","username")
        roles=[RoleAssignment.Role.REQUEST_MANAGER]
        if is_super_admin(actor): roles.append(RoleAssignment.Role.DEPARTMENT_LEAD)
        self.fields["role"].choices=[item for item in RoleAssignment.Role.choices if item[0] in roles]


class ServiceFamilyForm(forms.ModelForm):
    class Meta:
        model=Category
        fields=["name","slug","description","display_order","lifecycle_status"]
        labels={"name":"نام خانواده خدمت","slug":"شناسه نشانی","description":"توضیح","display_order":"ترتیب نمایش","lifecycle_status":"وضعیت"}
        widgets={"description":forms.Textarea(attrs={"rows":3})}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.fields["lifecycle_status"].required=False
    def save(self,commit=True):
        obj=super().save(commit=False);obj.active=obj.lifecycle_status==Category.Status.ACTIVE
        if commit:obj.save()
        return obj
    def clean_lifecycle_status(self):
        value=self.cleaned_data.get("lifecycle_status") or (self.instance.lifecycle_status if self.instance.pk else Category.Status.ACTIVE)
        if not self.instance.pk and value==Category.Status.ARCHIVED:raise forms.ValidationError("خانوادهٔ جدید را نمی‌توان مستقیماً بایگانی کرد.")
        old=self.instance.lifecycle_status if self.instance.pk else Category.Status.ACTIVE
        allowed={Category.Status.ACTIVE:{Category.Status.DISABLED,Category.Status.ARCHIVED},Category.Status.DISABLED:{Category.Status.ACTIVE,Category.Status.ARCHIVED},Category.Status.ARCHIVED:set()}
        if value!=old and value not in allowed[old]:raise forms.ValidationError("تغییر وضعیت با چرخهٔ عمر خانواده سازگار نیست.")
        return value


class ServiceManagementForm(forms.ModelForm):
    class Meta:
        model=Service
        fields=["code","name","category","domain","short_description","full_description","purpose","scope","deliverables","required_inputs","request_requirements","process_information","excluded","service_role","acceptance_criteria","legacy_sla","default_owner","initial_response_days","review_target_days","delivery_min_days","delivery_max_days","maximum_duration_days","supports_desired_date","lifecycle_status","display_order"]
        labels={"category":"خانواده خدمت","lifecycle_status":"وضعیت","display_order":"ترتیب نمایش","initial_response_days":"زمان پاسخ اولیه (روز کاری)","review_target_days":"هدف بازبینی (روز کاری)","delivery_min_days":"حداقل زمان انجام (روز کاری)","delivery_max_days":"حداکثر زمان انجام (روز کاری)","maximum_duration_days":"حد نهایی مدت (روز کاری)","default_owner":"مسئول پیش‌فرض"}
        widgets={name:forms.Textarea(attrs={"rows":3}) for name in ["short_description","full_description","purpose","scope","deliverables","required_inputs","request_requirements","process_information","excluded","acceptance_criteria","legacy_sla"]}

    def __init__(self,*args,department=None,**kwargs):
        from .policies import eligible_owners
        super().__init__(*args,**kwargs)
        self.department=department
        self.fields["lifecycle_status"].required=False
        self.fields["category"].queryset=department.service_families.all()
        self.fields["default_owner"].queryset=eligible_owners(department)

    def clean_category(self):
        category=self.cleaned_data["category"]
        if category.department_id!=self.department.pk:
            raise forms.ValidationError("خانواده خدمت باید متعلق به همین اداره باشد.")
        return category

    def clean_lifecycle_status(self):
        value=self.cleaned_data.get("lifecycle_status") or (self.instance.lifecycle_status if self.instance.pk else Service.Status.ACTIVE)
        if not self.instance.pk and value==Service.Status.ARCHIVED:raise forms.ValidationError("خدمت جدید را نمی‌توان مستقیماً بایگانی کرد.")
        old=self.instance.lifecycle_status if self.instance.pk else Service.Status.ACTIVE
        allowed={Service.Status.ACTIVE:{Service.Status.DISABLED,Service.Status.ARCHIVED},Service.Status.DISABLED:{Service.Status.ACTIVE,Service.Status.ARCHIVED},Service.Status.ARCHIVED:set()}
        if value!=old and value not in allowed[old]:raise forms.ValidationError("تغییر وضعیت با چرخهٔ عمر خدمت سازگار نیست.")
        return value

    def save(self,commit=True):
        obj=super().save(commit=False);obj.active=obj.lifecycle_status==Service.Status.ACTIVE
        if commit:obj.save()
        return obj


class ServiceFormFieldForm(forms.ModelForm):
    class Meta:
        model=ServiceFormField
        fields=["key","label","field_type","required","placeholder","help_text","options","display_order","active"]
        labels={"key":"کلید یکتا","label":"عنوان نمایشی","field_type":"نوع ورودی","required":"الزامی","placeholder":"متن راهنما","help_text":"توضیح زیر فیلد","options":"گزینه‌ها (JSON array برای انتخابی‌ها)","display_order":"ترتیب","active":"فعال"}
        widgets={"options":forms.Textarea(attrs={"rows":3}),"help_text":forms.Textarea(attrs={"rows":2})}
    def __init__(self,*args,service=None,**kwargs):
        super().__init__(*args,**kwargs);self.service=service
        self.fields["options"].help_text='برای select و multiselect: ["گزینه یک", "گزینه دو"]'
    def clean(self):
        cleaned=super().clean(); kind=cleaned.get("field_type"); options=cleaned.get("options") or []
        if kind in {ServiceFormField.FieldType.SELECT,ServiceFormField.FieldType.RADIO,ServiceFormField.FieldType.MULTISELECT} and not options:self.add_error("options","برای فیلد انتخابی دست‌کم یک گزینه لازم است.")
        if kind not in {ServiceFormField.FieldType.SELECT,ServiceFormField.FieldType.RADIO,ServiceFormField.FieldType.MULTISELECT} and options:self.add_error("options","فقط فیلدهای انتخابی گزینه دارند.")
        if len(options)>100:self.add_error("options","حداکثر ۱۰۰ گزینه مجاز است.")
        return cleaned
    def save(self,commit=True):
        obj=super().save(commit=False);obj.service=self.service
        if commit:obj.save()
        return obj

class PriorityPolicyForm(forms.ModelForm):
    class Meta:
        model=PriorityPolicy
        fields=['name','display_order','is_active','requires_credit','requires_approval','semantic_tone']

class AllocationPeriodForm(forms.ModelForm):
    class Meta:
        model=AllocationPeriod
        fields=['name','kind','starts_on','ends_on','is_active']
        widgets={'starts_on':forms.DateInput(attrs={'type':'date'}),'ends_on':forms.DateInput(attrs={'type':'date'})}

class CreditAllocationForm(forms.ModelForm):
    class Meta:
        model=CreditAllocation
        fields=['program','department','priority','period','quantity','is_active']
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['priority'].queryset=PriorityPolicy.objects.filter(requires_credit=True)

class ApprovalPolicyForm(forms.ModelForm):
    class Meta:
        model=ApprovalPolicy
        fields=['name','trigger','priority','requester_role','target','sequence','is_active','pauses_sla']

class SeniorApprovalConfigurationForm(forms.ModelForm):
    class Meta:
        model=SeniorApprovalConfiguration
        fields=['title','is_active']

class ProviderApprovalForm(forms.Form):
    target=forms.ChoiceField(label='مقصد تأیید',choices=ApprovalPolicy.Target.choices)
    reason=forms.CharField(label='دلیل ارجاع',widget=forms.Textarea(attrs={'rows':3}))
    assessment=forms.CharField(label='ارزیابی ارائه‌دهنده',required=False,widget=forms.Textarea(attrs={'rows':3}))
    estimated_time=forms.CharField(label='زمان برآوردی',max_length=120,required=False)
    estimated_cost=forms.CharField(label='هزینهٔ برآوردی',max_length=120,required=False)
    conditions=forms.CharField(label='شرایط',required=False,widget=forms.Textarea(attrs={'rows':2}))
    recommendation=forms.CharField(label='پیشنهاد',required=False,widget=forms.Textarea(attrs={'rows':2}))
    risks=forms.CharField(label='ریسک‌ها',required=False,widget=forms.Textarea(attrs={'rows':2}))
    file=forms.FileField(label='پیوست خصوصی تأیید',required=False)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields["file"].widget.attrs.update({"data-file-preview":"true","data-max-size":str(settings.MAX_UPLOAD_SIZE),"accept":",".join("."+extension for extension in sorted(settings.ALLOWED_UPLOAD_EXTENSIONS))})
    def clean_file(self):
        file=self.cleaned_data.get('file')
        if file and (file.size>settings.MAX_UPLOAD_SIZE or Path(file.name).suffix.lower().lstrip('.') not in settings.ALLOWED_UPLOAD_EXTENSIONS):
            raise forms.ValidationError('اندازه یا نوع فایل مجاز نیست.')
        return file
