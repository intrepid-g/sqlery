"""Async task definitions for basic async example."""

from sqlery import async_job
import asyncio
import aiohttp


@async_job(queue='default', timeout=60)
async def fetch_url(url):
    """Fetch a URL asynchronously."""
    print(f"🌐 Fetching {url}")
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.text()
            print(f"✓ Fetched {len(data)} bytes from {url}")
            return f"Fetched {len(data)} bytes"


@async_job(queue='default', priority=10, timeout=120)
async def process_data_async(data_id, processing_time=2):
    """Process data asynchronously (simulated)."""
    print(f"⚙️  Processing data {data_id}")
    await asyncio.sleep(processing_time)  # Simulate async processing
    print(f"✓ Data {data_id} processed")
    return f"Data {data_id} processed successfully"


@async_job(queue='reports', timeout=180)
async def generate_async_report(report_type, user_id):
    """Generate a report asynchronously (simulated)."""
    print(f"📊 Generating {report_type} report for user {user_id}")
    await asyncio.sleep(3)  # Simulate async report generation
    print(f"✓ {report_type} report generated")
    return f"{report_type} report for user {user_id}"


@async_job(queue='default', timeout=90)
async def send_notifications_batch(user_ids):
    """Send notifications to multiple users concurrently."""
    print(f"📢 Sending notifications to {len(user_ids)} users")

    async def send_one(user_id):
        await asyncio.sleep(0.5)  # Simulate sending
        return f"Sent to user {user_id}"

    # Send concurrently
    results = await asyncio.gather(*[send_one(uid) for uid in user_ids])
    print(f"✓ Sent {len(results)} notifications")
    return f"Sent {len(results)} notifications"
