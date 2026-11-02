import django.core.validators
from django.db import migrations, models


def refuse_if_blank_mobiles(apps, schema_editor):
    """Making mobile required cannot invent numbers: existing accounts must be fixed first."""
    User = apps.get_model("accounts", "User")
    blank = list(User.objects.filter(mobile="").values_list("email", flat=True))
    if blank:
        raise RuntimeError(
            "These users have no mobile number; set one for each before migrating: " + ", ".join(blank)
            + ". Example: manage.py shell -c \"from apps.accounts.models import User; "
            "User.objects.filter(email='<email>').update(mobile='<number>')\""
        )


class Migration(migrations.Migration):
    """Mobile becomes mandatory: unconditional unique + not-empty check (replaces the 'unique when set' rule)."""

    dependencies = [("accounts", "0002_user_mobile_unique")]

    operations = [
        migrations.RunPython(refuse_if_blank_mobiles, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name="user", name="user_mobile_unique_when_set"),
        migrations.AlterField(
            model_name="user",
            name="mobile",
            field=models.CharField(
                error_messages={"unique": "A user with this mobile number already exists."},
                max_length=16,
                unique=True,
                validators=[django.core.validators.RegexValidator("^\\+?\\d{10,15}$", "Enter a valid mobile number.")],
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(condition=models.Q(("mobile", ""), _negated=True), name="user_mobile_not_empty"),
        ),
    ]
