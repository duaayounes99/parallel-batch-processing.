

import random
import string
import time

from locust import HttpUser, TaskSet, between, events, task




def _random_str(n=10):
    return "".join(random.choices(string.ascii_lowercase, k=n))


PRODUCT_IDS = list(range(1, 11))   # product IDs 1-10 (created by seed_demo)


def _register_or_fallback(client, is_admin=False):
    """
    Register a new user and return the token.
    If registration fails -> fall back to logging in with a seed_demo account.

    Caching: POST /auth/register/ stores the token in cache immediately.
    Distributed lock: the services layer sets lock:register:<username>.
    """
    username = _random_str(12)
    email = f"{username}@test.com"
    password = "Passw0rd!"

    resp = client.post(
        "/auth/register/",
        json={"username": username, "email": email, "password": password},
        name="POST /auth/register/",
    )
    if resp.status_code in (200, 201):
        return resp.json().get("token")

    
    creds = (
        {"username": "admin", "password": "admin12345"} if is_admin
        else {"username": "student", "password": "student123"}
    )
    login = client.post("/auth/login/", json=creds, name="POST /auth/login/ [fallback]")
    if login.status_code == 200:
        return login.json().get("token")
    return None




class ShopperTasks(TaskSet):
    """
    Regular shopper - 70% of the load.
    Task weights reflect realistic usage:
        browse (5x) > product detail (4x) > checkout (2x) = orders (2x) > health (1x)
    """

    token = None

    def on_start(self):
        self.token = _register_or_fallback(self.client)

    def _auth(self):
        return {"Authorization": f"Token {self.token}"} if self.token else {}

    @task(5)
    def browse_products(self):
        """Caching: served from products:list cache (30s)."""
        self.client.get("/products/", name="GET /products/")

    @task(4)
    def product_detail(self):
        """Caching: served from products:detail:<id> cache (30s)."""
        pid = random.choice(PRODUCT_IDS)
        self.client.get(f"/products/{pid}/", name="GET /products/<id>/")

    @task(2)
    def checkout(self):
        """
        Distributed lock: lock:checkout:<hash> prevents a race.
        ACID: response includes "transaction": "committed".
        409 = out of stock | 503 = lock busy -> both expected under load.
        """
        if not self.token:
            return
        items = [{"product_id": random.choice(PRODUCT_IDS), "quantity": random.randint(1, 2)}]
        resp = self.client.post(
            "/checkout/",
            json={"items": items},
            headers=self._auth(),
            name="POST /checkout/",
        )
        if resp.status_code not in (201, 400, 409, 503):
            resp.failure(f"Unexpected checkout status: {resp.status_code}")

    @task(2)
    def my_orders(self):
        """Caching: served from orders:user:<id> cache (15s)."""
        if not self.token:
            return
        self.client.get("/orders/", headers=self._auth(), name="GET /orders/")

    @task(1)
    def health(self):
        self.client.get("/health/", name="GET /health/")


class AdminTasks(TaskSet):
    """
    Admin staff - 20% of the load.
    Weights: adjust_stock (3x) > task_results (2x) > health (1x)
    """

    token = None

    def on_start(self):
        self.token = _register_or_fallback(self.client, is_admin=True)

    def _auth(self):
        return {"Authorization": f"Token {self.token}"} if self.token else {}

    @task(3)
    def adjust_stock(self):
        """
        Distributed lock: lock:stock:<id> serializes concurrent updates.
        ACID applies inside adjust_stock().
        400 = negative stock | 503 = lock busy -> both expected.
        """
        if not self.token:
            return
        pid = random.choice(PRODUCT_IDS)
        change = random.choice([-3, -1, 5, 10, 20])
        resp = self.client.patch(
            f"/products/{pid}/stock/",
            json={"change": change},
            headers=self._auth(),
            name="PATCH /products/<id>/stock/",
        )
        if resp.status_code not in (200, 400, 403, 404, 503):
            resp.failure(f"Unexpected stock status: {resp.status_code}")

    @task(2)
    def task_results(self):
        """Caching: task_results:list cache (10s)."""
        if not self.token:
            return
        self.client.get("/tasks/results/", headers=self._auth(), name="GET /tasks/results/")

    @task(1)
    def health(self):
        self.client.get("/health/", name="GET /health/")


class BatchTasks(TaskSet):
    """
    Batch operator - 10% of the load.

    Distributed lock: concurrent triggers reveal the lock:
        10 BatchUsers trigger POST /process-batch/ -> most will return "skipped"
        because lock:batch is held by the first batch that starts.

    Caching:
        GET /process-batch/ reads jobs from batch:jobs:latest cache (5s).
        POST /process-batch/ invalidates that cache immediately.
    """

    def on_start(self):
        
        self.client.get("/process-batch/", name="GET /process-batch/ [init]")

    @task(3)
    def view_dashboard(self):
        """Caching: batch:jobs:latest cache (5s)."""
        self.client.get("/process-batch/", name="GET /process-batch/")

    @task(1)
    def trigger_batch(self):
        """
        Distributed lock: concurrent triggers -> lock:batch prevents a double run.
        redirect (302) = success | a second batch will be recorded as status="skipped".
        """
        get_resp = self.client.get("/process-batch/", name="GET /process-batch/ [pre-trigger]")
        csrf = get_resp.cookies.get("csrftoken", "")

        self.client.post(
            "/process-batch/",
            data={},
            headers={"X-CSRFToken": csrf, "Referer": self.client.base_url},
            name="POST /process-batch/",
            allow_redirects=False,  
        )
        time.sleep(random.uniform(1.0, 3.0))  



class ShopperUser(HttpUser):
    """Regular shopper - 70 out of 100."""
    tasks = [ShopperTasks]
    weight = 70
    wait_time = between(0.5, 2.5)   


class AdminUser(HttpUser):
    """Admin staff - 20 out of 100."""
    tasks = [AdminTasks]
    weight = 20
    wait_time = between(1, 4)


class BatchUser(HttpUser):
    """Batch operator - 10 out of 100."""
    tasks = [BatchTasks]
    weight = 10
    wait_time = between(2, 8)   



@events.quitting.add_listener
def on_quitting(environment, **kwargs):
    """
    Prints a full summary when the test finishes:
    - total requests and total failures
    - failure rate, with a warning if it exceeds 5%
    - average response time and RPS
    """
    stats = environment.stats.total
    if stats.num_requests == 0:
        return

    failure_rate = (stats.num_failures / stats.num_requests) * 100
    print("\n" + "=" * 60)
    print("Load Test Summary (100 concurrent users)")
    print("=" * 60)
    print(f"  Total requests   : {stats.num_requests:,}")
    print(f"  Failures         : {stats.num_failures:,}  ({failure_rate:.1f}%)")
    print(f"  Avg response     : {stats.avg_response_time:.1f} ms")
    print(f"  95th percentile  : {stats.get_response_time_percentile(0.95):.1f} ms")
    print(f"  RPS              : {stats.current_rps:.1f}")
    print("-" * 60)
    if failure_rate > 5:
        print("  WARNING: failure rate exceeds 5% - check capacity or distributed locks")
    else:
        print("  OK: failure rate is within the acceptable range (<5%)")
    print("=" * 60 + "\n")