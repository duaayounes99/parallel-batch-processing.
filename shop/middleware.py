import threading

from django.conf import settings
from django.http import JsonResponse


_request_slots = threading.BoundedSemaphore(settings.MAX_CONCURRENT_REQUESTS)


class CapacityControlMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        acquired = _request_slots.acquire(timeout=settings.CAPACITY_WAIT_SECONDS)
        if not acquired:
            return JsonResponse(
                {
                    "error": "server is busy",
                    "detail": "too many requests are already being processed",
                },
                status=503,
            )

        try:
            return self.get_response(request)
        finally:
            _request_slots.release()
