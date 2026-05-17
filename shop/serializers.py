from django.contrib.auth.models import User
from rest_framework import serializers

from .models import Order, OrderItem, Product


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8, write_only=True)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("username is already taken")
        return value

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("email is already registered")
        return value


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True)


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "description",
            "price",
            "stock_quantity",
            "version",
        ]


class CheckoutItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, max_value=100)


class CheckoutSerializer(serializers.Serializer):
    items = CheckoutItemSerializer(many=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("checkout requires at least one item")
        return value


class StockUpdateSerializer(serializers.Serializer):
    change = serializers.IntegerField(min_value=-1000, max_value=1000)


class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = ["product", "product_name", "quantity", "unit_price", "line_total"]

    def get_line_total(self, obj):
        return str(obj.line_total())


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "customer_email",
            "status",
            "total_amount",
            "items",
            "created_at",
        ]


class TaskResultSerializer(serializers.Serializer):
    task_id = serializers.CharField()
    task_name = serializers.CharField(allow_blank=True, allow_null=True)
    status = serializers.CharField()
    date_created = serializers.DateTimeField()
    date_done = serializers.DateTimeField(allow_null=True)
    result = serializers.CharField(allow_blank=True, allow_null=True)
