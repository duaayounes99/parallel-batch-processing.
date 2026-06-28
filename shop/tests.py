"""
test_shop.py
------------
Comprehensive tests covering:
  • Services: create_order, adjust_stock, caching helpers
  • Distributed lock: acquire / release / collision
  • Views: all endpoints (via DRF APIClient)
  • Tasks: smoke tests with eager execution
  • Batch: distributed lock prevents double-run
"""

from __future__ import annotations

import threading
import time
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient, APITestCase

from shop.models import BatchJob, Order, OrderItem, Product
from shop.services import (
    CheckoutLine,
    DistributedLockError,
    OutOfStockError,
    StockUpdateError,
    _checkout_lock_name,
    _merge_duplicate_lines,
    adjust_stock,
    create_order,
    distributed_lock,
    get_user_order_count,
    get_user_orders,
    order_count_cache_key,
    order_list_cache_key,
    product_detail_cache_key,
)


# ---------------------------------------------------------------------------
# Test helpers / base classes
# ---------------------------------------------------------------------------

def make_product(name="Widget", price="10.00", stock=50) -> Product:
    return Product.objects.create(
        name=name,
        price=Decimal(price),
        stock_quantity=stock,
    )


def make_user(username="buyer", password="pass1234") -> User:
    return User.objects.create_user(username=username, email=f"{username}@test.com", password=password)


class CacheResetMixin:
    """Clear the cache before every test to avoid inter-test pollution."""
    def setUp(self):
        super().setUp()
        cache.clear()


# ---------------------------------------------------------------------------
# 1. Service: create_order
# ---------------------------------------------------------------------------

class CreateOrderTests(CacheResetMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.product = make_product(stock=10)

    def test_creates_order_and_reduces_stock(self):
        lines = [CheckoutLine(product_id=self.product.id, quantity=3)]
        order = create_order(self.user, lines)

        self.assertEqual(order.status, Order.Status.PAID)
        self.assertEqual(order.total_amount, Decimal("30.00"))

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 7)

    def test_raises_out_of_stock(self):
        lines = [CheckoutLine(product_id=self.product.id, quantity=100)]
        with self.assertRaises(OutOfStockError):
            create_order(self.user, lines)

    def test_raises_on_empty_lines(self):
        with self.assertRaises(ValueError):
            create_order(self.user, [])

    def test_raises_on_unknown_product(self):
        lines = [CheckoutLine(product_id=999_999, quantity=1)]
        with self.assertRaises(ValueError, msg="unknown product ids"):
            create_order(self.user, lines)

    def test_merges_duplicate_lines(self):
        """Two lines for the same product should be merged before validation."""
        lines = [
            CheckoutLine(product_id=self.product.id, quantity=2),
            CheckoutLine(product_id=self.product.id, quantity=3),
        ]
        order = create_order(self.user, lines)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 5)

    def test_version_incremented_on_checkout(self):
        original_version = self.product.version
        lines = [CheckoutLine(product_id=self.product.id, quantity=1)]
        create_order(self.user, lines)
        self.product.refresh_from_db()
        self.assertEqual(self.product.version, original_version + 1)

    def test_order_items_created(self):
        lines = [CheckoutLine(product_id=self.product.id, quantity=2)]
        order = create_order(self.user, lines)
        items = OrderItem.objects.filter(order=order)
        self.assertEqual(items.count(), 1)
        self.assertEqual(items.first().quantity, 2)

    @patch("shop.services.distributed_lock")
    def test_distributed_lock_called_during_checkout(self, mock_lock):
        """Checkout must enter the distributed lock context manager."""
        mock_lock.return_value.__enter__ = lambda s: s
        mock_lock.return_value.__exit__  = MagicMock(return_value=False)
        lines = [CheckoutLine(product_id=self.product.id, quantity=1)]
        create_order(self.user, lines)
        mock_lock.assert_called_once()


# ---------------------------------------------------------------------------
# 2. Service: adjust_stock
# ---------------------------------------------------------------------------

class AdjustStockTests(CacheResetMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.product = make_product(stock=20)

    def test_increase_stock(self):
        adjust_stock(self.product.id, 10)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 30)

    def test_decrease_stock(self):
        adjust_stock(self.product.id, -5)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 15)

    def test_zero_change_raises(self):
        with self.assertRaises(StockUpdateError):
            adjust_stock(self.product.id, 0)

    def test_negative_result_raises(self):
        with self.assertRaises(StockUpdateError):
            adjust_stock(self.product.id, -999)

    def test_version_incremented(self):
        original = self.product.version
        adjust_stock(self.product.id, 5)
        self.product.refresh_from_db()
        self.assertEqual(self.product.version, original + 1)


# ---------------------------------------------------------------------------
# 3. Distributed lock (Request 7)
# ---------------------------------------------------------------------------

