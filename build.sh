#!/usr/bin/env bash
# Render build step: install, collect static files, apply migrations (free tier has no pre-deploy hook).
set -o errexit

# manage.py defaults to the local settings; a deploy build must use production (static manifest, DB, security).
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.production}"

pip install --upgrade pip
pip install -r requirements/production.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input

# First admin without shell access (Render free). Set DJANGO_SUPERUSER_EMAIL/_NAME/_MOBILE/_PASSWORD once;
# it is a no-op afterwards. A bad value must not block the deploy, so only warn.
python manage.py ensure_superuser || echo "WARNING: could not create the admin user; see the message above."
