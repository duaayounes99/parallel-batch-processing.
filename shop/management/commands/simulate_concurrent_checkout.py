from concurrent.futures import ThreadPoolExecutor, as_completed

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from shop.models import Product
from shop.services import CheckoutLine, OutOfStockError, create_order


class Command(BaseCommand):
    help = "Simulates many buyers trying to buy the same product at the same time."

    def add_arguments(self, parser):
        parser.add_argument("--buyers", type=int, default=20)
        parser.add_argument("--stock", type=int, default=5)
        parser.add_argument("--product-id", type=int, default=None)

    def handle(self, *args, **options):
        product = self._get_product(options)
        product.stock_quantity = options["stock"]
        product.version = 0
        product.save(update_fields=["stock_quantity", "version"])

        buyers = options["buyers"]
        self.stdout.write(
            f"Starting concurrency demo: {buyers} buyers, stock={product.stock_quantity}"
        )

        results = []
        with ThreadPoolExecutor(max_workers=buyers) as executor:
            futures = [
                executor.submit(self._attempt_checkout, index, product.id)
                for index in range(buyers)
            ]
            for future in as_completed(futures):
                results.append(future.result())

        product.refresh_from_db()
        success_count = results.count("success")
        rejected_count = results.count("out_of_stock")

        self.stdout.write(self.style.SUCCESS(f"Successful orders: {success_count}"))
        self.stdout.write(self.style.WARNING(f"Rejected orders: {rejected_count}"))
        self.stdout.write(f"Final stock: {product.stock_quantity}")

    def _get_product(self, options):
        if options["product_id"]:
            return Product.objects.get(id=options["product_id"])

        product, _ = Product.objects.get_or_create(
            name="Concurrency Demo Item",
            defaults={
                "description": "Small stock product used for race condition demos",
                "price": "10.00",
                "stock_quantity": options["stock"],
            },
        )
        return product

    def _attempt_checkout(self, index, product_id):
        close_old_connections()
        username = f"buyer_{index}"
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": f"{username}@example.com"},
        )
        if created:
            user.set_password("buyer12345")
            user.save(update_fields=["password"])

        try:
            create_order(user=user, lines=[CheckoutLine(product_id=product_id, quantity=1)])
            return "success"
        except OutOfStockError:
            return "out_of_stock"
        finally:
            close_old_connections()