class DistributedLockTests(CacheResetMixin, TestCase):

    def test_lock_acquired_and_released(self):
        with distributed_lock("test:lock:basic"):
            self.assertIsNotNone(cache.get("test:lock:basic"))
        self.assertIsNone(cache.get("test:lock:basic"))

    def test_lock_raises_when_already_held(self):
        """Second acquire should fail immediately (wait=0)."""
        cache.set("test:lock:held", "1", timeout=30)
        with self.assertRaises(DistributedLockError):
            with distributed_lock("test:lock:held", wait=0):
                pass

    def test_lock_released_even_on_exception(self):
        """The lock must be released even if the body raises."""
        try:
            with distributed_lock("test:lock:exc"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertIsNone(cache.get("test:lock:exc"))

    def test_concurrent_threads_serialised(self):
        """
        Two threads compete for the same lock.
        Only one should run at a time (non-overlapping critical sections).
        """
        timeline = []
        errors   = []

        def worker(name):
            try:
                with distributed_lock("test:lock:concurrent", timeout=5, wait=5):
                    timeline.append(f"{name}:enter")
                    time.sleep(0.05)
                    timeline.append(f"{name}:exit")
            except Exception as exc:
                errors.append(str(exc))

        t1 = threading.Thread(target=worker, args=("A",))
        t2 = threading.Thread(target=worker, args=("B",))
        t1.start(); t2.start()
        t1.join();  t2.join()

        self.assertFalse(errors, errors)
        # A:enter must be followed by A:exit before B:enter (or vice-versa)
        enter_A = timeline.index("A:enter")
        exit_A  = timeline.index("A:exit")
        enter_B = timeline.index("B:enter")
        exit_B  = timeline.index("B:exit")
        # No overlap: [A:enter … A:exit … B:enter … B:exit] OR the B-first variant
        self.assertLess(exit_A, enter_B) or self.assertLess(exit_B, enter_A)

    def test_checkout_lock_name_is_deterministic(self):
        ids = [3, 1, 2]
        self.assertEqual(_checkout_lock_name(ids), _checkout_lock_name([2, 3, 1]))


# ---------------------------------------------------------------------------
# 4. Cache helpers (Request 6)
# ---------------------------------------------------------------------------

class CacheHelperTests(CacheResetMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user("cacheuser")
        self.product = make_product(stock=100)

    def test_order_list_cache_hit(self):
        """Second call to get_user_orders should be served from cache."""
        lines = [CheckoutLine(product_id=self.product.id, quantity=1)]
        create_order(self.user, lines)

        with self.assertNumQueries(1):   # first call: 1 DB query
            get_user_orders(self.user)

        with self.assertNumQueries(0):   # second call: 0 DB queries (cache hit)
            get_user_orders(self.user)

    def test_order_count_cache(self):
        Product.objects.create(name="P2", price=Decimal("5.00"), stock_quantity=100)
        count = get_user_order_count(self.user.id)
        self.assertEqual(count, Order.objects.filter(user=self.user).count())

    def test_product_detail_cache_key_format(self):
        self.assertEqual(product_detail_cache_key(42), "products:detail:42")

    def test_order_list_cache_key_format(self):
        self.assertEqual(order_list_cache_key(7), "orders:user:7")

    def test_order_count_cache_key_format(self):
        self.assertEqual(order_count_cache_key(7), "orders:count:7")


# ---------------------------------------------------------------------------
# 5. Merge duplicate lines helper
# ---------------------------------------------------------------------------

class MergeDuplicateLinesTests(TestCase):

    def test_merges_correctly(self):
        lines = [
            CheckoutLine(product_id=1, quantity=2),
            CheckoutLine(product_id=1, quantity=3),
            CheckoutLine(product_id=2, quantity=1),
        ]
        merged = _merge_duplicate_lines(lines)
        by_id = {line.product_id: line.quantity for line in merged}
        self.assertEqual(by_id[1], 5)
        self.assertEqual(by_id[2], 1)

    def test_no_duplicates_unchanged_total(self):
        lines = [CheckoutLine(product_id=i, quantity=1) for i in range(1, 6)]
        merged = _merge_duplicate_lines(lines)
        self.assertEqual(len(merged), 5)


# ---------------------------------------------------------------------------
# 6. API Views (Request 10 – complete coverage)
# ---------------------------------------------------------------------------

class HealthViewTests(APITestCase):
    def test_health_ok(self):
        resp = self.client.get(reverse("health"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json(), {"status": "ok"})


class AuthViewTests(CacheResetMixin, APITestCase):
    def test_register(self):
        resp = self.client.post(reverse("register"), {
            "username": "newuser", "email": "new@test.com", "password": "Secure123"
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn("token", resp.json())

    def test_register_duplicate_username(self):
        make_user("dup")
        resp = self.client.post(reverse("register"), {
            "username": "dup", "email": "dup2@test.com", "password": "Secure123"
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_success(self):
        make_user("loginuser", password="mypass")
        resp = self.client.post(reverse("login"), {
            "username": "loginuser", "password": "mypass"
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("token", resp.json())

    def test_login_bad_password(self):
        make_user("loginuser2")
        resp = self.client.post(reverse("login"), {
            "username": "loginuser2", "password": "wrong"
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class ProductViewTests(CacheResetMixin, APITestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.p = make_product("Gizmo", stock=20)

    def test_product_list(self):
        resp = self.client.get(reverse("product-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("products", resp.json())

    def test_product_list_cached(self):
        self.client.get(reverse("product-list"))   # warm cache
        # Second request must be served from cache (0 DB queries)
        with self.assertNumQueries(0):
            self.client.get(reverse("product-list"))

    def test_product_detail(self):
        resp = self.client.get(reverse("product-detail", args=[self.p.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["product"]["name"], "Gizmo")

    def test_product_detail_not_found(self):
        resp = self.client.get(reverse("product-detail", args=[999_999]))
        self.assertEqual(resp.status_code, 404)


class StockUpdateViewTests(CacheResetMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.product = make_product(stock=10)
        self.admin   = User.objects.create_superuser("admin_su", "a@a.com", "adminpass")
        token, _     = Token.objects.get_or_create(user=self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def test_stock_increase(self):
        resp = self.client.patch(
            reverse("stock-update", args=[self.product.id]),
            {"change": 5}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 15)

    def test_stock_decrease_to_zero(self):
        resp = self.client.patch(
            reverse("stock-update", args=[self.product.id]),
            {"change": -10}, format="json"
        )
        self.assertEqual(resp.status_code, 200)

    def test_stock_negative_rejected(self):
        resp = self.client.patch(
            reverse("stock-update", args=[self.product.id]),
            {"change": -999}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_non_admin_forbidden(self):
        user = make_user("regular")
        t, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {t.key}")
        resp = self.client.patch(
            reverse("stock-update", args=[self.product.id]),
            {"change": 1}, format="json"
        )
        self.assertEqual(resp.status_code, 403)


class CheckoutViewTests(CacheResetMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user    = make_user("buyer")
        token, _     = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        self.product = make_product(stock=50)

    def test_successful_checkout(self):
        resp = self.client.post(
            reverse("checkout"),
            {"items": [{"product_id": self.product.id, "quantity": 2}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertIn("order", data)
        self.assertIn("queued_background_tasks", data)

    def test_out_of_stock(self):
        resp = self.client.post(
            reverse("checkout"),
            {"items": [{"product_id": self.product.id, "quantity": 1000}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 409)

    def test_unauthenticated_rejected(self):
        self.client.credentials()   # clear token
        resp = self.client.post(
            reverse("checkout"),
            {"items": [{"product_id": self.product.id, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_distributed_lock_error_returns_503(self):
        with patch("shop.views.create_order", side_effect=DistributedLockError("busy")):
            resp = self.client.post(
                reverse("checkout"),
                {"items": [{"product_id": self.product.id, "quantity": 1}]},
                format="json",
            )
        self.assertEqual(resp.status_code, 503)


class OrderListViewTests(CacheResetMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user    = make_user("orderer")
        token, _     = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        self.product = make_product(stock=100)

    def test_returns_user_orders(self):
        create_order(self.user, [CheckoutLine(product_id=self.product.id, quantity=1)])
        resp = self.client.get(reverse("order-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["orders"]), 1)

    def test_cached_on_second_call(self):
        create_order(self.user, [CheckoutLine(product_id=self.product.id, quantity=1)])
        self.client.get(reverse("order-list"))   # warm cache
        with self.assertNumQueries(0):
            resp = self.client.get(reverse("order-list"))
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# 7. Batch task & distributed lock (Request 7 + 10)
# ---------------------------------------------------------------------------

class BatchTaskTests(CacheResetMixin, TestCase):

    def setUp(self):
        super().setUp()
        # Enable eager (synchronous) task execution for testing
        from django.test.utils import override_settings
        self.product = make_product(stock=100)
        user = make_user("batchbuyer")
        create_order(user, [CheckoutLine(product_id=self.product.id, quantity=1)])

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_batch_completes(self):
        from shop.tasks import process_sales_batch_task

        job = BatchJob.objects.create(job_name="Test Batch", status="pending")
        process_sales_batch_task(job.id)

        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        self.assertTrue(Order.objects.filter(is_processed=True).exists())

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_second_batch_skipped_when_lock_held(self):
        """
        Simulate the distributed lock already being held by job A.
        Job B should be marked 'skipped' immediately.
        """
        from shop.tasks import BATCH_LOCK_KEY, process_sales_batch_task

        job_a = BatchJob.objects.create(job_name="Job A", status="pending")
        job_b = BatchJob.objects.create(job_name="Job B", status="pending")

        # Manually acquire the lock as job_a would
        cache.set(BATCH_LOCK_KEY, str(job_a.id), timeout=60)

        process_sales_batch_task(job_b.id)

        job_b.refresh_from_db()
        self.assertEqual(job_b.status, "skipped")