#!/bin/bash
set -e

echo "==> Waiting for services to be ready..."
sleep 2

echo "==> Running database migrations..."
python manage.py migrate --noinput

echo "==> Collecting static files..."
python manage.py collectstatic --noinput --clear

echo "==> Creating superuser if it doesn't exist..."
python manage.py shell <<EOF
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@example.com', 'admin')
    print('Superuser created: username=admin, password=admin')
else:
    print('Superuser already exists')
EOF

echo "==> Creating sample scheduled tasks..."
python manage.py shell <<EOF
from sqlery.models import ScheduledTask
from django.db import IntegrityError

tasks = [
    {
        'name': 'Every Minute Task',
        'task_path': 'tasks_app.tasks.scheduled_daily_task',
        'cron_expression': '* * * * *',
        'queue_name': 'default',
        'priority': 5,
        'enabled': True,
    },
    {
        'name': 'Every 5 Minutes',
        'task_path': 'tasks_app.tasks.simple_task',
        'cron_expression': '*/5 * * * *',
        'queue_name': 'default',
        'priority': 3,
        'enabled': True,
    },
]

for task_data in tasks:
    try:
        task, created = ScheduledTask.objects.get_or_create(
            name=task_data['name'],
            defaults=task_data
        )
        if created:
            print(f'Created scheduled task: {task.name}')
        else:
            print(f'Scheduled task already exists: {task.name}')
    except Exception as e:
        print(f'Error creating task {task_data["name"]}: {e}')
EOF

echo "==> Setup complete!"
echo ""
echo "=========================================="
echo "Django Admin Credentials:"
echo "  Username: admin"
echo "  Password: admin"
echo "=========================================="
echo "  Dashboard: http://localhost:8855/admin/dashboard/"
echo "  Admin: http://localhost:8855/admin/"
echo "=========================================="
echo ""

# Execute the main command
exec "$@"
