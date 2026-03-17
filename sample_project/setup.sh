#!/bin/bash
# Quick setup script for django-sql-jobs sample project

set -e

echo "🚀 Setting up Django SQL Jobs sample project..."
echo

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
else
    echo "✓ Virtual environment already exists"
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "📥 Installing dependencies..."
pip install -q -r requirements.txt

# Run migrations
echo "🔄 Running migrations..."
python manage.py migrate

# Check if superuser exists
echo
echo "✅ Setup complete!"
echo
echo "Next steps:"
echo "1. Create a superuser: python manage.py createsuperuser"
echo "2. Start the server: python manage.py runserver"
echo "3. Visit: http://127.0.0.1:9100/admin/django_sql_jobs/dashboard/"
echo
echo "Note: Make sure to activate the virtual environment first:"
echo "  source venv/bin/activate"
