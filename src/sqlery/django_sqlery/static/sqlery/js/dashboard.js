/* Dashboard configuration — populated by the Django template before this file loads */
// DASHBOARD_CONFIG is set by an inline <script> in the template before this file loads.
// Do not redeclare it here — just verify it exists.
if (typeof DASHBOARD_CONFIG === 'undefined') {
    console.error('DASHBOARD_CONFIG not set — dashboard URLs will not work');
    var DASHBOARD_CONFIG = {};
}

// API-first approach - all data from API endpoints
const URLS = {
    stats: () => DASHBOARD_CONFIG.statsUrl,
    tasks: () => DASHBOARD_CONFIG.tasksListUrl,
    taskAction: (id) =>
        DASHBOARD_CONFIG.taskActionUrl.replace("/0/", `/${id}/`),
    queueJobs: (name) =>
        DASHBOARD_CONFIG.queueJobsUrl.replace(
            "__QUEUE__",
            encodeURIComponent(name),
        ),
    vacuum: () => DASHBOARD_CONFIG.vacuumUrl,
    clearJobs: () => DASHBOARD_CONFIG.clearJobsUrl,
};

// Track which queues and stat panels are expanded
const expandedQueues = new Set();
const expandedStats = new Set();
let isRefreshing = false;
let lastStatsData = null;  // Cache last stats response for instant expand

// Activity Feed state
let feedOpen = false;
let feedPaused = false;
let feedFilter = 'all';       // 'all' | 'queued' | 'started' | 'success' | 'failed'
let feedItems = [];           // { id, type, msg, time, jobId }
let feedIdCounter = 0;
const FEED_MAX_ITEMS = 200;
// Track which job+status combos we've already emitted events for
const feedEmittedEvents = new Set();  // "jobId:status"
let feedInitialLoaded = false;
// Cursor: ISO timestamp of the newest event we've processed.
// On first load we fetch the last 50 events; subsequently we fetch only since this cursor.
let feedCursor = null;

// Convert a feed event from the server into the HTML msg string
function feedEventToMsg(ev) {
    const name = ev.name || '';
    const queue = ev.queue || '';
    switch (ev.type) {
        case 'queued':
            return `<strong>${escapeHtml(name)}</strong> queued on <em>${escapeHtml(queue)}</em>`;
        case 'started':
            return `<strong>${escapeHtml(name)}</strong> started on <em>${escapeHtml(queue)}</em>`;
        case 'success': {
            const dur = ev.duration_seconds != null ? ` (${formatElapsed(ev.duration_seconds)})` : '';
            return `<strong>${escapeHtml(name)}</strong> finished successfully${dur}`;
        }
        case 'failed':
            return `<strong>${escapeHtml(name)}</strong> failed${ev.error ? ' — ' + escapeHtml(ev.error.substring(0, 80)) : ''}`;
        case 'warning':
            return ev.msg || 'Warning';
        default:
            return escapeHtml(ev.msg || ev.type);
    }
}

// Poll /feed/ — called on initial load (no cursor → last 50 events) and every refresh cycle.
// Uses feedCursor so we only fetch new events after the first load.
async function pollFeed() {
    try {
        let url = DASHBOARD_CONFIG.activityFeedUrl + '?limit=100';
        if (feedCursor) {
            url += '&since=' + encodeURIComponent(feedCursor);
        }
        const resp = await fetch(url);

        // REGRESSION 2026-05-25: Dashboard kept polling feed silently forever after session expiry
        // Root cause: pollFeed returned silently on any non-OK, never stopping the 3s auto-refresh.
        // Fix: Handle 401/403 by stopping auto-refresh and showing a toast so the user knows to re-login.
        if (resp.status === 401 || resp.status === 403) {
            if (typeof autoRefreshInterval !== "undefined") clearInterval(autoRefreshInterval);
            showToast("Session expired", "Please reload and sign in again.", "error");
            return;
        }

        if (!resp.ok) return;
        const data = await resp.json();

        const events = (data.events || []).reverse(); // server returns newest-first; reverse to oldest-first
        if (events.length === 0) {
            feedInitialLoaded = true;
            return;
        }

        // Update cursor to the newest event's timestamp
        // events is oldest-first, so last item is newest
        const newestTime = events[events.length - 1].time;
        if (!feedCursor || newestTime > feedCursor) {
            feedCursor = newestTime;
        }

        const isInitial = !feedInitialLoaded;
        const newItems = [];

        for (const ev of events) {
            const key = `${ev.job_id}:${ev.type}`;
            if (feedEmittedEvents.has(key)) continue;
            feedEmittedEvents.add(key);

            const item = {
                id: ++feedIdCounter,
                type: ev.type,
                msg: feedEventToMsg(ev),
                subtitle: ev.subtitle || null,
                time: new Date(ev.time),
                jobId: ev.job_id,
            };
            newItems.push(item);
        }

        if (newItems.length === 0) {
            feedInitialLoaded = true;
            return;
        }

        if (isInitial) {
            // First load: populate feedItems oldest-first then render all at once
            feedItems = newItems.concat(feedItems);
            if (feedItems.length > FEED_MAX_ITEMS) feedItems = feedItems.slice(0, FEED_MAX_ITEMS);
            feedInitialLoaded = true;
            renderFeed();
        } else {
            // Subsequent polls: prepend new items (newest at top) without full re-render
            // Iterate oldest→newest so the last unshift lands at feedItems[0] (top).
            if (!feedPaused) {
                for (let i = 0; i < newItems.length; i++) {
                    addFeedEvent(newItems[i].type, newItems[i].msg, newItems[i].time, newItems[i].jobId, newItems[i].subtitle);
                }
            } else {
                // Still track items even when paused, just update badge
                feedItems = newItems.concat(feedItems);
                if (feedItems.length > FEED_MAX_ITEMS) feedItems = feedItems.slice(0, FEED_MAX_ITEMS);
                updateFeedBadge();
            }
        }

        // Prevent feedEmittedEvents from growing unbounded
        if (feedEmittedEvents.size > 5000) feedEmittedEvents.clear();

    } catch (e) {
        console.error('Feed poll error:', e);
        feedInitialLoaded = true;
    }
}

// Initial feed load
setTimeout(() => pollFeed().catch(e => console.error('Feed init error:', e)), 100);

function toggleFeed() {
    feedOpen = !feedOpen;
    document.getElementById('feed-header').classList.toggle('open', feedOpen);
    document.getElementById('feed-body').classList.toggle('open', feedOpen);
    updateFeedBadge();
}

function toggleFeedPause() {
    feedPaused = !feedPaused;
    const btn = document.getElementById('feed-pause-btn');
    btn.classList.toggle('active', feedPaused);
    btn.innerHTML = feedPaused ? '&#x25B6;' : '&#x23F8;';
    btn.title = feedPaused ? 'Resume feed' : 'Pause feed';
}

function clearFeed() {
    feedItems = [];
    renderFeed();
}

function updateHealthPanel(warnings) {
    const panel = document.getElementById('health-panel');
    if (!panel) return;

    if (!warnings || warnings.length === 0) {
        panel.style.display = 'none';
        panel.innerHTML = '';
        return;
    }

    panel.style.display = 'block';
    panel.innerHTML = `
        <div style="border:2px solid #e67e22;border-radius:4px;background:#fff8f0;padding:0.75rem 1rem;">
            <div style="font-weight:600;color:#e67e22;margin-bottom:0.5rem;">&#x26A0;&#xFE0F; System Health Issues</div>
            ${warnings.map((w, i) => `
                <div style="display:flex;align-items:center;gap:0.75rem;padding:0.4rem 0;${i > 0 ? 'border-top:1px solid #f5d9b8;' : ''}">
                    <span style="flex:1;font-size:0.9rem;">${escapeHtml(w.msg)}</span>
                    ${w.action ? `<button
                        onclick="executeHealthAction(${JSON.stringify(w.action).replace(/"/g, '&quot;')})"
                        ${w.action.kind === 'manual_intervention' ? 'data-intervention-btn' : ''}
                        style="white-space:nowrap;padding:0.25rem 0.75rem;background:#e67e22;color:white;border:none;border-radius:3px;cursor:pointer;font-size:0.85rem;">
                        ${escapeHtml(w.action.label)}
                    </button>` : '<span style="font-size:0.8rem;color:#999;white-space:nowrap;">Manual intervention required</span>'}
                </div>
            `).join('')}
        </div>`;

    // Also inject new warnings into the feed (deduped by message)
    for (const w of warnings) {
        const key = `warning:${w.msg}`;
        if (!feedEmittedEvents.has(key)) {
            feedEmittedEvents.add(key);
            addFeedEvent('warning', w.msg, new Date(), w.job_id || null);
        }
    }
}

async function executeHealthAction(action) {
    if (action.kind === 'stop_job') {
        stopJob(action.job_id, action.job_name || `#${action.job_id}`);
    } else if (action.kind === 'unpause_workers') {
        for (const wid of (action.worker_ids || [])) {
            await workerAction(wid, 'unpause');
        }
    } else if (action.kind === 'manual_intervention') {
        await triggerIntervention();
    }
}

