
import hashlib
import logging

from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.db import transaction
from rest_framework.authtoken.models import Token

from .models import BatchJob, Order, OrderItem, Product

logger = logging.getLogger(__name__)




class DistributedLockError(Exception):
   
    pass


class OutOfStockError(Exception):
  
    pass


class StockUpdateError(Exception):
   
    pass



PRODUCT_LIST_CACHE_KEY = "products:list"
PRODUCT_LIST_TTL       = 30   # ثانية

PRODUCT_DETAIL_TTL     = 30   # ثانية

ORDERS_USER_TTL        = 15   # ثانية

BATCH_JOBS_CACHE_KEY   = "batch:jobs:latest"
BATCH_JOBS_TTL         = 5    # ثانية

USER_TOKEN_TTL         = 300  # ثانية (5 دقائق)


def product_detail_cache_key(product_id) -> str:
  
    return f"products:detail:{product_id}"


def orders_user_cache_key(user_id) -> str:
   
    return f"orders:user:{user_id}"


def invalidate_product_caches(product_id=None):
    
    cache.delete(PRODUCT_LIST_CACHE_KEY)
    if product_id is not None:
        cache.delete(product_detail_cache_key(product_id))


def invalidate_orders_cache(user_id):
   
    cache.delete(orders_user_cache_key(user_id))


def invalidate_batch_jobs_cache():
  
    cache.delete(BATCH_JOBS_CACHE_KEY)


def get_latest_batch_jobs(limit: int = 10):
 
    cached = cache.get(BATCH_JOBS_CACHE_KEY)
    if cached is not None:
        return cached

    jobs = list(
        BatchJob.objects.order_by("-started_at")[:limit].values(
            "id", "job_name", "status", "task_id", "started_at"
        )
    )
    cache.set(BATCH_JOBS_CACHE_KEY, jobs, timeout=BATCH_JOBS_TTL)
    return jobs


def get_user_orders(user):
  
    from .serializers import OrderSerializer

    cache_key = orders_user_cache_key(user.id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    orders = Order.objects.filter(user=user).prefetch_related("items__product").order_by("-created_at")
    data = OrderSerializer(orders, many=True).data
    cache.set(cache_key, data, timeout=ORDERS_USER_TTL)
    return data



REGISTER_LOCK_TIMEOUT = 5    # ثوانٍ
TOKEN_LOCK_TIMEOUT     = 5    # ثوانٍ


def register_user(username: str, email: str, password: str):
 
    lock_key = f"lock:register:{username}"
    if not cache.add(lock_key, "1", timeout=REGISTER_LOCK_TIMEOUT):
        raise DistributedLockError("طلب تسجيل سابق بنفس اسم المستخدم قيد المعالجة، حاول بعد لحظات")

    try:
        if User.objects.filter(username=username).exists():
            raise ValueError("username is already taken")
        if User.objects.filter(email=email).exists():
            raise ValueError("email is already registered")

        with transaction.atomic():
            user = User.objects.create(
                username=username,
                email=email,
                password=make_password(password),
            )
            token, _ = Token.objects.get_or_create(user=user)

        cache.set(f"token:user:{user.id}", token.key, timeout=USER_TOKEN_TTL)

        return user, token.key

    finally:
        cache.delete(lock_key)


def get_or_create_user_token(user) -> str:
  
    cache_key = f"token:user:{user.id}"
    cached_token = cache.get(cache_key)
    if cached_token is not None:
        return cached_token

    lock_key = f"lock:token:{user.id}"
    if not cache.add(lock_key, "1", timeout=TOKEN_LOCK_TIMEOUT):
        raise DistributedLockError("طلب تسجيل دخول سابق قيد المعالجة، حاول بعد لحظات")

    try:
        token, _ = Token.objects.get_or_create(user=user)
        cache.set(cache_key, token.key, timeout=USER_TOKEN_TTL)
        return token.key
    finally:
        cache.delete(lock_key)



STOCK_LOCK_TIMEOUT = 5   # ثوانٍ


def adjust_stock(product_id, change: int) -> Product:
 
    lock_key = f"lock:stock:{product_id}"
    if not cache.add(lock_key, "1", timeout=STOCK_LOCK_TIMEOUT):
        raise DistributedLockError("طلب تعديل سابق لنفس المنتج قيد المعالجة، حاول بعد لحظات")

    try:
        with transaction.atomic():
            product = Product.objects.select_for_update().get(id=product_id)

            new_quantity = product.stock_quantity + change
            if new_quantity < 0:
                raise StockUpdateError("لا يمكن أن يكون المخزون سالباً")

            product.stock_quantity = new_quantity
            product.version += 1  
            product.save(update_fields=["stock_quantity", "version"])

      
        invalidate_product_caches(product_id)

        logger.info("Stock adjusted for product %s: change=%s, new=%s",
                    product_id, change, product.stock_quantity)
        return product

    finally:
        cache.delete(lock_key)




CHECKOUT_LOCK_TIMEOUT = 10   # ثوانٍ


class CheckoutLine:


    def __init__(self, product_id: int, quantity: int):
        self.product_id = product_id
        self.quantity = quantity


def _checkout_hash(user_id, lines) -> str:

    raw = f"{user_id}:" + ",".join(f"{l.product_id}:{l.quantity}" for l in lines)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def create_order(user, lines: list) -> Order:

    if not lines:
        raise ValueError("checkout requires at least one item")

    lock_key = f"lock:checkout:{user.id}"
    if not cache.add(lock_key, _checkout_hash(user.id, lines), timeout=CHECKOUT_LOCK_TIMEOUT):
        raise DistributedLockError("طلب دفع سابق قيد المعالجة، الرجاء الانتظار")

    try:
        with transaction.atomic():
            order = Order.objects.create(
                user=user,
                customer_email=user.email,
                status=Order.Status.PENDING,
            )

            total = 0
            for line in lines:
                try:
                    product = Product.objects.select_for_update().get(id=line.product_id)
                except Product.DoesNotExist:
                    raise ValueError(f"product {line.product_id} not found")

                if product.stock_quantity < line.quantity:
                    raise OutOfStockError(f"الكمية غير متوفرة لمنتج {product.name}")

                product.stock_quantity -= line.quantity
                product.version += 1
                product.save(update_fields=["stock_quantity", "version"])

                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=line.quantity,
                    unit_price=product.price,
                )
                total += product.price * line.quantity

            order.total_amount = total
            order.status = Order.Status.PAID
            order.save(update_fields=["total_amount", "status"])
        

      
        invalidate_orders_cache(user.id)
        for line in lines:
            invalidate_product_caches(line.product_id)

   
        from .tasks import generate_invoice, log_order_analytics, send_order_confirmation
        send_order_confirmation.delay(order.id)
        generate_invoice.delay(order.id)
        log_order_analytics.delay(order.id)

        logger.info("Order %s created and paid for user %s, total=%s", order.id, user.id, total)
        return order

    finally:
        cache.delete(lock_key)
