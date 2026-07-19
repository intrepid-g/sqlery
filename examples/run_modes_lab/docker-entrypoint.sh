#!/bin/sh
set -e

# Wait for postgres to be ready (unless LAB_SKIP_WAIT is set or we're running migrate)
if [ "$1" != "migrate" ] && [ -z "$LAB_SKIP_WAIT" ]; then
    python -c "
import socket
import time
import os
import sys

host = os.environ.get('POSTGRES_HOST', 'postgres')
port = int(os.environ.get('POSTGRES_PORT', '5432'))
max_attempts = 60
attempt = 0

while attempt < max_attempts:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex((host, port))
        sock.close()
        if result == 0:
            print(f'Database {host}:{port} is ready', file=sys.stderr)
            sys.exit(0)
    except Exception as e:
        pass

    attempt += 1
    time.sleep(0.5)

print(f'Database {host}:{port} did not become ready after {max_attempts * 0.5}s', file=sys.stderr)
sys.exit(1)
"
fi

# Execute the command supplied by compose
exec "$@"