async function triggerIntervention() {
    // Find and disable all intervention buttons
    const btns = document.querySelectorAll('[data-intervention-btn]');
    btns.forEach(b => { b.disabled = true; b.textContent = 'Working...'; });

    try {
        const resp = await fetch('/admin/api/sqlery/intervene/', {
            method: 'POST',
            headers: {'X-CSRFToken': getCsrfToken()},
        });
        const data = await resp.json();

        if (data.status === 'completed') {
            const actions = data.result?.actions_taken || [];
            const msg = actions.length > 0
                ? `Intervention complete: ${actions.join(', ')}`
                : 'Intervention complete: no issues found';
            if (data.note) addFeedEvent('info', data.note, new Date());
            addFeedEvent('info', msg, new Date());
        } else if (data.status === 'rejected') {
            addFeedEvent('info', data.message || 'System is healthy — no intervention needed', new Date());
        } else if (data.status === 'pending') {
            addFeedEvent('info', 'Intervention queued — waiting for daemon to process...', new Date());
        } else if (data.status === 'failed') {
            addFeedEvent('warning', `Intervention failed: ${data.result?.error || 'Unknown error'}`, new Date());
        }
    } catch (e) {
        addFeedEvent('warning', `Intervention request failed: ${e.message}`, new Date());
    } finally {
        btns.forEach(b => { b.disabled = false; b.textContent = 'Fix Now'; });
    }
}

function copyFeedToClipboard() {
    const items = feedItems.slice(0, 100);
    const text = items.map(item => {
        const time = item.time instanceof Date ? item.time.toISOString() : item.time;
        const jobPart = item.jobId ? ` #${item.jobId}` : '';
        return `[${time}] [${item.type.toUpperCase()}]${jobPart} ${item.msg}`;
    }).join('\n');
    navigator.clipboard.writeText(text).then(() => {
        const btn = document.getElementById('feed-copy-btn');
        const prev = btn.innerHTML;
        btn.innerHTML = '&#x2713;';
        setTimeout(() => { btn.innerHTML = prev; }, 1500);
    });
}

function addFeedEvent(type, msg, eventTime, jobId, subtitle) {
    if (feedPaused) return;
    const item = {
        id: ++feedIdCounter,
        type,   // 'started', 'success', 'failed', 'queued'
        msg,
        subtitle: subtitle || null,
        time: eventTime || new Date(),
        jobId: jobId || null,
        isNew: true,
    };
    feedItems.unshift(item);
    if (feedItems.length > FEED_MAX_ITEMS) feedItems.length = FEED_MAX_ITEMS;
    renderFeed();
}

function updateFeedBadge() {
    const badge = document.getElementById('feed-badge');
    if (!feedOpen && feedItems.length > 0) {
        badge.textContent = feedItems.length;
        badge.style.display = 'inline-block';
    } else {
        badge.style.display = 'none';
    }
}

function setFeedFilter(filter) {
    feedFilter = filter;
    // Update button active states
    document.querySelectorAll('.feed-filter').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.filter === filter);
    });
    // Full re-render from feedItems applying the new filter
    const list = document.getElementById('feed-list');
    list.innerHTML = '';
    const filtered = feedFilter === 'all' ? feedItems : feedItems.filter(i => i.type === feedFilter);
    if (filtered.length === 0) {
        list.innerHTML = '<li class="feed-empty">No activity matching this filter.</li>';
        return;
    }
    // feedItems[0] is newest; iterate oldest→newest with insertBefore(firstChild)
    // so each new item is inserted above the previous — newest ends up at top.
    for (let i = filtered.length - 1; i >= 0; i--) {
        list.insertBefore(_buildFeedLi(filtered[i], !!filtered[i].isNew), list.firstChild);
    }
}

function _buildFeedLi(item, isNew = false) {
    const li = document.createElement('li');
    li.className = 'feed-item' + (isNew ? ' feed-item--new' : '');
    li.id = `feed-item-${item.id}`;
    if (item.jobId) {
        const jobUrl = DASHBOARD_CONFIG.queuedJobChangeUrl.replace('/0/', `/${item.jobId}/`);
        li.setAttribute('onclick', `window.location='${jobUrl}'`);
        li.title = `View job #${item.jobId}`;
    }
    const timeStr = item.time.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit', fractionalSecondDigits: 3});
    const jobIdStr = item.jobId ? `<span style="color:var(--body-quiet-color,#999);font-size:0.85em;">#${item.jobId}</span> ` : '';
    const subtitleStr = item.subtitle ? `<span style="color:var(--body-quiet-color,#999);font-size:0.8em;display:block;margin-top:1px;padding-left:1.2em;">${item.subtitle}</span>` : '';
    li.innerHTML = `<span class="feed-dot ${item.type}"></span><span class="feed-time">${timeStr}</span><span class="feed-msg">${jobIdStr}${item.msg}${subtitleStr}</span>`;
    return li;
}

function renderFeed() {
    const list = document.getElementById('feed-list');

    updateFeedBadge();

    if (feedItems.length === 0) {
        list.innerHTML = '<li class="feed-empty">Waiting for activity...</li>';
        return;
    }

    // When a filter is active, do a clean re-render each time (simpler, correct)
    if (feedFilter !== 'all') {
        setFeedFilter(feedFilter);
        return;
    }

    // All filter: only prepend new items — iterate oldest-to-newest so the last
    // insertBefore(firstChild) call places the newest item at the top.
    const existingIds = new Set();
    list.querySelectorAll('.feed-item').forEach(el => existingIds.add(el.id));

    for (let i = 0; i < feedItems.length; i++) {
        const item = feedItems[i];
        const elId = `feed-item-${item.id}`;
        if (existingIds.has(elId)) continue;
        list.insertBefore(_buildFeedLi(item, !!item.isNew), list.firstChild);
    }

    // Remove the "waiting" placeholder if present
    const emptyEl = list.querySelector('.feed-empty');
    if (emptyEl) emptyEl.remove();

    // Trim excess DOM nodes from the bottom
    while (list.children.length > FEED_MAX_ITEMS) {
        list.removeChild(list.lastChild);
    }
}

// Detect job events from stats data using job_runs as source of truth
function detectFeedEvents(data) {
    const jobRuns = data.job_runs || [];
    const queuedJobs = data.all_queued_jobs || [];
    const runningJobs = data.all_running_jobs || [];

    // On first poll, set the timestamp cutoff from current server time
    // so we only emit events that happen AFTER the initial feed load
    if (feedFirstPoll) {
        feedFirstPoll = false;
        if (!feedLatestTimestamp) {
            // No initial feed loaded — use server timestamp as cutoff
            feedLatestTimestamp = data.timestamp || new Date().toISOString();
        }
        return;
    }

    // Helper: skip events at or before the initial load cutoff
    function isNewEvent(isoTime) {
        if (!isoTime || !feedLatestTimestamp) return true;
        return isoTime > feedLatestTimestamp;
    }

    const newEvents = [];

    // Helper: only emit if both new (by timestamp) and not yet emitted (by key)
    function shouldEmit(key, isoTime) {
        if (feedEmittedEvents.has(key)) return false;
        if (!isNewEvent(isoTime)) return false;
        feedEmittedEvents.add(key);
        return true;
    }

    // Detect queued jobs
    for (const j of queuedJobs) {
        if (shouldEmit(`${j.id}:queued`, j.created_at)) {
            const name = j.task_name || j.task_path.split('.').pop();
            newEvents.push({
                type: 'queued', jobId: j.id,
                msg: `<strong>${escapeHtml(name)}</strong> queued on <em>${escapeHtml(j.queue_name)}</em>`,
                time: j.created_at ? new Date(j.created_at) : new Date(),
                sort: j.created_at || new Date().toISOString(),
            });
        }
    }

    // Detect running jobs (started)
    for (const j of runningJobs) {
        feedEmittedEvents.add(`${j.id}:queued`);
        if (shouldEmit(`${j.id}:running`, j.started_at)) {
            const name = j.task_name || j.task_path.split('.').pop();
            newEvents.push({
                type: 'started', jobId: j.id,
                msg: `<strong>${escapeHtml(name)}</strong> started on <em>${escapeHtml(j.queue_name)}</em>`,
                time: j.started_at ? new Date(j.started_at) : new Date(),
                sort: j.started_at || new Date().toISOString(),
            });
        }
    }

    // Detect finished/failed from job_runs — catches jobs that
    // started AND finished between polls
    for (const j of jobRuns) {
        feedEmittedEvents.add(`${j.id}:queued`);
        if (j.started_at) feedEmittedEvents.add(`${j.id}:running`);

        if (j.status === 'success' && shouldEmit(`${j.id}:success`, j.finished_at)) {
            const name = j.task_name || j.task_path.split('.').pop();
            if (shouldEmit(`${j.id}:running`, j.started_at)) {
                newEvents.push({
                    type: 'started', jobId: j.id,
                    msg: `<strong>${escapeHtml(name)}</strong> started on <em>${escapeHtml(j.queue_name)}</em>`,
                    time: j.started_at ? new Date(j.started_at) : new Date(),
                    sort: j.started_at || new Date().toISOString(),
                });
            }
            const dur = j.duration_seconds != null ? ` (${formatElapsed(j.duration_seconds)})` : '';
            newEvents.push({
                type: 'success', jobId: j.id,
                msg: `<strong>${escapeHtml(name)}</strong> finished successfully${dur}`,
                time: j.finished_at ? new Date(j.finished_at) : new Date(),
                sort: j.finished_at || new Date().toISOString(),
            });
        }

        if (j.status === 'failed' && shouldEmit(`${j.id}:failed`, j.finished_at)) {
            const name = j.task_name || j.task_path.split('.').pop();
            if (shouldEmit(`${j.id}:running`, j.started_at)) {
                newEvents.push({
                    type: 'started', jobId: j.id,
                    msg: `<strong>${escapeHtml(name)}</strong> started on <em>${escapeHtml(j.queue_name)}</em>`,
                    time: j.started_at ? new Date(j.started_at) : new Date(),
                    sort: j.started_at || new Date().toISOString(),
                });
            }
            const errSnip = j.error_preview ? ` — ${escapeHtml(j.error_preview.substring(0, 80))}` : '';
            newEvents.push({
                type: 'failed', jobId: j.id,
                msg: `<strong>${escapeHtml(name)}</strong> failed${errSnip}`,
                time: j.finished_at ? new Date(j.finished_at) : new Date(),
                sort: j.finished_at || new Date().toISOString(),
            });
        }
    }

    // Sort by time and add to feed in chronological order
    newEvents.sort((a, b) => a.sort.localeCompare(b.sort));
    for (const ev of newEvents) {
        addFeedEvent(ev.type, ev.msg, ev.time, ev.jobId);
    }

    // Prevent feedEmittedEvents from growing unbounded
    if (feedEmittedEvents.size > 5000) {
        // Keep only recent entries by clearing and re-seeding from current data
        feedEmittedEvents.clear();
        for (const j of jobRuns) {
            feedEmittedEvents.add(`${j.id}:queued`);
            if (j.started_at) feedEmittedEvents.add(`${j.id}:running`);
            if (j.status === 'success') feedEmittedEvents.add(`${j.id}:success`);
            if (j.status === 'failed') feedEmittedEvents.add(`${j.id}:failed`);
        }
    }
}

