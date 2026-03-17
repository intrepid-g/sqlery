# Multi-Worker Architecture Plan

## Design Overview

```
Daemon Process
    ↓
Worker Pool Manager (monitors & spawns)
    ↓
Worker1 | Worker2 | Worker3 | ... | WorkerN
   ↓        ↓        ↓              ↓
Claims   Claims   Claims        Claims
jobs     jobs     jobs          jobs
from     from     from          from
queue    queue    queue         queue
```

## Components

### 1. Worker Model (Database)

```python
class Worker(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    node_id = models.CharField(max_length=255)  # hostname/container
    pid = models.IntegerField()  # process ID
    status = models.CharField(
        max_length=20,
        choices=[
            ('idle', 'Idle'),
            ('busy', 'Busy'),
            ('dead', 'Dead'),
        ],
        default='idle'
    )
    current_job = models.ForeignKey(
        'QueuedJob',
        null=True,
        blank=True,
        on_delete=models.SET_NULL
    )
    queues = models.JSONField(default=list)  # List of queue names
    last_heartbeat = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(auto_now_add=True)
    jobs_processed = models.IntegerField(default=0)
```

### 2. Settings Configuration

```python
DJANGO_SQL_JOBS = {
    # Daemon mode
    'TRIGGER_MODE': 'daemon',
    'ENABLE_DAEMON': True,
    'DAEMON_CHECK_INTERVAL': 10,

    # Worker pool settings
    'MAX_WORKERS_PER_NODE': 4,
    'WORKER_HEARTBEAT_INTERVAL': 5,  # seconds
    'WORKER_ALIVE_TIMEOUT': 30,  # seconds (no heartbeat = dead)
    'WORKER_QUEUES': ['high', 'default', 'low'],  # Priority order

    # Queue configuration
    'QUEUE_PRIORITIES': {
        'high': 100,
        'default': 50,
        'low': 10,
    },
}
```

### 3. Architecture Details

#### Daemon (main process)
- Spawns worker subprocesses
- Monitors worker pool size
- Cleans up dead workers (no heartbeat > timeout)
- Maintains max workers per node
- Handles graceful shutdown of all workers

#### Worker (subprocess)
- Registers on startup → writes to Worker table
- Writes heartbeat every 5s → updates Worker.last_heartbeat
- Claims & processes jobs atomically
- Respects queue priority order
- Updates status (idle/busy) in DB
- Exits gracefully on shutdown signal
- Unregisters on exit

#### Worker Job Claiming Logic (Atomic)

```sql
-- Atomic job claim with queue priority
UPDATE queued_jobs
SET status='running', worker_id=?
WHERE id = (
    SELECT id FROM queued_jobs
    WHERE status='queued'
    AND queue_name IN ('high', 'default', 'low')
    ORDER BY
        CASE queue_name
            WHEN 'high' THEN 1
            WHEN 'default' THEN 2
            WHEN 'low' THEN 3
        END,
        priority DESC,
        created_at ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
RETURNING *
```

## Workflow

### 1. Daemon Startup

```python
def daemon_startup():
    # 1. Get node_id (hostname or env var)
    node_id = get_node_id()

    # 2. Check current workers for this node in DB
    active_workers = Worker.objects.filter(
        node_id=node_id,
        status__in=['idle', 'busy']
    )

    # 3. Clean up stale/dead workers (no heartbeat)
    cleanup_dead_workers(node_id)

    # 4. Spawn workers up to MAX_WORKERS_PER_NODE
    current_count = active_workers.count()
    max_workers = get_setting('MAX_WORKERS_PER_NODE', 4)

    for i in range(max_workers - current_count):
        spawn_worker(node_id)
```

### 2. Worker Lifecycle

```
Spawn Worker Process
    ↓
Register in DB (Worker.objects.create)
    ↓
Enter Main Loop:
    1. Write heartbeat (Worker.objects.update(last_heartbeat=now))
    2. Claim job atomically (priority-based SELECT FOR UPDATE)
    3. If job found:
        - Update Worker.status = 'busy'
        - Update Worker.current_job = job
        - Execute job
        - Update Worker.status = 'idle'
        - Update Worker.jobs_processed += 1
    4. If no job: sleep 1s
    5. Check shutdown signal
    6. Repeat
    ↓
Unregister (Worker.objects.delete)
    ↓
Exit
```

### 3. Worker Pool Management (Daemon)

