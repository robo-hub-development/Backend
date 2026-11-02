#!/usr/bin/env bash
# Render build step: install, collect static files, apply migrations (free tier has no pre-deploy hook).
set -o errexit

# manage.py defaults to the local settings; a deploy build must use production (static manifest, DB, security).
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.production}"

pip install --upgrade pip
pip install -r requirements/production.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
