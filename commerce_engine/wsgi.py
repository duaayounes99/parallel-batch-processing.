"""
WSGI config for commerce_engine project.

يكشف متغير WSGI القابل للاستدعاء باسم ``application``.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "commerce_engine.settings")

application = get_wsgi_application()
