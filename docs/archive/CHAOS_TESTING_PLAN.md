# Chaos Testing Plan for Sqlery

## Philosophy
Instead of testing what we expect to work, test what we expect to break. Find edge cases, race conditions, and failure modes before users do.

## Current Test Coverage Analysis
✅ **Well Tested:**
- Basic job execution
- Concurrency control
- Timeout handling
- Retry logic
- Atomic claiming

❌ **Gaps (Chaos Opportunities):**
- Property-based testing (fuzzy inputs)
- Race conditions under high load
- Database connection failures
- Partial failures and corrupted state
- Resource exhaustion
- Network failures (for distributed scenarios)
- Edge cases in scheduling (cron expressions)

---

## Chaos Testing Categories

### 1. Database Chaos 🗄️

**Scenario: Connection Loss During Job Execution**
- Kill database connection mid-transaction
- Expect: Job should fail gracefully, not leave corrupted state
- Risk: Job marked as running but worker crashed

**Scenario: Database Deadlocks**
- Multiple workers claiming same job simultaneously
- Expect: One wins, others back off cleanly
- Risk: Deadlock causes all workers to hang

**Scenario: Disk Full During Write**
- Fill disk while writing job results
- Expect: Proper error handling, job marked failed
- Risk: Partial writes, corrupted data

**Scenario: Slow Query Performance**
- Simulate slow database with deliberate delays
- Expect: Timeouts work correctly, no cascading failures
- Risk: Workers pile up waiting for database

**Scenario: Lost UPDATE Statement**
- Job completes but status update fails
- Expect: Job should be detected as stale and recovered
- Risk: Job runs twice or appears stuck

### 2. Worker Chaos 👷

**Scenario: Worker Killed Mid-Job (SIGKILL)**
- Force kill worker process during job execution
- Expect: Job should be detected as orphaned and reclaimed
- Risk: Job appears running forever

**Scenario: Worker Out of Memory**
- Job consumes all available memory
- Expect: Worker crashes, job marked failed
- Risk: Entire system crashes

**Scenario: Worker Zombie Process**
- Worker process stuck in uninterruptible sleep
- Expect: Supervisor detects and kills it
- Risk: Jobs pile up waiting for zombie worker

**Scenario: Worker Rapid Restart Loop**
- Worker crashes immediately on startup
- Expect: Backoff strategy prevents infinite restarts
- Risk: Resource exhaustion from rapid spawning

**Scenario: Multiple Workers Same Queue**
- Race condition: workers claim same job
- Expect: Atomic claiming ensures only one wins
- Risk: Job runs twice

### 3. Scheduling Chaos 📅

**Scenario: Invalid Cron Expressions**
- Malformed, ambiguous, or impossible cron strings
- Expect: Validation rejects them or handles gracefully
- Risk: Scheduler crashes or creates infinite jobs

**Scenario: Clock Skew**
- System clock jumps forward/backward
- Expect: Scheduled jobs still execute correctly
- Risk: Jobs missed or executed multiple times

**Scenario: DST Transitions**
- Daylight saving time changes
- Expect: Cron schedules handle time changes
- Risk: Jobs run twice or skipped

**Scenario: Missed Schedules (Catch-up)**
- Worker down for 24 hours, then starts
- Expect: Clear policy on whether to catch up missed jobs
- Risk: Thundering herd of overdue jobs

**Scenario: Far Future Schedules**
- Schedule job for year 2100
- Expect: Handled without overflow or weird behavior
- Risk: Integer overflow, date parsing issues

### 4. Input Chaos (Fuzzing) 🎲

**Scenario: Massive Payloads**
- Job with 10MB+ of arguments/kwargs
- Expect: Either accepted or rejected with clear error
- Risk: Memory exhaustion, database bloat

**Scenario: Malformed JSON**
- Corrupted JSON in job arguments
- Expect: Job fails with clear error, doesn't crash worker
- Risk: Worker crashes on deserialization

**Scenario: Unicode Edge Cases**
- Null bytes, emoji, RTL text, control characters
- Expect: Stored and retrieved correctly
- Risk: Encoding issues, SQL injection, XSS in dashboard

**Scenario: SQL Injection Attempts**
- Job names/paths with SQL fragments
- Expect: Parameterized queries prevent injection
- Risk: Database compromise

**Scenario: Extremely Long Strings**
- Task path with 10,000 characters
- Expect: Validation rejects or truncates
- Risk: Database errors, buffer overflows

