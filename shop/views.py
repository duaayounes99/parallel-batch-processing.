

import traceback

from django.contrib.auth import authenticate
from django.shortcuts import redirect, render
from django_celery_results.models import TaskResult
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from django.core.cache import cache

from .models import BatchJob, Order, Product
from .serializers import (
    CheckoutSerializer,
    LoginSerializer,
    OrderSerializer,
    ProductSerializer,
    RegisterSerializer,
    StockUpdateSerializer,
    TaskResultSerializer,
)
from .services import (
    PRODUCT_LIST_CACHE_KEY,
    PRODUCT_LIST_TTL,
    PRODUCT_DETAIL_TTL,
    CheckoutLine,
    DistributedLockError,
    OutOfStockError,
    StockUpdateError,
    adjust_stock,
    create_order,
    get_latest_batch_jobs,
    get_or_create_user_token,
    get_user_orders,
    invalidate_batch_jobs_cache,
    product_detail_cache_key,
    register_user,
)
from .tasks import process_sales_batch_task

TASK_RESULT_CACHE_TTL = 10   # s




class HealthView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = []

    def get(self, request):
        return Response({"status": "ok"})




class RegisterView(APIView):
    """
    Caching: the token is stored in cache immediately after registration (300s).
    Distributed lock: lock:register:<username> prevents a username race condition.
    """
    permission_classes = [permissions.AllowAny]
    throttle_scope = "login"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user, token_key = register_user(
                username=serializer.validated_data["username"],
                email=serializer.validated_data["email"],
                password=serializer.validated_data["password"],
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except DistributedLockError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
                headers={"Retry-After": "5"},
            )

        return Response(
            {"user": {"id": user.id, "username": user.username, "email": user.email},
             "token": token_key},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    """
    Caching: the token is read from cache if present (300s).
    Distributed lock: lock:token:<user_id> prevents a token race on concurrent logins.
    """
    permission_classes = [permissions.AllowAny]
    throttle_scope = "login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = authenticate(
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            return Response(
                {"detail": "invalid username or password"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            token_key = get_or_create_user_token(user)
        except DistributedLockError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
                headers={"Retry-After": "5"},
            )

        return Response({"token": token_key})



class ProductListView(APIView):
    """Caching: products:list cache, 30s."""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        cached = cache.get(PRODUCT_LIST_CACHE_KEY)
        if cached is not None:
            return Response(cached)

        products = Product.objects.order_by("id")
        payload = {"products": ProductSerializer(products, many=True).data}
        cache.set(PRODUCT_LIST_CACHE_KEY, payload, timeout=PRODUCT_LIST_TTL)
        return Response(payload)


class ProductDetailView(APIView):
    """Caching: products:detail:<id> cache, 30s."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, product_id):
        cache_key = product_detail_cache_key(product_id)
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            return Response({"detail": "product not found"}, status=status.HTTP_404_NOT_FOUND)

        payload = {"product": ProductSerializer(product).data}
        cache.set(cache_key, payload, timeout=PRODUCT_DETAIL_TTL)
        return Response(payload)




class StockUpdateView(APIView):
    """
    Distributed lock: lock:stock:<id> inside adjust_stock() (services layer).
    ACID: full transaction integrity inside adjust_stock().
    Caching: cache is invalidated after COMMIT inside adjust_stock().
    """
    permission_classes = [permissions.IsAdminUser]

    def patch(self, request, product_id):
        serializer = StockUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            product = adjust_stock(product_id, serializer.validated_data["change"])
        except Product.DoesNotExist:
            return Response({"detail": "product not found"}, status=status.HTTP_404_NOT_FOUND)
        except StockUpdateError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except DistributedLockError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
                headers={"Retry-After": "5"},
            )

        return Response({"product": ProductSerializer(product).data})




class CheckoutView(APIView):
    """
    Distributed lock: lock:checkout:<hash> inside create_order() (services layer).
    ACID: full transaction integrity - the response includes "transaction": "committed".
    Caching: cache is invalidated after COMMIT inside create_order().
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "checkout"

    def post(self, request):
        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        lines = [
            CheckoutLine(product_id=item["product_id"], quantity=item["quantity"])
            for item in serializer.validated_data["items"]
        ]

        try:
            order = create_order(user=request.user, lines=lines)
        except OutOfStockError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except DistributedLockError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
                headers={"Retry-After": "5"},
            )

        saved_order = Order.objects.prefetch_related("items__product").get(id=order.id)
        return Response(
            {
                "order": OrderSerializer(saved_order).data,
                "transaction": "committed",
                "queued_background_tasks": [
                    "send_order_confirmation",
                    "generate_invoice",
                    "log_order_analytics",
                ],
            },
            status=status.HTTP_201_CREATED,
        )



class OrderListView(APIView):
    """Caching: orders:user:<id> cache, 15s, invalidated after every checkout."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        data = get_user_orders(request.user)
        return Response({"orders": data})



class TaskResultListView(APIView):
    """Caching: task_results:list cache, 10s."""
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        cache_key = "task_results:list"
        cached = cache.get(cache_key)
        if cached is not None:
            return Response({"tasks": cached})

        tasks = TaskResult.objects.order_by("-date_created")[:50]
        data = TaskResultSerializer(tasks, many=True).data
        cache.set(cache_key, data, timeout=TASK_RESULT_CACHE_TTL)
        return Response({"tasks": data})




def process_batch_view(request):
    """
    GET  -> Dashboard - batch jobs read from cache (TTL 5s).
    POST -> trigger a new batch via Celery + invalidate the cache immediately.

    Caching:
        GET:  get_latest_batch_jobs() reads from cache (5s TTL).
        POST: invalidate_batch_jobs_cache() invalidates the cache immediately
              so the user sees the new job on the very next refresh.

    Distributed lock:
        lock:batch inside process_sales_batch_task (tasks.py) guarantees
        that only one batch runs at a time.
    """
    if request.method == "POST":
        with __import__("django.db", fromlist=["transaction"]).transaction.atomic():
            job = BatchJob.objects.create(job_name="Sales Inventory Batch", status="pending")
            try:
                task = process_sales_batch_task.delay(job.id)
                job.task_id = task.id
                job.save(update_fields=["task_id"])
            except Exception:
                job.status = "failed"
                job.save(update_fields=["status"])
                traceback.print_exc()

      
        invalidate_batch_jobs_cache()
        return redirect("process_batch")

 
    jobs = get_latest_batch_jobs(limit=10)
    return render(request, "shop/dashboard.html", {"jobs": jobs})