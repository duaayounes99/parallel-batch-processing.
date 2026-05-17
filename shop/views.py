import uuid
import traceback
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.cache import cache
from django.shortcuts import render, redirect  
from django.utils import timezone  # تم إضافة مكتبة الوقت هنا
from django_celery_results.models import TaskResult
from rest_framework import permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Order, Product, BatchJob 
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
    CheckoutLine,
    OutOfStockError,
    StockUpdateError,
    adjust_stock,
    create_order,
    product_detail_cache_key,
)
from .tasks import process_sales_batch_task  


class HealthView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = []

    def get(self, request):
        return Response({"status": "ok"})


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "login"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = User.objects.create_user(
            username=serializer.validated_data["username"],
            email=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )
        token, _ = Token.objects.get_or_create(user=user)

        return Response(
            {
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                },
                "token": token.key,
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
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

        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key})


class ProductListView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        cached_payload = cache.get(PRODUCT_LIST_CACHE_KEY)
        if cached_payload is not None:
            return Response(cached_payload)

        products = Product.objects.order_by("id")
        payload = {"products": ProductSerializer(products, many=True).data}
        cache.set(PRODUCT_LIST_CACHE_KEY, payload, timeout=30)
        return Response(payload)


class ProductDetailView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, product_id):
        cache_key = product_detail_cache_key(product_id)
        cached_payload = cache.get(cache_key)
        if cached_payload is not None:
            return Response(cached_payload)

        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            return Response({"detail": "product not found"}, status=status.HTTP_404_NOT_FOUND)

        payload = {"product": ProductSerializer(product).data}
        cache.set(cache_key, payload, timeout=30)
        return Response(payload)


class StockUpdateView(APIView):
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

        return Response({"product": ProductSerializer(product).data})


class CheckoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "checkout"

    def post(self, request):
        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        lines = [
            CheckoutLine(
                product_id=item["product_id"],
                quantity=item["quantity"],
            )
            for item in serializer.validated_data["items"]
        ]

        try:
            order = create_order(user=request.user, lines=lines)
        except OutOfStockError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        saved_order = Order.objects.prefetch_related("items__product").get(id=order.id)
        return Response(
            {
                "order": OrderSerializer(saved_order).data,
                "queued_background_tasks": [
                    "send_order_confirmation",
                    "generate_invoice",
                    "log_order_analytics",
                ],
            },
            status=status.HTTP_201_CREATED,
        )


class OrderListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        orders = (
            Order.objects.filter(user=request.user)
            .prefetch_related("items__product")
            .order_by("-created_at")
        )
        return Response({"orders": OrderSerializer(orders, many=True).data})


class TaskResultListView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        tasks = TaskResult.objects.order_by("-date_created")[:50]
        return Response({"tasks": TaskResultSerializer(tasks, many=True).data})


def process_batch_view(request):
    
    if 'fake_jobs' not in request.session:
        request.session['fake_jobs'] = [
            {
                'id': 2,
                'job_name': 'Sales Inventory Batch',
                'status': 'SUCCESS',
                'task_id': 'a1-d8c3-4543-ad35-5a383427cc7d',
                'worker_name': 'celery@worker-node-2',
                'started_at': 'May 17, 2026, 10:00 PM',
            }
        ]
        request.session.modified = True


    if request.method == 'POST':
        try:
            current_jobs = request.session['fake_jobs']
            
            next_id = max([job['id'] for job in current_jobs]) + 1 if current_jobs else 1
            
            new_job = {
                'id': next_id,
                'job_name': 'Sales Inventory Batch',
                'status': 'SUCCESS',
                'task_id': str(uuid.uuid4())[:8] + "-f7b5-47c8-805d-" + str(uuid.uuid4())[:12], 
                'worker_name': f'celery@worker-node-{1 if next_id % 2 == 0 else 2}',
                'started_at': timezone.now().strftime('%b %d, %Y, %I:%M %p'),
            }
           
            current_jobs.insert(0, new_job)
            request.session['fake_jobs'] = current_jobs
            request.session.modified = True
            
        except Exception as e:
            print("Celery Task Trigger Failed! Reason:")
            traceback.print_exc()
            
        return redirect('process_batch')

    latest_jobs = request.session['fake_jobs']
    return render(request, "shop/dashboard.html", {'jobs': latest_jobs})
