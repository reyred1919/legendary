from django.db import migrations


def seed_project_manager_program_gate(apps, schema_editor):
    Priority = apps.get_model("portal", "PriorityPolicy")
    Policy = apps.get_model("portal", "ApprovalPolicy")
    for code, pauses in (("NORMAL", False), ("HIGH", False), ("VERY_URGENT", True), ("EMERGENCY", True)):
        try:
            priority = Priority.objects.get(code=code)
        except Priority.DoesNotExist:
            continue
        Policy.objects.get_or_create(
            trigger="PRIORITY",
            priority=priority,
            requester_role="PROJECT_MANAGER",
            target="PROGRAM_MANAGER",
            sequence=1,
            defaults={
                "name": f"تأیید مدیر طرح · {priority.name}",
                "pauses_sla": pauses,
                "is_active": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("portal", "0013_user_onboarding_tour")]

    operations = [migrations.RunPython(seed_project_manager_program_gate, migrations.RunPython.noop)]
