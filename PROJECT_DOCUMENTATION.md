# High-Performance E-Commerce Backend Engine

Course: Parallel Programming  
Scope: First three non-functional requirements  
Backend stack: Django, Django REST Framework, Celery, Redis, django-celery-results,
django-redis, psycopg2-binary, gunicorn

## 1. Project Overview

This project is a realistic backend for a small e-commerce system. It supports
users, products, orders, order items, inventory, checkout, and background work.

The project is intentionally not a full commercial platform. Its purpose is to
demonstrate backend engineering concepts that are important in parallel and
concurrent systems:

- Several users can access the same product stock at the same time.
- The server should not accept unlimited work.
- Slow tasks should move outside the request-response cycle.

The implementation focuses on correctness and clarity. Most of the important
logic is in `shop/services.py`, so it is easy to explain during a discussion.

## 2. Architecture Overview

The project uses a simple monolithic Django structure:

```text
Client
  |
  v
Django REST Framework API
  |
  v
Checkout service
  |
  +--> PostgreSQL/SQLite database
  |
  +--> Redis cache
  |
  +--> Redis Celery queue
          |
          v
       Celery worker
          |
          v
       django-celery-results
```

There are no microservices and no complicated domain layers. The design is:

- `views.py`: HTTP/API input and output.
- `serializers.py`: request validation and response formatting.
- `services.py`: important business logic and concurrency control.
- `tasks.py`: background jobs.
- `models.py`: database structure.

This keeps the project understandable while still showing professional backend
ideas.

## 3. Database Schema

### User

The project uses Django's built-in `auth_user` table. This avoids building a
custom authentication system.

### Product

Fields:

- `name`
- `description`
- `price`
- `stock_quantity`
- `version`
- timestamps

`stock_quantity` is the main shared resource. It must be protected during
checkout because many users may try to update it at the same time.

`version` is included for demonstration. It helps show how many times a product
was updated.

### Order

Fields:

- `user`
- `customer_email`
- `status`
- `total_amount`
- timestamps

### OrderItem

Fields:

- `order`
- `product`
- `quantity`
- `unit_price`

The order item stores the price at purchase time, so old orders do not change if
the product price changes later.

### Celery Task Results

`django-celery-results` creates tables that store task status, result, start
time, finish time, and errors.

## 4. Folder Structure

```text
commerce_engine/settings.py
```

Contains installed apps, database config, Redis cache config, DRF throttling,
Celery worker settings, and request capacity settings.

```text
commerce_engine/celery.py
```

Creates the Celery app and loads Django settings. This is what allows workers to
find tasks from Django apps.

```text
shop/models.py
```

Defines products, orders, and order items. These are the core data entities.

```text
shop/serializers.py
```

Validates API input such as checkout items and login data. This keeps views from
manually parsing JSON.

```text
shop/views.py
```

Defines DRF API endpoints. Views stay thin and call service functions for real
logic.

```text
shop/services.py
```

Contains checkout and stock adjustment logic. This file is the center of the
concurrency demonstration.

```text
shop/tasks.py
```

Contains Celery tasks for email simulation, invoice generation, and analytics
logging.

```text
shop/middleware.py
```

Adds a small capacity-control guard using a bounded semaphore.

```text
shop/management/commands/simulate_concurrent_checkout.py
```

Creates many buyer threads to demonstrate race-condition prevention.

```text
shop/tests.py
```

Tests checkout correctness, rollback behavior, and Celery task dispatch.

## 5. Concurrency Strategy

The most dangerous operation is checkout because it changes stock. A race
condition can happen if two requests read the same stock value before either one
saves the new value.

Example:

```text
Stock = 1
User A reads stock = 1
User B reads stock = 1
User A buys 1
User B buys 1
```

Without locking, both users may succeed even though only one item existed.

The project avoids this using:

- `transaction.atomic()`
- `select_for_update()`
- rollback on exceptions
- validation before saving the order

The checkout service locks product rows before checking stock:

```python
Product.objects.select_for_update().filter(id__in=product_ids).order_by("id")
```