```python
def monitor_worker_pool():
    """Called every 10s by daemon"""
    node_id = get_node_id()
    timeout = get_setting('WORKER_ALIVE_TIMEOUT', 30)
    max_workers = get_setting('MAX_WORKERS_PER_NODE', 4)

    # 1. Find dead workers (no heartbeat > timeout)
    dead_threshold = timezone.now() - timedelta(seconds=timeout)
    dead_workers = Worker.objects.filter(
        node_id=node_id,
        last_heartbeat__lt=dead_threshold
    )

    # 2. Mark as dead
    dead_workers.update(status='dead')

    # 3. Count active workers
    active_count = Worker.objects.filter(
        node_id=node_id,
        status__in=['idle', 'busy']
    ).count()

    # 4. Spawn replacements if needed
    needed = max_workers - active_count
    for i in range(needed):
        spawn_worker(node_id)
```

### 4. Node Isolation

- Each node (server/container) tracks its own workers
- `node_id` = `socket.gethostname()` or `os.environ.get('NODE_ID')`
- Workers only counted toward that node's MAX_WORKERS_PER_NODE cap
- Allows horizontal scaling: Run daemon on multiple nodes

## New Files Needed

### Core Implementation

1. **`models.py`** - Add `Worker` model
   - UUID primary key
   - Node tracking
   - Status & heartbeat fields
   - Foreign key to current job

2. **`worker_process.py`** - Individual worker subprocess
   - Main worker loop
   - Job claiming logic
   - Heartbeat writing
   - Signal handling

3. **`worker_pool.py`** - Pool manager for daemon
   - Worker spawning
   - Pool monitoring
   - Dead worker cleanup
   - Graceful shutdown

4. **`worker_claiming.py`** - Atomic job claiming
   - Priority-based queue selection
   - SELECT FOR UPDATE SKIP LOCKED
   - Worker assignment

### Database Migrations

```bash
python manage.py makemigrations sqlery
python manage.py migrate
```

### Management Commands

Update `daemon.py` command:
```bash
python manage.py daemon status    # Show daemon + workers
python manage.py daemon stop      # Stop daemon + all workers
python manage.py daemon restart   # Restart with new worker pool
```

Add new command:
```bash
python manage.py workers list     # List all workers
python manage.py workers kill <id> # Kill specific worker
```

## Configuration Examples

### Minimal (2 workers, default settings)

```python
DJANGO_SQL_JOBS = {
    'TRIGGER_MODE': 'daemon',
    'ENABLE_DAEMON': True,
    'MAX_WORKERS_PER_NODE': 2,
}
```

### Production (4 workers, 3 priority queues)

```python
DJANGO_SQL_JOBS = {
    'TRIGGER_MODE': 'daemon',
    'ENABLE_DAEMON': True,

    # Worker pool
    'MAX_WORKERS_PER_NODE': 4,
    'WORKER_HEARTBEAT_INTERVAL': 5,
    'WORKER_ALIVE_TIMEOUT': 30,

    # Queue priorities (high → default → low)
    'WORKER_QUEUES': ['high', 'default', 'low'],
    'QUEUE_PRIORITIES': {
        'high': 100,
        'default': 50,
        'low': 10,
    },
}
```

### Multi-node (Docker Swarm / K8s)

```python
# Same config on all nodes
DJANGO_SQL_JOBS = {
    'TRIGGER_MODE': 'daemon',
    'ENABLE_DAEMON': True,
    'MAX_WORKERS_PER_NODE': 4,  # 4 per container
}

# Set NODE_ID environment variable per container
# export NODE_ID="web-1"  # Or use hostname
```

## Dashboard Updates

### Worker Status Panel

```
Active Workers: 4 / 4
- Worker abc123 [BUSY] → Job #45 (high queue) - 5s
- Worker def456 [IDLE] → Last: Job #44 - 30s ago
- Worker ghi789 [BUSY] → Job #46 (default queue) - 2s
- Worker jkl012 [IDLE] → Last: Job #43 - 15s ago

Dead Workers (cleaned up): 0
Total Jobs Processed (all workers): 1,234
```

### API Endpoint Addition

```python
# Add to views.py
def dashboard_stats(request):
    # ... existing stats ...

    # Worker stats
    node_id = get_node_id()
    workers = Worker.objects.filter(
        node_id=node_id,
        status__in=['idle', 'busy']
    ).values(
        'id', 'status', 'current_job_id',
        'jobs_processed', 'started_at', 'last_heartbeat'
    )

    return JsonResponse({
        # ... existing data ...
        'workers': list(workers),
        'worker_stats': {
            'active': workers.count(),
            'max': get_setting('MAX_WORKERS_PER_NODE'),
            'busy': workers.filter(status='busy').count(),
            'idle': workers.filter(status='idle').count(),
        }
    })
```

## Benefits

### vs Single Worker Mode

