"""
aop_monitoring.py
------------------
Aspect-Oriented Programming (AOP) layer for performance monitoring.

Concept:
    AOP separates a "cross-cutting concern" (here: performance monitoring,
    i.e. timing + logging) from the core business logic of each view.
    Instead of writing timing/logging code inside every view function
    (which would scatter the same boilerplate across the codebase), we
    write it ONCE here as a decorator (the "aspect"), and apply it to any
    view we want monitored, without touching that view's actual logic.

This is the standard way to approximate AOP in Python/Django, since
Django itself has no native AOP framework (unlike Spring AOP in Java).
The decorator acts as an "advice" that runs:
    - BEFORE the view executes  (record start time)
    - AFTER the view executes   (record end time, compute duration, log it)
    regardless of what the view itself does internally.

Where it is applied (see views.py):
    @monitor_performance
    def get(self, request):
        ...

This single decorator is reused across ProductListView, CheckoutView,
StockUpdateView, etc. The monitoring logic is written exactly once.
"""

import functools
import logging
import time

logger = logging.getLogger("aop.performance")


def monitor_performance(view_method):
    """
    Decorator (the AOP "advice") that wraps any DRF view method
    (get/post/patch/...) and logs:
        - the view name
        - the HTTP method
        - the execution time in milliseconds
        - the final HTTP status code returned
        - whether an exception was raised

    The wrapped view method's own logic is completely untouched -
    this decorator only observes its execution from the outside,
    which is the core idea of AOP: separation of a cross-cutting
    concern (monitoring) from business logic.
    """

    @functools.wraps(view_method)
    def wrapper(self, request, *args, **kwargs):
        start = time.perf_counter()
        view_name = f"{self.__class__.__name__}.{view_method.__name__}"

        try:
            response = view_method(self, request, *args, **kwargs)
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "[AOP] %s %s -> status=%s duration=%.2fms",
                request.method,
                view_name,
                getattr(response, "status_code", "?"),
                duration_ms,
            )
            return response

        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            http_method = getattr(request, "method", "?")
            logger.error(
                "[AOP] %s %s -> EXCEPTION=%s duration=%.2fms",
                http_method,
                view_name,
                exc.__class__.__name__,
                duration_ms,
            )
            raise

    return wrapper