// Timezone preference (local by default, persisted in localStorage)
let dashboardUseUTC = false;
try { dashboardUseUTC = localStorage.getItem('sqlery_dash_tz') === 'utc'; } catch(e) {}

function setDashboardTimezone(useUTC) {
    dashboardUseUTC = useUTC;
    try { localStorage.setItem('sqlery_dash_tz', useUTC ? 'utc' : 'local'); } catch(e) {}
    const btn = document.getElementById('tz-toggle-btn');
    if (btn) btn.textContent = useUTC ? 'UTC' : 'Local';
    // Re-render from cache (no fetch needed)
    if (lastTasksData) renderTaskRows(lastTasksData);
}

// Format datetime with timezone awareness
function formatDateTime(isoString) {
    if (!isoString) return "-";
    try {
        const date = new Date(isoString);
        if (isNaN(date)) return isoString;
        const opts = {
            month: 'numeric', day: 'numeric',
            hour: '2-digit', minute: '2-digit',
            hour12: false,
        };
        if (dashboardUseUTC) opts.timeZone = 'UTC';
        return date.toLocaleString(undefined, opts);
    } catch (e) {
        return isoString;
    }
}

// Format next_run_at with relative time (e.g., "3/13, 10:05 (5m)" or "3/13, 10:05 (2h30m)")
function formatNextRun(isoString) {
    if (!isoString) return "-";
    try {
        const date = new Date(isoString);
        if (isNaN(date)) return isoString;
        const base = formatDateTime(isoString);
        const now = Date.now();
        const diffMs = date.getTime() - now;
        if (diffMs < 0) return base;  // already past
        const totalMin = Math.floor(diffMs / 60000);
        const d = Math.floor(totalMin / 1440);
        const h = Math.floor((totalMin % 1440) / 60);
        const m = totalMin % 60;
        let rel;
        if (d > 0) rel = `${d}d${h}h`;
        else if (h > 0 && m > 0) rel = `${h}h${m}m`;
        else if (h > 0) rel = `${h}h`;
        else rel = `${m}m`;
        return `${base} <span style="color: var(--body-quiet-color, #888); font-size: 0.85em;">(${rel})</span>`;
    } catch (e) {
        return isoString;
    }
}

// --- Table sorting ---
let currentSort = { column: null, direction: 'asc' };
let lastTasksData = null;  // cache for re-sorting without re-fetching

// Column indices and sort keys
const SORT_COLUMNS = {
    1: { key: 'name', type: 'string' },      // Name
    2: { key: 'schedule_type', type: 'string' }, // Type
    4: { key: 'enabled', type: 'boolean' },   // Status
    7: { key: 'next_run_sort', type: 'number' },  // Next Run (epoch seconds)
};

function sortTasks(colIndex) {
    const col = SORT_COLUMNS[colIndex];
    if (!col) return;
    if (currentSort.column === colIndex) {
        currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
    } else {
        currentSort.column = colIndex;
        currentSort.direction = 'asc';
    }
    // Update header indicators
    document.querySelectorAll('#tasks-table thead th[data-sort]').forEach(th => {
        const idx = parseInt(th.dataset.sort);
        const arrow = th.querySelector('.sort-arrow');
        if (!arrow) return;
        if (idx === colIndex) {
            arrow.textContent = currentSort.direction === 'asc' ? ' \u25B2' : ' \u25BC';
            arrow.style.opacity = '1';
        } else {
            arrow.textContent = ' \u25B2';
            arrow.style.opacity = '0.3';
        }
    });
    if (lastTasksData) renderTaskRows(lastTasksData);
}

function getSortedTasks(tasks) {
    if (!currentSort.column) return tasks;
    const col = SORT_COLUMNS[currentSort.column];
    if (!col) return tasks;
    const sorted = [...tasks];
    sorted.sort((a, b) => {
        let va = a[col.key], vb = b[col.key];
        if (col.type === 'date' || col.type === 'number') {
            // nulls always go to the end (regardless of asc/desc)
            if (va == null && vb == null) return 0;
            if (va == null) return 1;
            if (vb == null) return -1;
            if (col.type === 'date') {
                va = new Date(va).getTime();
                vb = new Date(vb).getTime();
            }
        } else if (col.type === 'boolean') {
            va = va ? 1 : 0;
            vb = vb ? 1 : 0;
        } else {
            va = (va || '').toString().toLowerCase();
            vb = (vb || '').toString().toLowerCase();
        }
        if (va < vb) return -1;
        if (va > vb) return 1;
        return 0;
    });
    if (currentSort.direction === 'desc') sorted.reverse();
    return sorted;
}

// Update refresh indicator
function updateRefreshIndicator(loading) {
    if (loading) return; // Don't show loading state — it causes flicker
    const text = document.getElementById("refresh-text");
    const now = new Date().toLocaleTimeString();
    text.textContent = `Last updated: ${now}`;
}

// Fetch and update stats
async function updateStats() {
    try {
        const response = await fetch(URLS.stats());
        if (response.status === 429) return;  // rate-limited — skip silently

        // REGRESSION 2026-05-25: Dashboard spammed console with "Failed to fetch stats" every 3s on session expiry
        // Root cause: Only HTTP 429 was treated as a non-error; 401/403 and other non-OK responses threw,
        // causing the catch block to log a hard error on every refresh cycle indefinitely.
        // Fix: Handle 401/403 by stopping auto-refresh and showing a toast; treat other non-OK as
        // transient warnings that skip one tick without throwing.
        if (response.status === 401 || response.status === 403) {
            if (typeof autoRefreshInterval !== "undefined") clearInterval(autoRefreshInterval);
            updateRefreshIndicator(false);
            showToast("Session expired", "Please reload and sign in again.", "error");
            return;
        }

        // if (!response.ok) throw new Error("Failed to fetch stats");
        if (!response.ok) {
            console.warn(`Stats request failed (HTTP ${response.status}); will retry on next refresh.`);
            return;  // transient (e.g. 502/504): skip this tick, keep polling
        }

        const data = await response.json();
        lastStatsData = data;

        document.getElementById("stat-queued").textContent =
            data.job_counts.queued || 0;
        document.getElementById("stat-scheduled").textContent =
            data.job_counts.scheduled || 0;
        document.getElementById("stat-running").textContent =
            data.job_counts.running || 0;
        document.getElementById("stat-success").textContent =
            data.job_counts.success || 0;
        document.getElementById("stat-failed").textContent =
            data.job_counts.failed || 0;
        document.getElementById("stat-tasks").textContent =
            data.scheduled_tasks.total || 0;
        document.getElementById("stat-workers").textContent =
            data.worker_stats?.active || 0;

        // Update inline stat tables if expanded
        if (expandedStats.has('queued')) {
            renderStatTable('queued', data.all_queued_jobs || []);
        }
        if (expandedStats.has('scheduled')) {
            renderStatTable('scheduled', data.all_scheduled_jobs || []);
        }
        if (expandedStats.has('running')) {
            renderStatTable('running', data.all_running_jobs || []);
        }

        // Update health panel and inject any new warnings into the feed
        updateHealthPanel(data.health_warnings || []);

        updateWorkers(data);
        updateQueues(data);
    } catch (error) {
        console.error("Failed to fetch stats:", error);
    }
}

// Toggle stat card inline table
function toggleStatTable(type) {
    const container = document.getElementById(`stat-table-${type}`);
    const card = container.closest('.stat-card');
    if (!container) return;

    if (expandedStats.has(type)) {
        expandedStats.delete(type);
        container.classList.remove('open');
        card.classList.remove('expanded');
    } else {
        expandedStats.add(type);
        container.classList.add('open');
        card.classList.add('expanded');
        // Render immediately from cached data, then refresh in background
        if (lastStatsData) {
            const keyMap = {queued: 'all_queued_jobs', scheduled: 'all_scheduled_jobs', running: 'all_running_jobs'};
            renderStatTable(type, lastStatsData[keyMap[type]] || []);
        }
        updateStats();
    }
}

