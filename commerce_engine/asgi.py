"""
ASGI config for commerce_engine project.

يكشف متغير ASGI القابل للاستدعاء باسم ``application``.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "commerce_engine.settings")

application = get_asgi_application()