**Scenario: Negative/Zero/Float Timeouts**
- timeout=-1, timeout=0, timeout=0.0001
- Expect: Validation or sensible defaults
- Risk: Unexpected behavior, infinite waits

**Scenario: Circular References**
- Job arguments with circular object references
- Expect: Serialization fails gracefully
- Risk: Infinite recursion, stack overflow

### 5. Concurrency Chaos 🏃

**Scenario: 1000 Jobs Enqueued Simultaneously**
- Thundering herd of job creation
- Expect: All jobs created successfully
- Risk: Database connection pool exhaustion

**Scenario: Worker Pool Exhaustion**
- More jobs than workers, all jobs take hours
- Expect: Jobs queue properly, no starvation
- Risk: Priority inversion, system unresponsive

**Scenario: Rapid Enqueue/Dequeue Cycle**
- Enqueue and immediately try to claim
- Expect: Atomic operations prevent race conditions
- Risk: Job claimed before fully committed

**Scenario: Competing Schedulers**
- Multiple scheduler instances running
- Expect: Each scheduled job created only once
- Risk: Duplicate scheduled jobs

### 6. Failure Cascade Chaos 💥

**Scenario: Failing Job in Retry Loop**
- Job fails, retries, fails again, repeat
- Expect: Exponential backoff, eventual max retries
- Risk: Retry storm overwhelms system

**Scenario: Downstream Service Down**
- Job calls external API that's timing out
- Expect: Job timeout works, doesn't block other jobs
- Risk: All workers stuck waiting for timeouts

**Scenario: Poison Job**
- Job that crashes worker every time
- Expect: Job marked failed after max retries, doesn't kill all workers
- Risk: Worker pool drained by poison jobs

**Scenario: Memory Leak in Job**
- Job leaks 100MB per execution
- Expect: Worker eventually OOMs and restarts
- Risk: System OOM kills random processes

### 7. State Corruption Chaos 🔧

**Scenario: Manual Database Edits**
- User manually updates job status in database
- Expect: System handles unexpected states
- Risk: Worker confused by invalid state transitions

**Scenario: Clock Reset During Execution**
- System clock changes mid-job
- Expect: Timeout still works based on elapsed time
- Risk: Timeout never triggers or triggers immediately

**Scenario: Partial Migration**
- Database migration interrupted mid-way
- Expect: Clear error, no silent failures
- Risk: Queries fail mysteriously

**Scenario: Stale Heartbeats**
- Worker crashes but heartbeat remains
- Expect: Heartbeat timeout detects dead worker
- Risk: Jobs appear stuck forever

---

## Testing Strategy

### Phase 1: Property-Based Testing (Hypothesis)
- Install `hypothesis` library
- Write property tests for:
  - Job serialization/deserialization (any input should round-trip)
  - Cron expression parsing (valid expressions should never crash)
  - Queue name validation (what characters are allowed?)
  - Timeout values (must be positive numbers)

### Phase 2: Chaos Tests (Controlled Failures)
- Create `tests/chaos/` directory
- Write tests that deliberately:
  - Kill database connections
  - Simulate slow queries
  - Force kill worker processes
  - Inject malformed data
  - Create race conditions

### Phase 3: Load/Stress Tests
- Enqueue thousands of jobs
- Run multiple workers simultaneously
- Measure: throughput, latency, failure rate
- Find breaking points

### Phase 4: Fuzzing (Optional)
- Use tools like `pythonfuzz` or `atheris`
- Fuzz job arguments, task paths, cron expressions
- Goal: Find crashes or hangs

---

## Success Metrics

✅ **Good Outcomes:**
- Tests find bugs we didn't know existed
- System fails gracefully (errors, not crashes)
- Edge cases documented and handled
- Confidence in production resilience

❌ **Bad Outcomes:**
- Tests are too brittle (false positives)
- Tests take too long to run
- Tests don't find real issues

---

## Next Steps

1. ✅ Document chaos scenarios (this file)
2. ⏳ Add `hypothesis` to dev dependencies
3. ⏳ Create `tests/chaos/` directory
4. ⏳ Implement property-based tests first (easy wins)
5. ⏳ Implement worker kill tests (high value)
6. ⏳ Implement input fuzzing tests
7. ⏳ Run tests and document findings

---

**Status**: Planning phase
**Owner**: TBD
**Priority**: High (prevent production failures)