// Render inline table for queued or running stat card
function renderStatTable(type, jobs) {
    const container = document.getElementById(`stat-table-${type}`);
    if (!container) return;

    const jobUrl = (id) => DASHBOARD_CONFIG.queuedJobChangeUrl.replace('/0/', `/${id}/`);

    if (jobs.length === 0) {
        container.innerHTML = `<div class="queue-jobs-empty">No ${type} jobs</div>`;
        return;
    }

    if (type === 'queued') {
        container.innerHTML = `
            <table>
                <thead><tr><th>ID</th><th>Task</th><th>Queue</th><th>Pri</th><th>Created</th><th>Actions</th></tr></thead>
                <tbody>
                    ${jobs.map(j => {
                        const name = j.task_name || j.task_path.split('.').pop();
                        return `<tr onclick="window.location='${jobUrl(j.id)}'">
                            <td>#${j.id}</td>
                            <td title="${escapeHtml(j.task_path)}"><strong>${escapeHtml(name)}</strong></td>
                            <td>${escapeHtml(j.queue_name)}</td>
                            <td>${j.priority}</td>
                            <td>${formatDateTime(j.created_at)}</td>
                            <td>${priorityButtons(j.id)}</td>
                        </tr>`;
                    }).join('')}
                </tbody>
            </table>`;
    } else if (type === 'scheduled') {
        container.innerHTML = `
            <div class="scheduled-bulk-bar" id="scheduled-bulk-bar" style="display:none; padding:0.4rem 0.6rem; background:#f0f0f0; border-radius:4px; margin-bottom:0.4rem; font-size:0.8rem; align-items:center; gap:0.5rem;">
                <span id="scheduled-bulk-count">0 selected</span>
                <button onclick="event.stopPropagation();archiveSelectedScheduledJobs()" style="font-size:0.7rem;padding:0.2rem 0.5rem;background:#6c757d;color:white;border:none;border-radius:3px;cursor:pointer;">Archive Selected</button>
                <button onclick="event.stopPropagation();clearScheduledSelection()" style="font-size:0.7rem;padding:0.2rem 0.5rem;background:#aaa;color:white;border:none;border-radius:3px;cursor:pointer;">Clear</button>
            </div>
            <table>
                <thead><tr>
                    <th style="width:30px"><input type="checkbox" onclick="event.stopPropagation();toggleAllScheduledCheckboxes(this)" title="Select all"></th>
                    <th>ID</th><th>Task</th><th>Queue</th><th>Pri</th><th>Scheduled For</th><th>Actions</th>
                </tr></thead>
                <tbody>
                    ${jobs.map(j => {
                        const name = j.task_name || j.task_path.split('.').pop();
                        const enqueueBtn = `<button onclick="event.stopPropagation();enqueueJobNow(${j.id},'${escapeHtml(name)}')" style="font-size:0.7rem;padding:0.15rem 0.4rem;background:#f0ad4e;color:white;border:none;border-radius:3px;cursor:pointer;">Enqueue Now</button>`;
                        return `<tr onclick="window.location='${jobUrl(j.id)}'">
                            <td onclick="event.stopPropagation()"><input type="checkbox" class="scheduled-job-cb" value="${j.id}" onclick="updateScheduledBulkBar()"></td>
                            <td>#${j.id}</td>
                            <td title="${escapeHtml(j.task_path)}"><strong>${escapeHtml(name)}</strong></td>
                            <td>${escapeHtml(j.queue_name)}</td>
                            <td>${j.priority}</td>
                            <td>${formatDateTime(j.scheduled_at || j.created_at)}</td>
                            <td>${enqueueBtn}</td>
                        </tr>`;
                    }).join('')}
                </tbody>
            </table>`;
    } else {
        // running
        container.innerHTML = `
            <table>
                <thead><tr><th>ID</th><th>Task</th><th>Queue</th><th>Elapsed</th><th>Actions</th></tr></thead>
                <tbody>
                    ${jobs.map(j => {
                        const name = j.task_name || j.task_path.split('.').pop();
                        let elapsed = '-';
                        if (j.elapsed_seconds != null) {
                            const secs = Math.round(j.elapsed_seconds);
                            if (secs >= 3600) {
                                elapsed = Math.floor(secs/3600) + 'h ' + Math.floor((secs%3600)/60) + 'm';
                            } else if (secs >= 60) {
                                elapsed = Math.floor(secs/60) + 'm ' + (secs%60) + 's';
                            } else {
                                elapsed = secs + 's';
                            }
                        }
                        const stopBtn = `<button onclick="event.stopPropagation();stopJob(${j.id},'${escapeHtml(name)}')" style="font-size:0.7rem;padding:0.15rem 0.4rem;background:#dc3545;color:white;border:none;border-radius:3px;cursor:pointer;">Stop</button>`;
                        return `<tr onclick="window.location='${jobUrl(j.id)}'">
                            <td>#${j.id}</td>
                            <td title="${escapeHtml(j.task_path)}"><strong>${escapeHtml(name)}</strong></td>
                            <td>${escapeHtml(j.queue_name)}</td>
                            <td>${elapsed}</td>
                            <td>${stopBtn}</td>
                        </tr>`;
                    }).join('')}
                </tbody>
            </table>`;
    }
}

// Track expanded workers and their sub-sections
const expandedWorkers = new Set();
const expandedWorkerSections = new Set();  // tracks open sub-accordions like "wh-finished-xxx"

// Update workers section (in-place to avoid flicker)
function updateWorkers(data) {
    try {
        const workerStats = data.worker_stats || {};
        const workersList = data.workers_list || [];

        // Update summary text only
        const summary = document.getElementById("workers-summary");
        const newSummary = `<strong>Worker Pool:</strong> ${workerStats.active || 0} active (${workerStats.idle || 0} idle, ${workerStats.busy || 0} busy) / ${workerStats.max_workers || 1} max configured`;
        if (summary.innerHTML.trim() !== newSummary.trim()) {
            summary.innerHTML = newSummary;
        }

        const tbody = document.getElementById("workers-tbody");

        if (workersList.length === 0) {
            tbody.innerHTML = `<tr data-empty><td colspan="9" style="text-align: center; padding: 2rem; color: var(--body-quiet-color, #999);">No active workers at the moment</td></tr>`;
            return;
        }

        // Build a map of current worker IDs in DOM
        const existingRows = {};
        tbody.querySelectorAll('tr[data-worker-id]').forEach(tr => {
            existingRows[tr.dataset.workerId] = tr;
        });

        // Remove empty placeholder if present
        const emptyRow = tbody.querySelector('[data-empty]');
        if (emptyRow) emptyRow.remove();

        // Update or create rows for each worker
        const seenIds = new Set();
        for (const worker of workersList) {
            seenIds.add(worker.id);
            const statusColor = worker.is_paused ? '#f39c12'
                : worker.status === "busy" ? "#f39c12" : "#27ae60";
            const statusLabel = worker.is_paused ? 'paused' : worker.status;
            let jobDisplay = "-";
            let elapsedDisplay = "-";
            if (worker.current_job) {
                const name = worker.current_job.task_name || "";
                const path = worker.current_job.task_path || "";
                const jobLabel = name ? `<strong>${escapeHtml(name)}</strong>` : `<code>${escapeHtml(path)}</code>`;
                const jobId = worker.current_job.id;
                const jobDesc = escapeHtml(name || path);
                jobDisplay = `${jobLabel} <button class="btn btn-danger btn-small" style="margin-left:0.5rem;padding:2px 8px;font-size:0.8em;" onclick="event.stopPropagation();stopJob(${jobId},'${jobDesc}')">Stop</button>`;
                if (worker.current_job.elapsed_seconds != null) {
                    elapsedDisplay = formatElapsed(worker.current_job.elapsed_seconds);
                }
            }
            const isExpanded = expandedWorkers.has(worker.id);

            // Uptime / usage cell
            let uptimeDisplay = '-';
            if (worker.uptime_seconds != null) {
                const uptimeFmt = formatElapsed(worker.uptime_seconds);
                const pct = worker.utilization_pct != null ? worker.utilization_pct : null;
                const busyFmt = worker.busy_seconds != null ? formatElapsed(worker.busy_seconds) : '?';
                const idleFmt = worker.idle_seconds != null ? formatElapsed(worker.idle_seconds) : '?';
                const pctStr = pct != null
                    ? `<span style="font-weight:bold;color:${pct > 80 ? '#27ae60' : pct > 30 ? '#f39c12' : '#999'};">${pct}%</span>`
                    : '';
                uptimeDisplay = `<span title="Alive: ${uptimeFmt} | Busy: ${busyFmt} | Idle: ${idleFmt}">${uptimeFmt} ${pctStr}</span>`;
            }

            // Heartbeat cell — red when stale (>60s)
            const heartbeatAge = worker.heartbeat_age_seconds;
            const heartbeatStale = heartbeatAge != null && heartbeatAge > 60;
            const heartbeatStyle = heartbeatStale ? 'color:#e74c3c;font-weight:bold;' : '';
            const heartbeatTitle = heartbeatAge != null ? ` title="${Math.round(heartbeatAge)}s ago"` : '';
            let heartbeatText;
            if (heartbeatAge != null) {
                if (heartbeatAge < 60) heartbeatText = `${Math.round(heartbeatAge)}s ago`;
                else if (heartbeatAge < 3600) heartbeatText = `${Math.floor(heartbeatAge / 60)}m${Math.round(heartbeatAge % 60)}s ago`;
                else heartbeatText = `${Math.floor(heartbeatAge / 3600)}h${Math.floor((heartbeatAge % 3600) / 60)}m ago`;
            } else {
                heartbeatText = formatDateTime(worker.last_heartbeat);
            }
            const heartbeatDisplay = `<span style="${heartbeatStyle}"${heartbeatTitle}>${heartbeatText}</span>`;

            const cellValues = [
                `<code style="font-size:0.85em;" title="${escapeHtml(worker.id)}">${escapeHtml(worker.friendly_name || worker.id.substring(0,8))}</code>`,
                escapeHtml(worker.node_id),
                String(worker.pid),
                `<span style="color: ${statusColor}; font-weight: bold;">${statusLabel}</span>`,
                jobDisplay,
                elapsedDisplay,
                String(worker.jobs_processed || 0),
                uptimeDisplay,
                heartbeatDisplay,
            ];

            const existingRow = existingRows[worker.id];
            if (existingRow) {
                // Update cells in-place
                const cells = existingRow.querySelectorAll('td');
                for (let i = 0; i < cellValues.length && i < cells.length; i++) {
                    if (cells[i].innerHTML !== cellValues[i]) {
                        cells[i].innerHTML = cellValues[i];
                    }
                }
                // Update expanded class
                existingRow.classList.toggle('expanded', isExpanded);
            } else {
                // New worker — create both rows
                const dataRow = document.createElement('tr');
                dataRow.className = `queue-row ${isExpanded ? 'expanded' : ''}`;
                dataRow.dataset.workerId = worker.id;
                dataRow.style.cursor = 'pointer';
                dataRow.setAttribute('onclick', `toggleWorkerDetail('${worker.id}')`);
                dataRow.innerHTML = cellValues.map(v => `<td>${v}</td>`).join('');

                const detailRow = document.createElement('tr');
                detailRow.dataset.workerDetailRow = worker.id;
                detailRow.innerHTML = `<td colspan="9" style="padding:0 !important; border-bottom: ${isExpanded ? '2px solid var(--primary, #417690)' : 'none'};">
                    <div class="worker-detail-container ${isExpanded ? 'open' : ''}" id="worker-detail-${worker.id}">
                        <div class="worker-detail-inner" id="worker-detail-inner-${worker.id}">
                            <div style="text-align:center; padding:1rem; color:var(--body-quiet-color,#999);">Loading...</div>
                        </div>
                    </div>
                </td>`;

                tbody.appendChild(dataRow);
                tbody.appendChild(detailRow);

                if (isExpanded) fetchWorkerDetail(worker.id);
            }
        }

        // Remove workers no longer in the list
        for (const [id, row] of Object.entries(existingRows)) {
            if (!seenIds.has(id)) {
                const detailRow = tbody.querySelector(`tr[data-worker-detail-row="${id}"]`);
                row.remove();
                if (detailRow) detailRow.remove();
                expandedWorkers.delete(id);
            }
        }

        // Re-fetch details for expanded workers (content updates, DOM preserved)
        for (const wid of expandedWorkers) {
            fetchWorkerDetail(wid);
        }
    } catch (error) {
        console.error("Failed to update workers:", error);
    }
}

