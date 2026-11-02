#!/usr/bin/env bash
# Render build step: install, collect static files, apply migrations (free tier has no pre-deploy hook).
set -o errexit

pip install --upgrade pip
pip install -r requirements/production.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
