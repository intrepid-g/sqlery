#!/bin/bash
echo "Verifying Docker build context from: $(pwd)"
echo "============================================"
echo ""

# Check if we're in the right directory
if [[ ! -d "sample_project" || ! -d "src" ]]; then
    echo "❌ ERROR: Must run from django-sql-jobs directory"
    echo "Current: $(pwd)"
    exit 1
fi

echo "✓ In correct directory: django-sql-jobs/"
echo ""

# Check files that Dockerfile will COPY
echo "Checking files Dockerfile will copy:"
echo "------------------------------------"

files=(
    "sample_project/requirements.txt"
    "sample_project/docker-entrypoint.sh"
    "sample_project/manage.py"
    "sample_project/mysite/settings.py"
    "src/django_sql_jobs/__init__.py"
)

all_good=true
for file in "${files[@]}"; do
    if [ -f "$file" ]; then
        echo "✓ $file"
    else
        echo "❌ MISSING: $file"
        all_good=false
    fi
done

echo ""
if $all_good; then
    echo "✅ All required files present!"
    echo ""
    echo "Docker build would succeed with:"
    echo "  cd sample_project"
    echo "  docker compose build"
    exit 0
else
    echo "❌ Some files missing - build will fail"
    exit 1
fi