function formatElapsed(secs) {
    secs = Math.round(secs);
    if (secs >= 3600) return Math.floor(secs/3600) + 'h ' + Math.floor((secs%3600)/60) + 'm';
    if (secs >= 60) return Math.floor(secs/60) + 'm ' + (secs%60) + 's';
    return secs + 's';
}

function toggleWorkerDetail(workerId) {
    const container = document.getElementById(`worker-detail-${workerId}`);
    const row = container?.closest('tr')?.previousElementSibling;
    if (!container) return;

    if (expandedWorkers.has(workerId)) {
        expandedWorkers.delete(workerId);
        container.classList.remove('open');
        if (row) row.classList.remove('expanded');
    } else {
        expandedWorkers.add(workerId);
        container.classList.add('open');
        if (row) row.classList.add('expanded');
        fetchWorkerDetail(workerId);
    }
}

async function fetchWorkerDetail(workerId) {
    const inner = document.getElementById(`worker-detail-inner-${workerId}`);
    if (!inner) return;

    try {
        const url = DASHBOARD_CONFIG.workerDetailUrl.replace('__WID__', workerId);
        const resp = await fetch(url);
        if (!resp.ok) throw new Error('Failed');
        const w = await resp.json();

        const jobUrl = (id) => DASHBOARD_CONFIG.queuedJobChangeUrl.replace('/0/', `/${id}/`);
        const workerActionUrl = DASHBOARD_CONFIG.workerActionUrl.replace('__WID__', workerId);
        const statusColors = { running: '#f39c12', queued: '#3498db', success: '#27ae60', failed: '#e74c3c' };

        let html = '';

        // Paused banner
        if (w.is_paused) {
            const pausedUntilStr = w.paused_until ? formatDateTime(w.paused_until) : 'indefinitely';
            html += `<div class="paused-banner">
                Worker paused until ${pausedUntilStr}
                <button class="btn-worker" onclick="event.stopPropagation();workerAction('${workerId}','unpause')" style="background:white;color:#f39c12;font-weight:bold;">Unpause</button>
            </div>`;
        }

        // Actions bar
        html += `<div class="worker-actions-bar" onclick="event.stopPropagation();">`;
        if (w.current_job) {
            const jName = w.current_job.task_name || w.current_job.task_path.split('.').pop();
            html += `<button class="btn-worker danger" onclick="stopJob(${w.current_job.id},'${escapeHtml(jName)}')">Stop Current Job</button>`;
        }
        if (!w.is_paused) {
            html += `<button class="btn-worker warning" onclick="workerAction('${workerId}','pause')">Pause</button>`;
            html += `<button class="btn-worker warning" onclick="workerPauseFor('${workerId}')">Pause for...</button>`;
        } else {
            html += `<button class="btn-worker success" onclick="workerAction('${workerId}','unpause')">Unpause</button>`;
        }
        html += `<button class="btn-worker danger" onclick="restartWorker('${workerId}')" title="Send SIGTERM — daemon will spawn a replacement within ~10s" style="margin-left:auto;">↺ Restart</button>`;
        html += `</div>`;

        // Current job
        if (w.current_job) {
            const cj = w.current_job;
            const name = cj.task_name || cj.task_path.split('.').pop();
            html += `<div class="worker-sub-label">Currently Executing</div>
            <div style="padding:0.6rem 0.75rem; border:1px solid #f39c12; border-radius:4px; margin-bottom:0.75rem; background:var(--body-bg,#fff);">
                <a href="${jobUrl(cj.id)}" onclick="event.stopPropagation();" style="text-decoration:none;color:inherit;">
                    <strong>#${cj.id} - ${escapeHtml(name)}</strong>
                </a>
                &nbsp; <code>${escapeHtml(cj.task_path)}</code>
                &nbsp; queue: <strong>${escapeHtml(cj.queue_name)}</strong>
                &nbsp; elapsed: <strong>${formatElapsed(cj.elapsed_seconds)}</strong>
            </div>`;
        }

        // Upcoming jobs
        const upcoming = w.upcoming_jobs || [];
        html += `<div class="worker-sub-label">Upcoming Jobs (${upcoming.length})</div>`;
        if (upcoming.length === 0) {
            html += `<div style="padding:0.5rem; color:var(--body-quiet-color,#999); font-style:italic;">No queued jobs</div>`;
        } else {
            html += `<table class="worker-sub-table" onclick="event.stopPropagation();">
                <thead><tr><th>ID</th><th>Task</th><th>Queue</th><th>Priority</th><th>Created</th><th></th></tr></thead>
                <tbody>`;
            for (const j of upcoming) {
                const name = j.task_name || j.task_path.split('.').pop();
                html += `<tr>
                    <td><a href="${jobUrl(j.id)}" onclick="event.stopPropagation();">#${j.id}</a></td>
                    <td title="${escapeHtml(j.task_path)}"><strong>${escapeHtml(name)}</strong></td>
                    <td>${escapeHtml(j.queue_name)}</td>
                    <td>${j.priority}</td>
                    <td>${formatDateTime(j.created_at)}</td>
                    <td>${priorityButtons(j.id)} <button class="btn-worker danger" style="font-size:0.75rem;padding:0.15rem 0.5rem;" onclick="event.stopPropagation();removeQueuedJob(${j.id},'${escapeHtml(name)}')">Remove</button></td>
                </tr>`;
            }
            html += `</tbody></table>`;
        }

        // Stats pills
        const stats = w.job_stats || {};
        const avgDur = stats.avg_duration != null ? formatElapsed(stats.avg_duration) : '-';
        const wid = workerId;
        const pillBase = DASHBOARD_CONFIG.queuedJobChangelistUrl + `?worker__id__exact=${wid}`;
        const pillStyle = 'padding:0.3rem 0.7rem;border-radius:20px;font-size:0.8rem;font-weight:600;text-decoration:none;display:inline-block;';
        html += `<div class="worker-sub-label">Statistics</div>
            <div style="display:flex;gap:0.75rem;flex-wrap:wrap;margin-bottom:0.75rem;" onclick="event.stopPropagation();">
                <a href="${pillBase}" style="${pillStyle}background:rgba(52,152,219,0.15);color:#3498db;">${stats.total || 0} total</a>
                <a href="${pillBase}&status__exact=success" style="${pillStyle}background:rgba(39,174,96,0.15);color:#27ae60;">${stats.success || 0} success</a>
                <a href="${pillBase}&status__exact=failed" style="${pillStyle}background:rgba(231,76,60,0.15);color:#e74c3c;">${stats.failed || 0} failed</a>
                <span style="${pillStyle}background:rgba(155,89,182,0.15);color:#9b59b6;">avg ${avgDur}</span>
            </div>`;

        // Recently finished jobs (expandable)
        const finished = (w.jobs_history || []).filter(j => j.status === 'success');
        const failed = (w.jobs_history || []).filter(j => j.status === 'failed');

        function renderJobHistorySection(label, jobs, sectionId, color) {
            if (jobs.length === 0) return '';
            const isOpen = expandedWorkerSections.has(sectionId);
            let s = `<div class="worker-sub-label" style="cursor:pointer;user-select:none;" onclick="event.stopPropagation();toggleWorkerSubSection('${sectionId}');">
                <span class="arrow" id="arrow-${sectionId}">${isOpen ? '\u25BE' : '\u25B8'}</span> ${label} (${jobs.length})
            </div>
            <div id="${sectionId}" style="display:${isOpen ? 'block' : 'none'};" onclick="event.stopPropagation();">
            <table class="worker-sub-table">
                <thead><tr><th>ID</th><th>Task</th><th>Queue</th><th>Duration</th>${label.includes('Failed') ? '<th>Error</th>' : ''}</tr></thead>
                <tbody>`;
            for (const j of jobs.slice(0, 20)) {
                const name = j.task_name || j.task_path.split('.').pop();
                const dur = j.duration_seconds != null ? formatElapsed(j.duration_seconds) : '-';
                const errCol = label.includes('Failed') ? `<td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(j.error || '')}">${j.error ? escapeHtml(j.error).substring(0, 60) : '-'}</td>` : '';
                s += `<tr onclick="window.location='${jobUrl(j.id)}'">
                    <td>#${j.id}</td>
                    <td title="${escapeHtml(j.task_path)}"><strong>${escapeHtml(name)}</strong></td>
                    <td>${escapeHtml(j.queue_name)}</td>
                    <td>${dur}</td>
                    ${errCol}
                </tr>`;
            }
            s += `</tbody></table></div>`;
            return s;
        }

        html += renderJobHistorySection('Recently Finished', finished, `wh-finished-${wid.substring(0,8)}`, '#27ae60');
        html += renderJobHistorySection('Recently Failed', failed, `wh-failed-${wid.substring(0,8)}`, '#e74c3c');

        inner.innerHTML = html;
    } catch (error) {
        console.error(`Failed to fetch worker detail for ${workerId}:`, error);
        inner.innerHTML = `<div style="padding:1rem;color:#e74c3c;">Failed to load worker details</div>`;
    }
}