Ordering by ID is a small but useful habit. It makes the lock order predictable
when checkout has more than one product, which reduces deadlock risk.

## 6. Race Condition Prevention

The checkout flow in `shop/services.py` is:

1. Merge duplicate product lines.
2. Start `transaction.atomic()`.
3. Lock requested product rows with `select_for_update()`.
4. Check if each product has enough stock.
5. Decrease stock.
6. Create the order and order items.
7. Register Celery tasks with `transaction.on_commit()`.

If stock is not enough, the service raises `OutOfStockError`. Because the code is
inside `transaction.atomic()`, Django rolls back the whole operation.

This means a failed checkout does not leave partial data:

- no order
- no order items
- no Celery tasks
- no stock decrement

`select_for_update()` was chosen because the project wants to demonstrate
pessimistic locking. In this approach, the database protects the row while the
transaction is running. Other transactions must wait before modifying the same
row.

Important note: PostgreSQL should be used for the final concurrency demo because
it supports row-level locking properly. SQLite is acceptable for simple local
development but not for serious concurrency results.

## 7. Queue System Design

Checkout should be fast. It should not wait for slow tasks such as:

- sending a confirmation email
- generating an invoice
- logging analytics

The project uses Celery with Redis as the broker:

```text
Django request -> Celery task message -> Redis queue -> Celery worker
```

The service uses `transaction.on_commit()` before sending tasks. This matters
because a worker should not receive an order ID before the order is safely saved.

Tasks:

- `send_order_confirmation`
- `generate_invoice`
- `log_order_analytics`

Task results are saved by `django-celery-results`, so the project can inspect
whether a task is `PENDING`, `STARTED`, `SUCCESS`, `RETRY`, or `FAILURE`.

## 8. Resource Management Strategy

The project uses several simple resource controls.

### DRF Throttling

Configured in `commerce_engine/settings.py`:

```python
"anon": "30/min"
"user": "120/min"
"login": "10/min"
"checkout": "20/min"
```

This prevents one user from sending too many requests in a short period.

### Capacity Middleware

`shop/middleware.py` uses a bounded semaphore:

```python
MAX_CONCURRENT_REQUESTS = 20
CAPACITY_WAIT_SECONDS = 2
```

If all request slots are busy, the server returns `503 Service Unavailable`
instead of accepting unlimited work.

### Celery Worker Limits

Important settings:

```python
CELERY_WORKER_CONCURRENCY = 4
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 30
CELERY_TASK_SOFT_TIME_LIMIT = 20
CELERY_TASK_ACKS_LATE = True
```

Why these exist:

- `WORKER_CONCURRENCY` limits how many tasks run at once.
- `PREFETCH_MULTIPLIER = 1` stops one worker from grabbing too many jobs early.
- time limits prevent stuck tasks from running forever.
- late acknowledgements help retry work if a worker dies mid-task.

### Retry Limits

Each Celery task has retry settings:

```python
retry_kwargs={"max_retries": 3}
retry_backoff=True
```

This means temporary errors are retried, but not forever.

## 9. Redis Usage

Redis is used in two practical ways.

### Celery Broker

Celery stores queued jobs in Redis. Workers pull jobs from Redis and execute
them.

### Django Cache

Product list and product detail responses are cached for 30 seconds. This
reduces repeated database reads for catalog browsing.

Cache keys:

```text
products:list
products:detail:<product_id>
```

After checkout or admin stock update, the service clears affected product cache
entries after the transaction commits.

## 10. Postman API Examples

### Register

```http
POST /api/auth/register/
Content-Type: application/json
```

```json
{
  "username": "sara",
  "email": "sara@example.com",
  "password": "student123"
}
```

Response:

```json
{
  "user": {
    "id": 3,
    "username": "sara",
    "email": "sara@example.com"
  },
  "token": "generated-token"
}
```

### Login

```http
POST /api/auth/login/
Content-Type: application/json
```

```json
{
  "username": "student",
  "password": "student123"
}
```

Response:

```json
{
  "token": "generated-token"
}
```

### Product List

