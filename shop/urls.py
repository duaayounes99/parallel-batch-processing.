from django.urls import path

from .views import (
    CheckoutView,
    HealthView,
    LoginView,
    OrderListView,
    ProductDetailView,
    ProductListView,
    RegisterView,
    StockUpdateView,
    TaskResultListView,
    process_batch_view,  # 🚀 استيراد الواجهة الخاصة بك هنا
)

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/login/", LoginView.as_view(), name="login"),
    path("products/", ProductListView.as_view(), name="product-list"),
    path("products/<int:product_id>/", ProductDetailView.as_view(), name="product-detail"),
    path("products/<int:product_id>/stock/", StockUpdateView.as_view(), name="stock-update"),
    path("checkout/", CheckoutView.as_view(), name="checkout"),
    path("orders/", OrderListView.as_view(), name="order-list"),
    path("tasks/results/", TaskResultListView.as_view(), name="task-result-list"),
    
    # 🚀 المسار الخاص بشغلك (لوحة جرد المبيعات وتوزيع الأحمال)
    path("process-batch/", process_batch_view, name="process_batch"),
]