function toggleWorkerSubSection(sectionId) {
    const el = document.getElementById(sectionId);
    const arrow = document.getElementById(`arrow-${sectionId}`);
    if (!el) return;
    if (expandedWorkerSections.has(sectionId)) {
        expandedWorkerSections.delete(sectionId);
        el.style.display = 'none';
        if (arrow) arrow.textContent = '\u25B8';
    } else {
        expandedWorkerSections.add(sectionId);
        el.style.display = 'block';
        if (arrow) arrow.textContent = '\u25BE';
    }
}

async function workerAction(workerId, action, extraBody = {}) {
    try {
        const url = DASHBOARD_CONFIG.workerActionUrl.replace('__WID__', workerId);
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
            body: JSON.stringify({ action, ...extraBody }),
        });
        const data = await resp.json();
        if (data.success) {
            showToast(`Worker ${action}d`, '', 'success');
            refreshAll();
        } else {
            showToast(`Failed to ${action} worker`, data.error || '', 'error');
        }
    } catch (e) {
        showToast(`Failed to ${action} worker`, e.message, 'error');
    }
}

function restartWorker(workerId) {
    if (!confirm('Restart this worker?\n\nThis sends SIGTERM to the process. The daemon will spawn a replacement within ~10 seconds.')) return;
    workerAction(workerId, 'restart');
}

function workerPauseFor(workerId) {
    const input = prompt('Pause worker for how many seconds?', '300');
    if (!input) return;
    const seconds = parseInt(input);
    if (isNaN(seconds) || seconds <= 0) {
        showToast('Invalid number of seconds', '', 'error');
        return;
    }
    workerAction(workerId, 'pause_for', { seconds });
}

async function enqueueJobNow(jobId, jobName) {
    if (!confirm(`Enqueue job #${jobId} (${jobName}) for immediate processing?`)) return;
    try {
        const url = DASHBOARD_CONFIG.enqueueJobNowUrl.replace('/0/', `/${jobId}/`);
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        });
        const data = await resp.json();
        if (data.success) {
            showToast('Job enqueued for immediate processing', `#${jobId}`, 'success');
            refreshAll();
        } else {
            showToast('Failed to enqueue job', data.error || '', 'error');
        }
    } catch (e) {
        showToast('Failed to enqueue job', e.message, 'error');
    }
}

async function removeQueuedJob(jobId, jobName) {
    if (!confirm(`Remove queued job #${jobId} (${jobName}) from queue?`)) return;
    try {
        const url = DASHBOARD_CONFIG.removeQueuedJobUrl.replace('/0/', `/${jobId}/`);
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        });
        const data = await resp.json();
        if (data.success) {
            showToast('Job removed from queue', `#${jobId}`, 'success');
            refreshAll();
        } else {
            showToast('Failed to remove job', data.error || '', 'error');
        }
    } catch (e) {
        showToast('Failed to remove job', e.message, 'error');
    }
}

// --- Scheduled jobs bulk selection helpers ---

function toggleAllScheduledCheckboxes(selectAllCb) {
    document.querySelectorAll('.scheduled-job-cb').forEach(cb => {
        cb.checked = selectAllCb.checked;
    });
    updateScheduledBulkBar();
}

function updateScheduledBulkBar() {
    const checked = document.querySelectorAll('.scheduled-job-cb:checked');
    const bar = document.getElementById('scheduled-bulk-bar');
    const count = document.getElementById('scheduled-bulk-count');
    if (bar) bar.style.display = checked.length > 0 ? 'flex' : 'none';
    if (count) count.textContent = `${checked.length} selected`;
}

function clearScheduledSelection() {
    document.querySelectorAll('.scheduled-job-cb').forEach(cb => { cb.checked = false; });
    const selectAll = document.querySelector('#stat-table-scheduled input[type="checkbox"]:not(.scheduled-job-cb)');
    if (selectAll) selectAll.checked = false;
    updateScheduledBulkBar();
}

async function archiveSelectedScheduledJobs() {
    const ids = Array.from(document.querySelectorAll('.scheduled-job-cb:checked')).map(cb => Number(cb.value));
    if (ids.length === 0) return;
    if (!confirm(`Archive ${ids.length} scheduled job(s)?`)) return;
    try {
        const resp = await fetch(DASHBOARD_CONFIG.archiveScheduledJobsUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
            body: JSON.stringify({ job_ids: ids }),
        });
        const data = await resp.json();
        if (data.success) {
            showToast(`Archived ${data.archived} scheduled job(s)`, '', 'success');
            refreshAll();
        } else {
            showToast('Failed to archive jobs', data.error || '', 'error');
        }
    } catch (e) {
        showToast('Failed to archive jobs', e.message, 'error');
    }
}

async function changeJobPriority(jobId, action) {
    try {
        const url = DASHBOARD_CONFIG.jobPriorityUrl.replace('/0/', `/${jobId}/`);
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
            body: JSON.stringify({ action }),
        });
        const data = await resp.json();
        if (data.success) {
            showToast('Priority updated', `Job #${jobId} priority: ${data.priority}`, 'success');
            refreshAll();
        } else {
            showToast('Failed to change priority', data.error || '', 'error');
        }
    } catch (e) {
        showToast('Failed to change priority', e.message, 'error');
    }
}

function priorityButtons(jobId) {
    const btn = (label, action, title) =>
        `<button class="btn-priority" title="${title}" onclick="event.stopPropagation();changeJobPriority(${jobId},'${action}')">${label}</button>`;
    return btn('\u23EB', 'move_top', 'Move to top')
         + btn('\u25B2', 'bump_up', 'Bump up')
         + btn('\u25BC', 'bump_down', 'Bump down')
         + btn('\u23EC', 'move_bottom', 'Move to bottom');
}

