# HTTP Trigger Mode - Known Issues and Failure Modes

This document catalogues all known issues, edge cases, and failure modes for the HTTP trigger mode in sqlery.

## Executive Summary

HTTP trigger mode makes the Django application send HTTP requests to itself to trigger background job processing. While clever for avoiding separate processes, this architecture has numerous failure modes, especially in production multi-instance deployments.

**Recommendation:** HTTP trigger mode should only be used for single-instance ASGI deployments. Production systems should use dedicated scheduler processes or serverless invocation.

---

## Configuration Issues

### 1. httpx Not Installed - Import Crash
**Location:** `http_trigger_middleware.py:1`

**Problem:**
```python
import httpx  # ← Crashes if httpx not installed
```

**Impact:**
- ImportError even when `TRIGGER_MODE != 'http'`
- Crashes on startup regardless of configuration
- Affects users who never intended to use HTTP mode

**Fix Needed:** Lazy import with try/except
```python
try:
    import httpx
except ImportError:
    if get_setting('TRIGGER_MODE') == 'http':
        raise RuntimeError("httpx required for HTTP trigger mode")
```

---

### 2. INTERNAL_SECRET Not Configured
**Location:** `views.py:46`

**Problem:**
```python
secret = get_setting("INTERNAL_SECRET")
if not secret:
    return JsonResponse({"error": "Server misconfiguration"}, status=500)
```

**Impact:**
- Worker endpoint returns 500
- Jobs never run
- Error only visible in logs, not to users
- Silent failure mode

**User Impact:** Cron jobs stop working, nobody notices until monitored

---

### 3. INTERNAL_BASE_URL Misconfiguration
**Common mistakes:**
```python
# Development settings in production
INTERNAL_BASE_URL = 'http://127.0.0.1:8000'  # ❌

# Actual production setup:
# - https://myapp.com (SSL termination at LB)
# - Container internal port: 8080
# - Container IP: 10.0.1.5
```

**Impact:**
- POST requests go nowhere
- Connection refused or timeout
- Jobs never trigger
- No error feedback to user

---

## Container and Networking Issues

### 4. Container Networking - localhost Doesn't Work
**Problem:** `INTERNAL_BASE_URL = 'http://127.0.0.1:8000'`

**Failure scenarios:**

**Docker containers:**
```
Container A: Middleware sends POST to 127.0.0.1:8000
Container B: Listening on port 8000
❌ Request never arrives (different network namespace)
```

**Kubernetes pods:**
```
Pod replicas behind Service
127.0.0.1 = pod's own localhost
❌ Can't reach other pods or service
```

**Impact:** Jobs never execute in multi-container deployments

**Workaround:**
```python
# Docker
INTERNAL_BASE_URL = 'http://host.docker.internal:8000'

# Kubernetes
INTERNAL_BASE_URL = 'http://myapp-service:8000'
```

---

### 5. Firewall and Network Policy Blocking
**Problem:** Security policies may block internal endpoints

**Common scenarios:**
```
Load balancer: Block /_internal/* paths
Firewall: DROP packets to internal endpoints
WAF: Flag suspicious internal requests
Network policy: Deny pod-to-pod traffic
```

**Impact:**
- POST times out after 2 seconds
- Jobs silently fail to trigger
- Error logged but not visible to users

---

### 6. Load Balancer Routing Issues
**Problem:** External load balancer may not route internal requests correctly

**Scenario:**
```
Browser → LB → Container A → Middleware
Middleware → POST http://myapp.com/_internal/worker
LB → routes to Container B (random)
Container B processes job ✅ BUT wasteful

OR worse:
LB → SSL redirect → HTTP 301
POST lost, jobs never run ❌
```

---

## Timing and Synchronization Issues

### 7. Clock Skew - Signature Expiry
**Location:** `signature.py`, `SIGNATURE_MAX_AGE = 5`

**Problem:** Multi-server deployments without NTP sync

**Failure case:**
```
Server A (10:00:00): Generates signature, timestamp=10:00:00
Server B (9:59:54): Receives request
Validation: (10:00:00 - 9:59:54) = 6 seconds > 5 seconds MAX_AGE
❌ Signature rejected: 403 Forbidden
```

**Impact:**
- Jobs silently don't run
- No error visible to users
- Intermittent failures hard to debug