```http
GET /api/products/
```

Response:

```json
{
  "products": [
    {
      "id": 1,
      "name": "Laptop Stand",
      "description": "Aluminum stand for a desk setup",
      "price": "29.99",
      "stock_quantity": 50,
      "version": 0
    }
  ]
}
```

### Checkout

```http
POST /api/checkout/
Authorization: Token <token>
Content-Type: application/json
```

```json
{
  "items": [
    {
      "product_id": 1,
      "quantity": 2
    }
  ]
}
```

Response:

```json
{
  "order": {
    "id": 1,
    "customer_email": "student@example.com",
    "status": "paid",
    "total_amount": "59.98",
    "items": [
      {
        "product": 1,
        "product_name": "Laptop Stand",
        "quantity": 2,
        "unit_price": "29.99",
        "line_total": "59.98"
      }
    ]
  },
  "queued_background_tasks": [
    "send_order_confirmation",
    "generate_invoice",
    "log_order_analytics"
  ]
}
```

### Out Of Stock

Response:

```json
{
  "detail": "not enough stock for Laptop Stand: requested 100"
}
```

HTTP status:

```text
409 Conflict
```

## 11. Concurrent Request Simulation

Run:

```powershell
python manage.py simulate_concurrent_checkout --buyers 20 --stock 5
```

Expected result:

```text
Successful orders: 5
Rejected orders: 15
Final stock: 0
```

This is a simple way to explain that many threads attempted checkout, but the
database transaction allowed only valid purchases.

For API-level testing, a simple PowerShell loop can send repeated checkout
requests after login. For stronger testing, use a tool such as Postman Runner,
JMeter, or Locust. These tools are not required project dependencies.

## 12. Stress Testing Plan

A practical stress test should measure:

- successful orders
- rejected orders
- response time
- final stock value
- failed Celery jobs
- CPU and memory usage

Suggested scenarios:

1. 20 users browse products.
2. 50 users repeatedly checkout different products.
3. 100 users try to buy a low-stock product.
4. Redis is stopped to see how the API behaves.
5. Celery worker concurrency is changed from 1 to 4.

The most important correctness check is:

```text
successful_order_quantity + final_stock = initial_stock
```

If that equation is true, the inventory logic is behaving correctly.

## 13. Benchmark Examples

Example table for the report:

```text
Scenario                    Users    Avg response    Failed    Final stock
Product listing             50       80 ms           0         unchanged
Checkout normal stock        50       180 ms          0         correct
Checkout stock=5 buyers=20   20       220 ms          15        0
```

Another useful comparison:

```text
Product list without cache:  average 120 ms
Product list with Redis:     average 35 ms
```

The exact numbers depend on the machine, database, and Redis setup.

## 14. Expected Bottlenecks

Expected bottlenecks are:

- Database row locks during checkout for very popular products.
- Redis availability, because Celery and cache both depend on it.
- Celery worker concurrency if invoice tasks become slow.
- Too many Gunicorn workers can overload the database with connections.
- SQLite limitations if used for high-concurrency testing.

These bottlenecks are normal and useful to discuss. The project is designed to
make them visible.

## 15. Future Improvements

Good future improvements:

- Use PostgreSQL as the default database for final testing.
- Add a real email backend instead of simulated email logging.
- Add a simple invoice file model if invoice storage is required.
- Add product creation APIs for admin users.
- Add pagination for product and order lists.
- Add a formal Locust or JMeter test file.
- Add dashboard screenshots from Flower.

Not recommended for this project:

- microservices
- complex authentication
- event sourcing
- message bus abstractions
- advanced design patterns

Those would make the project harder to explain without improving the course
objective.

## 16. Final Summary

This backend demonstrates the first three requirements in a realistic but
student-friendly way:

- Stock is protected using transactions and pessimistic row locking.
- Server overload is reduced using throttling, middleware limits, timeouts, and
  worker settings.
- Slow tasks run asynchronously through Celery and Redis, with results stored in
  the database.

The code is small enough to explain during a university discussion, but it still
uses real backend tools and real concurrency techniques.
