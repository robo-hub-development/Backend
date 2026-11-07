from django.contrib.admin.apps import AdminConfig


class RoboHubAdminConfig(AdminConfig):
    """Replaces django.contrib.admin in INSTALLED_APPS so the admin uses our site (branding + dashboard).

    Kept out of apps.py: Django refuses a module that exposes two AppConfig classes as defaults."""

    default_site = "apps.dashboard.admin_site.RoboHubAdminSite"
