from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from shop.models import Product


class Command(BaseCommand):
    help = "Creates demo products for local testing."

    def handle(self, *args, **options):
        products = [
            ("Laptop Stand", "Aluminum stand for a desk setup", Decimal("29.99"), 50),
            ("Wireless Mouse", "Ergonomic mouse with USB receiver", Decimal("18.50"), 80),
            ("Mechanical Keyboard", "Compact keyboard for programmers", Decimal("64.00"), 30),
        ]

        for name, description, price, stock in products:
            Product.objects.update_or_create(
                name=name,
                defaults={
                    "description": description,
                    "price": price,
                    "stock_quantity": stock,
                },
            )

        student, created = User.objects.get_or_create(
            username="student",
            defaults={"email": "student@example.com"},
        )
        if created:
            student.set_password("student123")
            student.save(update_fields=["password"])

        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={"email": "admin@example.com", "is_staff": True, "is_superuser": True},
        )
        if created:
            admin.set_password("admin12345")
            admin.save(update_fields=["password"])

        self.stdout.write(self.style.SUCCESS("Demo products and users are ready"))