**Mitigation:**
- Increase SIGNATURE_MAX_AGE to 30+ seconds
- Require NTP sync in documentation
- Add clock skew monitoring

---

### 8. HTTP Request Timeout Too Strict
**Location:** `http_trigger_middleware.py`

**Problem:**
```python
httpx.Client(timeout=2.0)  # 2 second timeout
```

**Failure scenarios:**
- High server load: endpoint responds in 2.1s → timeout
- Network congestion: packet delay → timeout
- DNS resolution slow: adds to timeout
- Container startup: endpoint not ready → timeout

**Impact:**
- Jobs never trigger
- Error logged: "Request timeout"
- User unaware of failure

**Better approach:**
- Increase timeout to 10s
- Add retries with exponential backoff
- Fire-and-forget with background task

---

### 9. Throttle Cache Not Shared Across Instances
**Location:** `http_trigger_middleware.py`, `middleware.py`

**Problem:** Each instance has own LocMemCache

**Scenario:**
```
3 Django containers behind load balancer
Container A cache: "Last check: 9:59:00"
Container B cache: "Last check: 9:59:00"
Container C cache: "Last check: 9:59:00"

10:00:00 - Three requests arrive (one per container)
Each checks: "60s passed? Yes!"
All trigger: 3× POST → 3× subprocess → 3× job processing
```

**Impact:**
- Wasteful redundant execution
- Resource consumption 3x higher
- Relies on atomic job claiming to prevent duplicates

**Fix Required:** Shared cache backend (Redis/Memcached)

---

## Resource and Scalability Issues

### 10. Thundering Herd
**Problem:** Many concurrent requests after idle period

**Scenario:**
```
60 seconds pass with no HTTP traffic
Suddenly: 100 concurrent requests arrive (traffic spike)
Each request checks throttle independently
Racing condition: multiple threads think "60s passed"
Result: 20 concurrent POSTs to /_internal/worker
20 subprocesses spawn simultaneously
```

**Impact:**
- CPU spike
- Memory exhaustion
- Potential OOM killer
- Cascading failures

**Mitigation:**
- Distributed lock for throttle check
- Rate limiting on /_internal/worker endpoint

---

### 11. Subprocess Spawn Limits
**Location:** `views.py:spawn_worker_subprocess()`

**Problem:** OS/container limits on processes

**Limits:**
```bash
ulimit -u       # Max user processes (e.g., 1024)
docker --pids-limit=100  # Container PID limit
cgroup limits   # Kubernetes resource constraints
```

**Failure:**
```python
proc = await asyncio.create_subprocess_exec(...)
# After 1000th subprocess:
OSError: [Errno 11] Resource temporarily unavailable
```

**Impact:**
- New jobs can't spawn
- Jobs pile up in queue
- System becomes unresponsive

---

### 12. Zombie Process Accumulation
**Location:** `views.py:95`

**Problem:** If `start_new_session=True` doesn't work correctly

**Symptom:**
```bash
$ ps aux | grep defunct
django   12001  0.0  0.0      0     0 ?        Z    10:00   0:00 [python] <defunct>
django   12002  0.0  0.0      0     0 ?        Z    10:01   0:00 [python] <defunct>
... (thousands)
```

**Impact:**
- Consumes PIDs
- Eventually hits pid_max limit
- System can't spawn new processes
- Requires container/server restart

**Root causes:**
- Parent process not reaping children
- Signal handling issues
- OS-specific behavior differences

---

## ASGI and Server Compatibility Issues

### 13. Requires ASGI Server
**Location:** `views.py:17` - async views

**Problem:**
```python
async def internal_worker(request):  # ← Async view
    await spawn_worker_subprocess()
```

**Incompatible with WSGI:**
```bash
# ❌ Won't work:
gunicorn myproject.wsgi:application
uwsgi --wsgi-file myproject/wsgi.py

# ✅ Required:
uvicorn myproject.asgi:application
gunicorn -k uvicorn.workers.UvicornWorker myproject.asgi:application
daphne myproject.asgi:application
```

**Impact:**
- Silent failure: async views don't execute
- Or crashes depending on server
- User confused by non-functional cron jobs

**Documentation gap:** Not clearly stated as requirement

---

### 14. Subprocess Environment Inheritance Issues
**Location:** `views.py:88`

**Problem:** Subprocess may not inherit necessary environment