// Update queues section
function updateQueues(data) {
    try {
        const queueStats = data.queue_stats || [];
        const tbody = document.getElementById("queues-tbody");

        if (queueStats.length === 0) {
            if (!tbody.querySelector('[data-empty]')) {
                tbody.innerHTML = `<tr data-empty><td colspan="6" style="text-align: center; padding: 2rem; color: var(--body-quiet-color, #999);">No queues configured</td></tr>`;
            }
            return;
        }

        const existingRows = {};
        tbody.querySelectorAll('tr[data-queue-name]').forEach(tr => {
            existingRows[tr.dataset.queueName] = tr;
        });
        const emptyRow = tbody.querySelector('[data-empty]');
        if (emptyRow) emptyRow.remove();

        const seenQueues = new Set();
        for (const queue of queueStats) {
            const qn = queue.queue_name;
            seenQueues.add(qn);
            const totalActive = (queue.queued || 0) + (queue.running || 0);
            const nameColor = qn === "high" ? "#e74c3c" : qn === "low" ? "#95a5a6" : "#3498db";
            const isExpanded = expandedQueues.has(qn);
            const eqn = escapeHtml(qn);

            const cellValues = [
                `<strong style="color: ${nameColor};">${eqn}</strong>`,
                String(queue.queued || 0),
                String(queue.running || 0),
                String(queue.scheduled || 0),
                String(queue.enabled || 0),
                `<strong>${totalActive}</strong>`,
            ];

            const existingRow = existingRows[qn];
            if (existingRow) {
                const cells = existingRow.querySelectorAll('td');
                for (let i = 0; i < cellValues.length && i < cells.length; i++) {
                    if (cells[i].innerHTML !== cellValues[i]) cells[i].innerHTML = cellValues[i];
                }
                existingRow.classList.toggle('expanded', isExpanded);
            } else {
                const dataRow = document.createElement('tr');
                dataRow.className = `queue-row ${isExpanded ? 'expanded' : ''}`;
                dataRow.dataset.queueName = qn;
                dataRow.setAttribute('onclick', `toggleQueue('${eqn}')`);
                dataRow.innerHTML = cellValues.map(v => `<td>${v}</td>`).join('');

                const detailRow = document.createElement('tr');
                detailRow.className = 'queue-jobs-row';
                detailRow.id = `queue-jobs-row-${eqn}`;
                detailRow.dataset.queueDetailRow = qn;
                detailRow.innerHTML = `<td colspan="6"><div class="queue-jobs-container ${isExpanded ? 'open' : ''}" id="queue-jobs-${eqn}"><div class="queue-jobs-empty">Click to load jobs...</div></div></td>`;

                tbody.appendChild(dataRow);
                tbody.appendChild(detailRow);
            }
        }

        // Remove queues no longer present
        for (const [qn, row] of Object.entries(existingRows)) {
            if (!seenQueues.has(qn)) {
                const detailRow = tbody.querySelector(`tr[data-queue-detail-row="${qn}"]`);
                row.remove();
                if (detailRow) detailRow.remove();
            }
        }

        // Re-fetch jobs for expanded queues
        for (const qn of expandedQueues) {
            fetchQueueJobs(qn);
        }
    } catch (error) {
        console.error("Failed to update queues:", error);
    }
}

// Toggle queue accordion
function toggleQueue(queueName) {
    const row = document.querySelector(`.queue-row[onclick*="'${queueName}'"]`);
    const container = document.getElementById(`queue-jobs-${queueName}`);
    if (!container) return;

    if (expandedQueues.has(queueName)) {
        expandedQueues.delete(queueName);
        container.classList.remove('open');
        if (row) row.classList.remove('expanded');
    } else {
        expandedQueues.add(queueName);
        container.classList.add('open');
        if (row) row.classList.add('expanded');
        fetchQueueJobs(queueName);
    }
}

// Fetch and render jobs for a queue
async function fetchQueueJobs(queueName) {
    const container = document.getElementById(`queue-jobs-${queueName}`);
    if (!container) return;

    try {
        const response = await fetch(URLS.queueJobs(queueName));
        if (!response.ok) throw new Error("Failed to fetch");
        const data = await response.json();
        const jobs = data.jobs || [];

        if (jobs.length === 0) {
            container.innerHTML = `<div class="queue-jobs-empty">No active or recent jobs in this queue</div>`;
            return;
        }

        const jobUrl = (id) => DASHBOARD_CONFIG.queuedJobChangeUrl.replace('/0/', `/${id}/`);

        container.innerHTML = `
            <table class="queue-jobs-table">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Task</th>
                        <th>Status</th>
                        <th>Started</th>
                        <th>Duration</th>
                        <th>Error</th>
                    </tr>
                </thead>
                <tbody>
                    ${jobs.map(j => {
                        const statusColors = {
                            running: '#f39c12',
                            queued: '#3498db',
                            success: '#27ae60',
                            failed: '#e74c3c',
                        };
                        const sc = statusColors[j.status] || '#666';
                        const name = j.task_name || j.task_path.split('.').pop();
                        const dur = j.duration_seconds != null
                            ? (j.duration_seconds >= 60
                                ? Math.floor(j.duration_seconds / 60) + 'm ' + Math.round(j.duration_seconds % 60) + 's'
                                : Math.round(j.duration_seconds) + 's')
                            : (j.started_at ? 'running...' : '-');
                        const err = j.error ? escapeHtml(j.error).substring(0, 60) : '-';
                        return `
                        <tr style="cursor:pointer;" onclick="window.location='${jobUrl(j.id)}'">
                            <td>#${j.id}</td>
                            <td><strong>${escapeHtml(name)}</strong><br/><small><code>${escapeHtml(j.task_path)}</code></small></td>
                            <td><span style="color:${sc};font-weight:bold;">${j.status}</span></td>
                            <td>${formatDateTime(j.started_at)}</td>
                            <td>${dur}</td>
                            <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(j.error || '')}">${err}</td>
                        </tr>`;
                    }).join('')}
                </tbody>
            </table>`;
    } catch (error) {
        console.error(`Failed to fetch jobs for queue ${queueName}:`, error);
        container.innerHTML = `<div class="queue-jobs-empty" style="color:#e74c3c;">Failed to load jobs</div>`;
    }
}

// Render task rows from cached data (called on fetch and on sort/tz change)
function renderTaskRows(tasks) {
    const tbody = document.getElementById("tasks-tbody");

    if (!tasks || tasks.length === 0) {
        tbody.innerHTML = `
        <tr>
            <td colspan="11" style="text-align: center; padding: 2rem;">
                <div class="empty-state">
                    <div class="empty-state-icon">📋</div>
                    <div>No scheduled tasks yet.</div>
                    <div style="margin-top: 1rem;">
                        <a href="${DASHBOARD_CONFIG.scheduledTaskAddUrl}" class="btn btn-primary">Create Your First Task</a>
                    </div>
                </div>
            </td>
        </tr>`;
        return;
    }

    const sorted = getSortedTasks(tasks);

    // Remove loading/empty placeholder
    const emptyRow = tbody.querySelector('[data-empty]');
    if (emptyRow) emptyRow.remove();

    // Build full HTML (simpler than diffing when sort order changes)
    const rows = [];
    for (const task of sorted) {
        const typeColors = { cron: "#3498db", interval: "#9b59b6", once: "#e67e22" };
        const typeColor = typeColors[task.schedule_type] || "#333";
        const checked = selectedTasks.has(task.id) ? 'checked' : '';

        const cellValues = [
            `<input type="checkbox" class="task-checkbox" data-task-id="${task.id}" data-enabled="${task.enabled}" ${checked} onchange="onTaskCheckboxChange()">`,
            `<strong>${escapeHtml(task.name)}</strong>`,
            `<span style="color: ${typeColor}; font-weight: bold;">${escapeHtml(task.schedule_type || "cron")}</span>`,
            `<code>${escapeHtml(task.schedule_display || task.cron_expression || "-")}</code>`,
            `<span class="badge ${task.enabled ? "badge-enabled" : "badge-disabled"}">${task.enabled ? "Enabled" : "Disabled"}</span>`,
            escapeHtml(task.queue_name),
            String(task.priority),
            formatNextRun(task.next_run_at),
            formatDateTime(task.last_run_at),
            `${task.total_jobs} / ${task.failed_jobs} / ${task.queued_jobs}`,
            `<button onclick="enqueueTask(${task.id})" class="btn btn-primary btn-small">Enqueue Now</button>`,
        ];

        const cells = cellValues.map((v, i) => {
            const stop = (i === 0 || i === cellValues.length - 1) ? ' onclick="event.stopPropagation()"' : '';
            return `<td${stop}>${v}</td>`;
        }).join('');
        rows.push(`<tr data-task-id="${task.id}" onclick="navigateToTask(${task.id})">${cells}</tr>`);
    }
    tbody.innerHTML = rows.join('');
}

// Fetch and render tasks
async function updateTasks() {
    try {
        updateRefreshIndicator(true);

        const response = await fetch(URLS.tasks());
        if (response.status === 429) return;  // rate-limited — skip silently

        // REGRESSION 2026-05-25: Dashboard spammed console with "Failed to fetch tasks" every 3s on session expiry
        // Root cause: Only HTTP 429 was treated as a non-error in updateStats; updateTasks had the same bug.
        // Fix: Handle 401/403 by stopping auto-refresh and showing a toast; treat other non-OK as transient.
        if (response.status === 401 || response.status === 403) {
            if (typeof autoRefreshInterval !== "undefined") clearInterval(autoRefreshInterval);
            updateRefreshIndicator(false);
            showToast("Session expired", "Please reload and sign in again.", "error");
            return;
        }

        // if (!response.ok) throw new Error("Failed to fetch tasks");
        if (!response.ok) {
            console.warn(`Tasks request failed (HTTP ${response.status}); will retry on next refresh.`);
            updateRefreshIndicator(false);
            return;
        }

        const data = await response.json();
        // Pre-compute numeric sort key for next_run_at (avoids JS date-parse issues in sort)
        lastTasksData = (data.tasks || []).map(t => {
            if (t.next_run_sort != null) return t;  // backend already provided it
            let sort = null;
            if (t.next_run_at) {
                const ts = new Date(t.next_run_at).getTime();
                if (!isNaN(ts)) sort = ts / 1000;  // epoch seconds, matching backend
            }
            return { ...t, next_run_sort: sort };
        });
        renderTaskRows(lastTasksData);
        updateRefreshIndicator(false);
    } catch (error) {
        console.error("Failed to fetch tasks:", error);
        const tbody = document.getElementById("tasks-tbody");
        tbody.innerHTML = `
        <tr>
            <td colspan="11" style="text-align: center; padding: 2rem; color: #d32f2f;">
                Failed to load tasks. Please refresh the page.
            </td>
        </tr>`;
        updateRefreshIndicator(false);
    }
}

