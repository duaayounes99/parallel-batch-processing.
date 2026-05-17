from dataclasses import dataclass
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction

from .models import Order, OrderItem, Product


class OutOfStockError(Exception):
    pass


@dataclass(frozen=True)
class CheckoutLine:
    product_id: int
    quantity: int


class StockUpdateError(Exception):
    pass


PRODUCT_LIST_CACHE_KEY = "products:list"


def product_detail_cache_key(product_id: int) -> str:
    return f"products:detail:{product_id}"


def create_order(user, lines: list[CheckoutLine]) -> Order:
    if not lines:
        raise ValueError("checkout requires at least one item")

    normalized_lines = _merge_duplicate_lines(lines)

    with transaction.atomic():
        product_ids = [line.product_id for line in normalized_lines]
        # Pessimistic locking:
        # select_for_update asks the database to lock these product rows until
        # the transaction finishes. PostgreSQL will make another checkout wait
        # instead of reading the same stock at the same time.
        locked_products = (
            Product.objects.select_for_update()
            .filter(id__in=product_ids)
            .order_by("id")
        )
        products = {product.id: product for product in locked_products}

        missing = sorted(set(product_ids) - set(products))
        if missing:
            raise ValueError(f"unknown product ids: {missing}")

        total = Decimal("0.00")
        for line in normalized_lines:
            if line.quantity <= 0:
                raise ValueError("quantity must be greater than zero")

            product = products[line.product_id]
            if product.stock_quantity < line.quantity:
                raise OutOfStockError(
                    f"not enough stock for {product.name}: requested {line.quantity}"
                )

            product.stock_quantity -= line.quantity
            product.version += 1
            product.save(update_fields=["stock_quantity", "version", "updated_at"])

            total += product.price * line.quantity

        order = Order.objects.create(
            user=user,
            customer_email=user.email or user.username,
            status=Order.Status.PAID,
            total_amount=total,
        )

        OrderItem.objects.bulk_create(
            [
                OrderItem(
                    order=order,
                    product=products[line.product_id],
                    quantity=line.quantity,
                    unit_price=products[line.product_id].price,
                )
                for line in normalized_lines
            ]
        )

        changed_product_ids = [line.product_id for line in normalized_lines]
        transaction.on_commit(lambda: _after_successful_checkout(order.id, changed_product_ids))

    return order


def adjust_stock(product_id: int, change: int) -> Product:
    if change == 0:
        raise StockUpdateError("stock change cannot be zero")

    with transaction.atomic():
        product = Product.objects.select_for_update().get(id=product_id)
        new_stock = product.stock_quantity + change
        if new_stock < 0:
            raise StockUpdateError("stock update would make inventory negative")

        product.stock_quantity = new_stock
        product.version += 1
        product.save(update_fields=["stock_quantity", "version", "updated_at"])

        transaction.on_commit(lambda: _clear_product_cache([product_id]))

    return product


def _merge_duplicate_lines(lines: list[CheckoutLine]) -> list[CheckoutLine]:
    quantities: dict[int, int] = {}
    for line in lines:
        quantities[line.product_id] = quantities.get(line.product_id, 0) + line.quantity
    return [
        CheckoutLine(product_id=product_id, quantity=quantity)
        for product_id, quantity in quantities.items()
    ]


def _after_successful_checkout(order_id: int, product_ids: list[int]) -> None:
    _clear_product_cache(product_ids)

    # Importing here avoids a circular import and keeps the service easy to read.
    from .tasks import (
        generate_invoice,
        log_order_analytics,
        send_order_confirmation,
    )

    send_order_confirmation.delay(order_id)
    generate_invoice.delay(order_id)
    log_order_analytics.delay(order_id)


def _clear_product_cache(product_ids: list[int]) -> None:
    cache.delete(PRODUCT_LIST_CACHE_KEY)
    cache.delete_many([product_detail_cache_key(product_id) for product_id in product_ids])