| Feature | Single Worker | Multi-Worker Pool |
|---------|--------------|-------------------|
| Concurrency | 1 job at a time | N jobs in parallel |
| Throughput | Low | High |
| Queue blocking | High queue blocks others | Queues processed in parallel |
| Resource usage | Low | Medium-High |
| Complexity | Simple | Moderate |

### vs RQ

| Feature | RQ | sqlery Multi-Worker |
|---------|-----|----------------------------|
| External dependency | Redis | None (just Django DB) |
| Worker management | Manual (rq worker) | Automatic (daemon spawns) |
| Monitoring | RQ dashboard | Django admin + dashboard |
| Database | Redis | PostgreSQL/SQLite |
| Setup complexity | Medium | Low |

## Implementation Phases

### Phase 1: Core Infrastructure
1. ✅ Add Worker model
2. ✅ Create migration
3. ✅ Implement worker_process.py
4. ✅ Add worker registration/unregistration

### Phase 2: Pool Management
5. ✅ Implement worker_pool.py
6. ✅ Add spawning logic to daemon
7. ✅ Implement heartbeat monitoring
8. ✅ Add dead worker cleanup

### Phase 3: Job Claiming
9. ✅ Implement atomic job claiming
10. ✅ Add queue priority logic
11. ✅ Update QueuedJob model (add worker FK)
12. ✅ Create migration

### Phase 4: Monitoring & Control
13. ✅ Update dashboard with worker stats
14. ✅ Add worker management commands
15. ✅ Implement graceful shutdown
16. ✅ Add worker kill/restart

### Phase 5: Testing & Docs
17. ✅ Test multi-worker scenarios
18. ✅ Load testing with concurrent jobs
19. ✅ Document configuration
20. ✅ Update README

## Testing Scenarios

1. **Single Node, Multiple Workers**
   - Start daemon with 4 workers
   - Enqueue 10 jobs
   - Verify parallel processing

2. **Queue Priorities**
   - Enqueue jobs: 5 low, 5 high
   - Verify high priority processed first

3. **Worker Failure Recovery**
   - Kill worker process (SIGKILL)
   - Verify daemon spawns replacement
   - Verify job is retried

4. **Graceful Shutdown**
   - Workers processing jobs
   - Send SIGTERM to daemon
   - Verify workers finish current jobs
   - Verify clean exit

5. **Multi-Node Scaling**
   - Run daemon on 2+ nodes
   - Verify each respects its MAX_WORKERS_PER_NODE
   - Verify no job double-processing

## Migration Path

### From Single Worker Mode

```python
# Old config
DJANGO_SQL_JOBS = {
    'TRIGGER_MODE': 'daemon',
    'DAEMON_CHECK_INTERVAL': 10,
}

# New config (opt-in to multi-worker)
DJANGO_SQL_JOBS = {
    'TRIGGER_MODE': 'daemon',
    'MAX_WORKERS_PER_NODE': 4,  # Enable multi-worker
}
```

Default behavior: If `MAX_WORKERS_PER_NODE` not set, defaults to 1 (single worker, backward compatible)

## Security Considerations

1. **Worker Authentication**
   - Workers write to database directly (authenticated via Django)
   - No external API calls between workers

2. **Job Claiming**
   - Atomic with SELECT FOR UPDATE SKIP LOCKED
   - Prevents race conditions
   - No two workers can claim same job

3. **Worker Isolation**
   - Each worker runs in separate process
   - Memory leaks isolated to single worker
   - Crashed worker doesn't affect others

## Performance Considerations

1. **Database Load**
   - Heartbeat writes: N workers × (1 write / 5s) = N/5 writes/sec
   - For 4 workers: 0.8 writes/sec (negligible)

2. **Job Claiming**
   - Uses database-level locking (fast)
   - PostgreSQL: FOR UPDATE SKIP LOCKED (no blocking)
   - SQLite: May have some contention (use PostgreSQL in production)

3. **Scaling**
   - Horizontal: Add more nodes
   - Vertical: Increase MAX_WORKERS_PER_NODE
   - Recommended: 1-4 workers per CPU core

## Future Enhancements

1. **Worker Specialization**
   - Some workers only handle specific queues
   - `WORKER_1_QUEUES = ['high']`

2. **Dynamic Scaling**
   - Auto-adjust workers based on queue depth
   - Scale up when queue > threshold
   - Scale down when idle

3. **Worker Health Checks**
   - More sophisticated than just heartbeat
   - Check memory usage, CPU, etc.

4. **Job Affinity**
   - Pin jobs to specific workers
   - Useful for stateful tasks

5. **Rate Limiting**
   - Per-queue rate limits
   - Per-worker rate limits
