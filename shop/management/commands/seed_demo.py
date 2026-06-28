
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from rest_framework.authtoken.models import Token

from shop.models import Product


DEMO_PRODUCTS = [
    {"name": "لابتوب Dell XPS 13",        "price": "4500.00", "stock_quantity": 15},
    {"name": "ماوس لاسلكي Logitech",       "price": "120.00",  "stock_quantity": 50},
    {"name": "كيبورد ميكانيكي",            "price": "350.00",  "stock_quantity": 30},
    {"name": "شاشة Dell 27 بوصة",          "price": "900.00",  "stock_quantity": 20},
    {"name": "سماعات Sony WH-1000XM5",     "price": "1200.00", "stock_quantity": 10},
    {"name": "هارد خارجي 2 تيرا",          "price": "280.00",  "stock_quantity": 40},
    {"name": "كابل USB-C",                "price": "35.00",   "stock_quantity": 100},
    {"name": "حامل لابتوب قابل للطي",      "price": "150.00",  "stock_quantity": 25},
    {"name": "كاميرا ويب Full HD",         "price": "220.00",  "stock_quantity": 18},
    {"name": "بطارية محمولة 20000mAh",     "price": "180.00",  "stock_quantity": 1},  # ← كمية 1 عمداً، لاختبار تعارض الشراء المتزامن
]


class Command(BaseCommand):
    help = "Seeds the database with demo products and users for running the project and load testing."

    def handle(self, *args, **options):
        with transaction.atomic():
            self._seed_products()
            self._seed_users()
        self.stdout.write(self.style.SUCCESS("Demo data seeded successfully."))

    def _seed_products(self):
        created_count = 0
        for data in DEMO_PRODUCTS:
            _, created = Product.objects.get_or_create(
                name=data["name"],
                defaults={
                    "price": data["price"],
                    "stock_quantity": data["stock_quantity"],
                    "description": f"منتج تجريبي: {data['name']}",
                },
            )
            if created:
                created_count += 1
        self.stdout.write(f"  Products: {created_count} new product(s) (Total: {Product.objects.count()})")

    def _seed_users(self):
        admin_user, admin_created = User.objects.get_or_create(
            username="admin",
            defaults={"email": "admin@demo.com", "is_staff": True, "is_superuser": True},
        )
        if admin_created:
            admin_user.set_password("admin12345")
            admin_user.save()
        Token.objects.get_or_create(user=admin_user)

        student_user, student_created = User.objects.get_or_create(
            username="student",
            defaults={"email": "student@demo.com"},
        )
        if student_created:
            student_user.set_password("student123")
            student_user.save()
        Token.objects.get_or_create(user=student_user)

        self.stdout.write(
            f"  Users: admin (staff{' - new' if admin_created else ''}), "
            f"student (customer{' - new' if student_created else ''})"
        )