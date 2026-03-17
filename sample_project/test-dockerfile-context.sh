#!/bin/bash
echo "Files that will be copied to Docker image:"
echo "============================================"
ls -la | grep -v "^d" | grep -v ".venv" | grep -v "venv" | grep -v "db.sqlite3" | grep -v "__pycache__"
echo ""
echo "Checking critical files:"
echo "------------------------"
for file in requirements.txt Dockerfile docker-entrypoint.sh manage.py; do
    if [ -f "$file" ]; then
        echo "✓ $file exists"
    else
        echo "✗ $file MISSING"
    fi
done
