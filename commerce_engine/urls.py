"""
هذا هو ملف urls.py الخاص بالمشروع (PROJECT-LEVEL).
المسار عندك: <اسم_مشروعك>/urls.py
مثال: backend/urls.py  أو  config/urls.py  أو  core/urls.py

⚠️  هذا غير ملف shop/urls.py
    - shop/urls.py  → مسارات التطبيق (موجود وصحيح)
    - هذا الملف    → يُدرج shop/urls.py في المشروع الكلي
"""

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),

    # ✅ يُدرج كل مسارات تطبيق shop مباشرةً في الجذر
    # هذا السطر هو اللي يجعل /products/ و /checkout/ و /process-batch/ تعمل
    path("", include("shop.urls")),
]
