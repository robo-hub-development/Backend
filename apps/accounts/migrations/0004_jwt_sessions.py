from django.db import migrations


def drop_opaque_sessions(apps, schema_editor):
    """Opaque tokens cannot be turned into JWTs; those sessions end and users sign in again."""
    apps.get_model("accounts", "AuthToken").objects.all().delete()


class Migration(migrations.Migration):
    """Session tokens become signed JWTs whose `jti` is the AuthToken id; the stored token hash is obsolete."""

    dependencies = [("accounts", "0003_user_mobile_required")]

    operations = [
        migrations.RunPython(drop_opaque_sessions, migrations.RunPython.noop),
        migrations.RemoveField(model_name="authtoken", name="token_hash"),
    ]
