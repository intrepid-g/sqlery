"""Create sqlery tables via compat.initialize (calls SQLModel.metadata.create_all)."""

import os
import sys
import time

DATABASE_URL = os.environ["DATABASE_URL"]

# Small retry loop — postgres container may not accept connections immediately
for attempt in range(15):
    try:
        import sqlalchemy as sa
        sa.create_engine(DATABASE_URL).connect().close()
        break
    except Exception as exc:
        print(f"  Waiting for postgres... ({exc})")
        time.sleep(2)
else:
    print("ERROR: could not connect to postgres after 15 attempts")
    sys.exit(1)

from sqlery.compat import initialize  # noqa: E402

initialize(database_url=DATABASE_URL, max_workers=0, enable_daemon=False)
print("✓ Sqlery tables ready")