**Missing variables:**
```python
DJANGO_SETTINGS_MODULE  # May not be inherited
DATABASE_URL            # Environment-based config
SECRET_KEY              # From environment
AWS credentials         # For S3, SES, etc.
Virtual environment     # PATH to Python packages
```

**Symptom:**
```
ImportError: No module named 'myproject.settings'
django.core.exceptions.ImproperlyConfigured
Connection refused: Database
```

**Current code:**
```python
proc = await asyncio.create_subprocess_exec(
    sys.executable,
    manage_py,
    "run_jobs",
    "--once",
    # env=os.environ,  # ← Not explicitly passed!
)
```

**Fix needed:** Explicitly pass `env=os.environ`

---

## Security and Headers Issues

### 15. Proxy/Middleware Stripping Headers
**Problem:** Reverse proxies may strip custom headers

**Headers required:**
```
X-Signature: <hmac-signature>
X-Timestamp: <unix-timestamp>
```

**Common culprits:**
- Nginx: `proxy_pass_header` not configured
- CloudFlare: Strips unknown headers
- AWS ALB: Header size limits
- Corporate proxies: Security policies

**Impact:**
- Headers missing at endpoint
- Signature verification fails
- 403 Forbidden
- Jobs never run

---

### 16. Unauthenticated Health Endpoint
**Location:** `views.py:102` - `health_check()`

**Problem:**
```python
@csrf_exempt
async def health_check(request):  # No authentication
    # Returns database stats
```

**Security issues:**
- Anyone can hit `/_internal/health`
- Reveals queue depth
- Could be used for timing attacks
- No rate limiting

**Best practice:** Add authentication or IP allowlist

---

## Architectural Problems

### 17. Self-HTTP is Fundamentally Fragile
**Core issue:** Making HTTP requests to yourself introduces unnecessary complexity

**Dependencies required:**
- Network stack
- DNS resolution
- HTTP client/server
- Port availability
- Firewall traversal
- SSL/TLS (if HTTPS)

**Better approach:**
```python
# Direct subprocess spawn (no HTTP layer)
asyncio.create_subprocess_exec(...)
```

**Advantages:**
- No network dependencies
- No port conflicts
- No SSL issues
- Simpler, faster, more reliable

---

### 18. No Delivery Guarantee
**Problem:** Fire-and-forget HTTP POST with no retry

**Current code:**
```python
try:
    response = client.post(url, ...)
except Exception as e:
    logger.error(f"Failed to trigger worker: {e}")
    # Jobs just don't run ¯\_(ツ)_/¯
```

**Missing:**
- Retry logic
- Dead letter queue
- Monitoring/alerts
- Fallback mechanism

**Impact:** Silent failures, jobs never execute

---

### 19. Cache Coherence in Distributed Systems
**Problem:** LocMemCache doesn't work across instances

**Fundamental issue:**
```python
cache.set(CACHE_KEY, True, timeout=60)
```

In-memory cache is per-process/instance, not shared

**Requirements for multi-instance:**
- Redis/Memcached for shared cache
- Distributed locks for throttle coordination
- Adds external dependencies

---

### 20. No Visibility into Failures
**Problem:** All errors logged but not surfaced to users

**User experience:**
```
User: "Why aren't my cron jobs running?"
Developer: *checks logs*
Developer: "Oh, INTERNAL_SECRET not set 3 days ago"
```

**Missing:**
- Admin dashboard showing last successful trigger
- Alerts when jobs haven't run
- Health check endpoint status
- Prometheus metrics

---

## Edge Cases and Race Conditions

### 21. Startup Race Condition
**Problem:** Worker endpoint may not be ready during startup

**Scenario:**
```
Container starts
Web server binding to port... (100ms)
First request arrives
Middleware triggers POST to /_internal/worker
❌ Connection refused: port not yet bound
```

**Impact:** First job trigger always fails

---

### 22. Graceful Shutdown Issues
**Problem:** In-flight subprocesses during container shutdown

**Scenario:**
```
SIGTERM received (Kubernetes pod eviction)
Django starts graceful shutdown (30s timeout)
5 job subprocesses still running
Kubernetes: SIGKILL after 30s
Jobs terminated mid-execution
Database transactions uncommitted
```

**Impact:** Data corruption, incomplete jobs

---

### 23. Signature Replay Attack (Low Risk)
**Problem:** 5-second window for signature reuse

