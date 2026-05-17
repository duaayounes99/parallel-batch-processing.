# High-Performance E-Commerce Backend Engine

Backend project for a Parallel Programming course. The goal is not to build a
large online store. The goal is to show that a backend can keep shared data
correct, avoid overload, and move slow work into background queues.

The implementation focuses deeply on the first three requirements:

1. Concurrent access and data integrity.
2. Resource management and capacity control.
3. Asynchronous processing with queues.

## Architecture Overview

The project uses one Django project and one Django app:

```text
commerce_engine/     Django settings, URLs, WSGI, Celery app
shop/                Products, orders, checkout service, DRF APIs, Celery tasks
```

The important request flow is:

```text
Client -> DRF checkout API -> transaction.atomic()
       -> Product rows locked with select_for_update()
       -> stock decremented and order saved
       -> transaction commits
       -> Celery tasks are queued in Redis
```

Redis has two jobs:

- Celery broker: stores queued background jobs.
- Django cache: stores product API responses and supports throttling.

`django-celery-results` stores task status and results in the database so failed
or completed tasks can be inspected later.

## Database Schema

Main tables:

- `auth_user`: simple Django users for register/login.
- `shop_product`: product catalog and `stock_quantity`.
- `shop_order`: order header linked to a user.
- `shop_orderitem`: purchased products and quantities.
- `django_celery_results_taskresult`: Celery task status/result history.

The sensitive shared resource is:

```text
shop_product.stock_quantity
```

Checkout protects this field with `transaction.atomic()` and
`select_for_update()`.

## Folder Structure

```text
New project/
|-- manage.py
|-- requirements.txt
|-- Dockerfile
|-- docker-compose.yml
|-- .dockerignore
|-- README.md
|-- PROJECT_DOCUMENTATION.md
|-- STUDENT_EXPLANATION_GUIDE.md
|-- DOCKER_GUIDE.md
|-- commerce_engine/
|   |-- __init__.py
|   |-- celery.py
|   |-- settings.py
|   |-- urls.py
|   |-- asgi.py
|   `-- wsgi.py
`-- shop/
    |-- admin.py
    |-- apps.py
    |-- middleware.py
    |-- models.py
    |-- serializers.py
    |-- services.py
    |-- tasks.py
    |-- tests.py
    |-- urls.py
    |-- views.py
    |-- migrations/
    `-- management/
        `-- commands/
            |-- seed_demo.py
            `-- simulate_concurrent_checkout.py
```

## Implementation Roadmap

1. Define products, orders, and order items.
2. Add token-based register/login using Django users.
3. Implement checkout in a service function, not inside the view.
4. Protect stock updates using database transactions and row locks.
5. Add DRF throttling and a small request-capacity middleware.
6. Queue email, invoice, and analytics jobs using Celery + Redis.
7. Store task results with `django-celery-results`.
8. Add tests and a command that simulates concurrent buyers.
9. Document Postman examples, stress testing, bottlenecks, and future work.

## Setup Options

There are two ways to run the project:

- **Recommended:** Docker Compose, because it starts Redis and PostgreSQL for you.
- **Local manual:** Python directly on your machine, useful only if Redis and database services are already installed.

## Recommended Docker Setup

Docker is the easiest way to run the full project correctly. It starts all
services needed by the backend:

```text
web      Django + Gunicorn API server
worker   Celery worker for background jobs
redis    Redis queue/cache service
db       PostgreSQL database
flower   optional Celery monitoring dashboard
```

Build and start the main services:

```powershell
docker compose up --build
```

The `web` container automatically runs:

```text
python manage.py migrate
python manage.py seed_demo
gunicorn commerce_engine.wsgi:application --bind 0.0.0.0:8000
```

Open the API health check:

```text
http://127.0.0.1:8000/api/health/
```

Expected:

```json
{"status":"ok"}
```

Check Redis:

```powershell
docker compose exec redis redis-cli ping
```

Expected:

```text
PONG
```

Check PostgreSQL:

```powershell
docker compose exec db pg_isready -U commerce_user -d commerce_engine
```

Run the concurrency demo inside Docker:

```powershell
docker compose exec web python manage.py simulate_concurrent_checkout --buyers 20 --stock 5
```

For the full Docker explanation, read:

```text
DOCKER_GUIDE.md
```

This Docker setup was verified on this machine. Redis, PostgreSQL, Django,
Celery, Flower, tests, checkout, and the concurrency demo all ran successfully.

Docker service names matter. Inside containers, Django connects to Redis with
`redis://redis:6379/0` and PostgreSQL with `POSTGRES_HOST=db`. That is why Redis
works without being installed directly on Windows.

Stop the containers:

```powershell
docker compose down
```

Reset Docker database/cache data:

```powershell
docker compose down -v
```

## Local Manual Setup

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Apply migrations:

```powershell
python manage.py migrate
python manage.py seed_demo
```

Run Redis before testing Celery and Redis caching. The default local URL is:

```text
redis://127.0.0.1:6379/0
```

Start Django:

```powershell
python manage.py runserver
```

Start a Celery worker:

```powershell
celery -A commerce_engine worker -l info --pool=solo -Q emails,invoices,analytics
```

Optional Flower monitoring:

```powershell
celery -A commerce_engine flower
```

## Demo Users

Created by `python manage.py seed_demo`:

```text
student / student123
admin / admin12345
```

## Main API Endpoints

Base URL:

```text
http://127.0.0.1:8000/api/
```

Endpoints:

- `GET /health/`
- `POST /auth/register/`
- `POST /auth/login/`
- `GET /products/`
- `GET /products/<id>/`
- `PATCH /products/<id>/stock/` admin only
- `POST /checkout/` authenticated
- `GET /orders/` authenticated
- `GET /tasks/results/` admin only

## Concurrency Demo

This command starts many threads that all try to buy the same product:

```powershell
python manage.py simulate_concurrent_checkout --buyers 20 --stock 5
```

Expected idea:

```text
Successful orders: 5
Rejected orders: 15
Final stock: 0
```

For the strongest locking demonstration, use PostgreSQL. SQLite is useful for
local development, but PostgreSQL gives real row-level locking for
`select_for_update()`.

## Tests

```powershell
python manage.py check
python manage.py test
```

The current tests cover:

- Checkout decreases stock.
- Checkout queues Celery tasks after commit.
- Overselling is rejected.
- Failed checkout does not create an order or queue tasks.

## Production-Style Server Command

For a simple production-like run:

```powershell
gunicorn commerce_engine.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 30
```

Gunicorn is included because the project discusses backend capacity, worker
limits, and timeout behavior.
