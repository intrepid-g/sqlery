# Sqlery Demo Project

This is a minimal Django project demonstrating sqlery.

## Setup

```bash
# Install dependencies
pip install django croniter

# Install sqlery in development mode
cd ../..
pip install -e .

# Go back to demo
cd examples/demo_project

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Run server
python manage.py runserver
```

## Usage

1. Visit http://localhost:8000/admin
2. Go to "Scheduled Tasks"
3. Add a new task:
   - Name: "Hello World"
   - Task Path: `demo_tasks.hello_world`
   - Cron Expression: `* * * * *` (every minute)
   - Enabled: ✓

4. Wait a minute and check "Task Executions" to see it ran!

## Test Tasks

The demo includes several test tasks in `demo_tasks.py`:

- `hello_world()` - Simple success task
- `failing_task()` - Demonstrates error handling
- `slow_task()` - Long-running task (5 seconds)
