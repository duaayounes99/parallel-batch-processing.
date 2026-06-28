import statistics
import time
import urllib.request

BASE_URL = "http://localhost:8000"
NUM_REQUESTS = 50


def timed_request(url):
    start = time.perf_counter()
    try:
        urllib.request.urlopen(url, timeout=5)
    except Exception as exc:
        print("warning: request failed:", exc)
        return -1
    return (time.perf_counter() - start) * 1000


def run_benchmark(label, url, n):
    print("Running", n, "requests -", label)
    timings = []
    for i in range(n):
        t = timed_request(url)
        if t >= 0:
            timings.append(t)
    return timings


def print_stats(label, timings):
    if not timings:
        print(label, ": no valid data")
        return
    print(label)
    print("  avg    :", round(statistics.mean(timings), 2), "ms")
    print("  median :", round(statistics.median(timings), 2), "ms")
    print("  max    :", round(max(timings), 2), "ms")
    print("  min    :", round(min(timings), 2), "ms")


print("=" * 60)
print("Benchmark: GET /products/  (cache effect)")
print("=" * 60)

url = BASE_URL + "/products/"

first_request_timing = run_benchmark("first request batch (cache warm-up)", url, 5)
print_stats("First 5 requests (may include cache misses)", first_request_timing)

print()
cache_timings = run_benchmark("cached requests (Redis warm)", url, NUM_REQUESTS)
print_stats("Cached requests (Redis)", cache_timings)

print("=" * 60)
print("NOTE: this measures cache HIT performance only.")
print("To measure a true cache MISS baseline, set CACHES to DummyCache")
print("in settings.py, restart the server, run this script again,")
print("then compare the two 'avg' numbers.")
print("=" * 60)