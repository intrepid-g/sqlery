"""Create the database tables for the async example."""

from sqlery.compat import initialize

DATABASE_URL = "sqlite:///example_async.db"

if __name__ == "__main__":
    initialize(database_url=DATABASE_URL, enable_daemon=False)  # creates the tables
    print("Database tables created in example_async.db")