// Navigate to task detail page
function navigateToTask(taskId) {
    window.location.href =
        DASHBOARD_CONFIG.taskDetailUrl.replace("/0/", `/${taskId}/`);
}

// Toast notification system
function showToast(message, details = "", type = "success") {
    const container = document.getElementById("toast-container");

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;

    const icon = type === "success" ? "\u2713" : "\u26A0\uFE0F";

    toast.innerHTML = `
    <div class="toast-icon">${icon}</div>
    <div class="toast-content">
        <div class="toast-message">${message}</div>
        ${details ? `<div class="toast-details">${details}</div>` : ""}
    </div>
`;

    container.appendChild(toast);

    // Auto-dismiss after 4 seconds
    setTimeout(() => {
        toast.classList.add("hiding");
        setTimeout(() => {
            container.removeChild(toast);
        }, 300); // Wait for animation to complete
    }, 4000);
}

// Enqueue task action
async function enqueueTask(taskId) {
    try {
        const response = await fetch(URLS.taskAction(taskId), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCookie("csrftoken"),
            },
            body: JSON.stringify({ action: "enqueue" }),
        });

        const data = await response.json();

        if (data.success) {
            showToast(
                "Task enqueued successfully!",
                `Job ID: ${data.job_id}`,
                "success",
            );
            updateStats();
            updateTasks();
        } else {
            // Show the specific error message from the API
            showToast(
                "Failed to enqueue task",
                data.error || "Unknown error",
                "error",
            );
        }
    } catch (error) {
        console.error("Failed to enqueue task:", error);
        showToast("Failed to enqueue task", "Please try again", "error");
    }
}

// Vacuum database action
async function clearJobsByStatus(status) {
    const label = status === 'failed' ? 'failed' : 'successful';
    if (!confirm(`Delete ALL ${label} jobs? This cannot be undone.`)) return;
    try {
        const response = await fetch(URLS.clearJobs(), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCookie("csrftoken"),
            },
            body: JSON.stringify({ status }),
        });
        const data = await response.json();
        if (data.success) {
            showToast(`Deleted ${data.deleted} ${label} jobs`, '', 'success');
            updateStats();
        } else {
            showToast(`Clear failed: ${data.error}`, '', 'error');
        }
    } catch (error) {
        showToast(`Clear error: ${error.message}`, '', 'error');
    }
}

async function vacuumDatabase() {
    if (
        !confirm(
            "Run database VACUUM? This reclaims disk space after bulk deletes. It may take a moment.",
        )
    )
        return;
    try {
        const response = await fetch(URLS.vacuum(), {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCookie("csrftoken"),
            },
        });
        const data = await response.json();
        if (data.success) {
            alert("Vacuum completed successfully.");
        } else {
            alert(`Vacuum failed: ${data.error}`);
        }
    } catch (error) {
        alert(`Vacuum error: ${error.message}`);
    }
}

// Get CSRF token from cookies
async function stopJob(jobId, jobName) {
    if (!confirm(`Stop job "${jobName}"?`)) return;
    if (!confirm(`Are you sure? This will kill the running job and cannot be undone.`)) return;

    try {
        const url = DASHBOARD_CONFIG.stopJobUrl.replace('/0/', `/${jobId}/`);
        const response = await fetch(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCookie("csrftoken"),
            },
        });
        const data = await response.json();
        if (data.success) {
            showToast("Job stopped successfully", `Job #${jobId}`, "success");
            refreshAll();
        } else {
            showToast("Failed to stop job", data.error || "Unknown error", "error");
        }
    } catch (error) {
        console.error("Failed to stop job:", error);
        showToast("Failed to stop job", error.message, "error");
    }
}

function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
        const cookies = document.cookie.split(";");
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === name + "=") {
                cookieValue = decodeURIComponent(
                    cookie.substring(name.length + 1),
                );
                break;
            }
        }
    }
    return cookieValue;
}

// HTML escape helper
function escapeHtml(unsafe) {
    if (unsafe === null || unsafe === undefined) return "";
    return String(unsafe)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// --- Auto-refresh pause/resume ---
let autoRefreshPaused = false;
let autoRefreshInterval = null;

function toggleAutoRefresh() {
    autoRefreshPaused = !autoRefreshPaused;
    const btn = document.getElementById('btn-pause-refresh');
    const icon = document.getElementById('pause-icon');
    const label = document.getElementById('pause-label');
    if (autoRefreshPaused) {
        btn.classList.add('paused');
        icon.textContent = '\u25B6';
        label.textContent = 'Resume';
    } else {
        btn.classList.remove('paused');
        icon.textContent = '\u23F8';
        label.textContent = 'Pause';
        refreshAll(); // Refresh immediately on resume
    }
}

// --- Task selection & bulk actions ---
const selectedTasks = new Set();

function onTaskCheckboxChange() {
    selectedTasks.clear();
    document.querySelectorAll('.task-checkbox:checked').forEach(cb => {
        selectedTasks.add(parseInt(cb.dataset.taskId));
    });
    updateBulkActionsBar();
    // Auto-pause when selecting tasks to prevent table rebuild losing state
    if (selectedTasks.size > 0 && !autoRefreshPaused) {
        toggleAutoRefresh();
    }
}

function toggleSelectAll(master) {
    document.querySelectorAll('.task-checkbox').forEach(cb => {
        cb.checked = master.checked;
    });
    onTaskCheckboxChange();
}

function clearSelection() {
    selectedTasks.clear();
    document.querySelectorAll('.task-checkbox').forEach(cb => {
        cb.checked = false;
    });
    const selectAll = document.getElementById('select-all-tasks');
    if (selectAll) selectAll.checked = false;
    updateBulkActionsBar();
    // Resume auto-refresh when selection is cleared
    if (autoRefreshPaused) {
        toggleAutoRefresh();
    }
}

function updateBulkActionsBar() {
    const bar = document.getElementById('bulk-actions');
    const count = document.getElementById('bulk-count');
    if (selectedTasks.size > 0) {
        bar.classList.add('visible');
        count.textContent = `${selectedTasks.size} selected`;
    } else {
        bar.classList.remove('visible');
    }
}

async function executeBulkAction() {
    const action = document.getElementById('bulk-action-select').value;
    if (!action) {
        showToast('Select an action first', '', 'error');
        return;
    }
    if (selectedTasks.size === 0) return;

    const ids = Array.from(selectedTasks);

    if (action === 'delete') {
        // Check all selected are disabled
        const enabledSelected = [];
        document.querySelectorAll('.task-checkbox:checked').forEach(cb => {
            if (cb.dataset.enabled === 'true') enabledSelected.push(cb.dataset.taskId);
        });
        if (enabledSelected.length > 0) {
            showToast('Cannot delete enabled tasks', `${enabledSelected.length} selected task(s) are still enabled. Disable them first.`, 'error');
            return;
        }
        if (!confirm(`Delete ${ids.length} task(s)? This cannot be undone.`)) return;
    } else {
        if (!confirm(`${action.charAt(0).toUpperCase() + action.slice(1)} ${ids.length} task(s)?`)) return;
    }

    let successCount = 0;
    let errorCount = 0;

    for (const taskId of ids) {
        try {
            const response = await fetch(URLS.taskAction(taskId), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken'),
                },
                body: JSON.stringify({ action }),
            });
            const data = await response.json();
            if (data.success) {
                successCount++;
            } else {
                errorCount++;
                console.error(`Action ${action} failed for task ${taskId}:`, data.error);
            }
        } catch (e) {
            errorCount++;
            console.error(`Action ${action} failed for task ${taskId}:`, e);
        }
    }

    clearSelection();
    document.getElementById('bulk-action-select').value = '';

    if (successCount > 0) {
        showToast(`${action} completed`, `${successCount} task(s) updated${errorCount ? `, ${errorCount} failed` : ''}`, errorCount ? 'error' : 'success');
    } else {
        showToast(`${action} failed`, `All ${errorCount} task(s) failed`, 'error');
    }

    refreshAll();
}

// Refresh both stats and tasks
async function refreshAll() {
    if (isRefreshing) return;
    isRefreshing = true;

    await Promise.all([updateStats(), updateTasks(), pollFeed()]);

    isRefreshing = false;
}

// Auto-refresh every 3 seconds (respects pause)
autoRefreshInterval = setInterval(() => {
    if (!autoRefreshPaused) refreshAll();
}, 3000);

// Initialize timezone toggle button text
(function() {
    const btn = document.getElementById('tz-toggle-btn');
    if (btn) btn.textContent = dashboardUseUTC ? 'UTC' : 'Local';
})();

// Initial load
refreshAll();