**Attack:**
```
Attacker intercepts legitimate POST
Replays request within 5 seconds
Worker processes job twice
```

**Mitigation:** Atomic job claiming prevents duplicate execution, but wasteful

---

## Multi-Instance Specific Issues

### 24. Load Balancer Health Checks Trigger Jobs
**Problem:** Health checks may hit throttled endpoints

**Scenario:**
```
Load balancer: GET / every 10 seconds (health check)
Middleware: Checks throttle on every request
If 60s passed: Triggers jobs
Health checks cause job triggers
```

**Impact:**
- Jobs triggered by health checks, not real traffic
- Unpredictable execution timing

---

### 25. Rolling Deployments and Race Conditions
**Problem:** Old and new containers running simultaneously

**Scenario:**
```
Rolling update starts
Old container (v1.0): Still processing jobs
New container (v1.1): Starts up, triggers jobs
Both containers processing different jobs from queue ✅ (atomic claiming)
BUT: 2x resource usage during rollout
```

---

## Performance Issues

### 26. Subprocess Overhead
**Problem:** Spawning subprocess for every trigger

**Cost per spawn:**
- Fork: ~10-20ms
- Import Django: ~500ms
- Database connection: ~50ms
- Total: ~570ms overhead per trigger

**At scale:**
```
High-traffic site: 1000 req/sec
Every 60s: Trigger fires
570ms × frequent triggers = noticeable latency
```

---

### 27. Database Connection Pool Exhaustion
**Problem:** Each subprocess creates database connections

**Scenario:**
```
10 subprocesses running concurrently
Each creates 2-5 DB connections
Total: 20-50 connections
Database max_connections: 100
Main app using 60 connections
❌ Connection pool exhausted
```

**Impact:**
- New requests fail with "too many connections"
- Application becomes unavailable

---

## When HTTP Trigger Mode Actually Works

**Ideal conditions (all required):**
1. ✅ Single container/instance deployment
2. ✅ ASGI server (uvicorn/daphne)
3. ✅ `127.0.0.1` works within container
4. ✅ Clock properly sync'd (NTP)
5. ✅ httpx installed
6. ✅ Proper configuration (secret, base URL)
7. ✅ Low to medium traffic (< 100 req/sec)
8. ✅ Short jobs (< 5 minutes)
9. ✅ Not mission-critical cron schedules

**Verdict:** Works reliably only for **simple single-instance ASGI deployments**.

---

## Recommended Alternatives

### Option A: Dedicated Scheduler Container
```yaml
# docker-compose.yml
services:
  web:
    image: myapp
    command: uvicorn myproject.asgi:application

  scheduler:
    image: myapp
    command: |
      bash -c 'while true; do
        python manage.py run_jobs --once
        sleep 60
      done'
```

**Advantages:**
- Guaranteed execution
- No network dependencies
- Independent scaling
- Clear separation of concerns

---

### Option B: Serverless/Cron Invocation
```yaml
# AWS EventBridge
schedule: rate(1 minute)
target: ECS Task
command: python manage.py run_jobs --once

# Google Cloud Scheduler
schedule: "* * * * *"
target: Cloud Run Job
```

**Advantages:**
- Guaranteed execution
- No always-on costs
- Managed infrastructure
- Built-in monitoring

---

### Option C: Direct Subprocess (No HTTP)
```python
# Middleware spawns subprocess directly
if should_trigger():
    subprocess.Popen([
        sys.executable,
        manage_py,
        "run_jobs",
        "--once"
    ], env=os.environ, start_new_session=True)
```

**Advantages:**
- No HTTP complexity
- No network dependencies
- Simpler implementation
- More reliable

---

## Conclusion

HTTP trigger mode is a clever hack to avoid separate scheduler processes, but introduces numerous failure modes that make it unsuitable for production use, especially in multi-instance deployments.

**Critical issues:**
1. Network dependencies for local operations
2. No delivery guarantees
3. Silent failures
4. Cache coherence problems
5. Resource exhaustion risks

**Recommendation:** Deprecate direct mode, document HTTP trigger mode limitations clearly, and recommend dedicated scheduler processes or serverless invocation for production deployments.

---

## Related Files
- `BUGS.md` - General bug tracker
- `ROADMAP.md` - Feature planning
- `REVIEW.md` - Project status summary
