# #CLEANUP: This file has been moved to src/sqlery/django_sqlery/
# Remove after 2027-05-14 (12 months from CLEAN-01 stamp date 2026-05-14, per Phase 04 CONTEXT).
# This stub exists for backward compatibility during migration.
# Re-export from new location for backward compatibility
try:
    from sqlery.django_sqlery.db_compat import (
        get_database_vendor,
        is_postgresql,
        is_sqlite,
        atomic_claim_job_queryset,
        atomic_claim_job_sqlite,
        atomic_claim_job_postgres,
        atomic_claim_job,
        get_boto3_rds_client,
        execute_rds_data_api_query,
        is_using_rds_data_api,
    )
    __all__ = [
        "get_database_vendor",
        "is_postgresql",
        "is_sqlite",
        "atomic_claim_job_queryset",
        "atomic_claim_job_sqlite",
        "atomic_claim_job_postgres",
        "atomic_claim_job",
        "get_boto3_rds_client",
        "execute_rds_data_api_query",
        "is_using_rds_data_api",
    ]
except ImportError:
    pass
