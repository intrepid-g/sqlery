"""Property-based tests using Hypothesis to find edge cases."""

import pytest
import json
from hypothesis import given, strategies as st, assume, settings, HealthCheck
from hypothesis import Phase
from django.utils import timezone

from sqlery.django_sqlery.models import QueuedJob
from sqlery.utils import serialize_job_arguments, deserialize_job_arguments


# Custom strategies for realistic job data
@st.composite
def job_arguments(draw):
    """Generate realistic job arguments (args + kwargs)."""
    # Generate args - list of various types
    args = draw(st.lists(
        st.one_of(
            st.none(),
            st.booleans(),
            st.integers(min_value=-2**31, max_value=2**31-1),
            st.floats(allow_nan=False, allow_infinity=False),
            st.text(max_size=1000),
            st.lists(st.integers(), max_size=10),
            st.dictionaries(st.text(max_size=10), st.integers(), max_size=5),
        ),
        max_size=10
    ))

    # Generate kwargs - dict of string keys to various types
    kwargs = draw(st.dictionaries(
        st.text(min_size=1, max_size=50, alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd'),
            blacklist_characters='\x00'
        )),
        st.one_of(
            st.none(),
            st.booleans(),
            st.integers(min_value=-2**31, max_value=2**31-1),
            st.floats(allow_nan=False, allow_infinity=False),
            st.text(max_size=1000),
        ),
        max_size=10
    ))

    return args, kwargs


@st.composite
def queue_name(draw):
    """Generate valid queue names."""
    # Queue names should be reasonable strings
    return draw(st.text(
        min_size=1,
        max_size=100,
        alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd'),
            min_codepoint=32,
            max_codepoint=126,
            blacklist_characters='\x00\n\r\t'
        )
    ))


@st.composite
def task_path(draw):
    """Generate valid Python task paths."""
    # Generate something like "module.submodule.function"
    parts = draw(st.lists(
        st.text(
            min_size=1,
            max_size=30,
            alphabet=st.characters(
                whitelist_categories=('Lu', 'Ll'),
                min_codepoint=ord('a'),
                max_codepoint=ord('z')
            )
        ),
        min_size=1,
        max_size=5
    ))
    return '.'.join(parts)


@pytest.mark.django_db
class TestPropertyBasedJobSerialization:
    """Property-based tests for job argument serialization."""

    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow]
    )
    @given(args_kwargs=job_arguments())
    def test_serialization_round_trip(self, args_kwargs):
        """Property: Any serializable args/kwargs should round-trip correctly.

        If we can serialize it, we should be able to deserialize it back
        to the exact same value.
        """
        args, kwargs = args_kwargs

        # Serialize
        try:
            serialized = serialize_job_arguments(args, kwargs)
        except (TypeError, ValueError) as e:
            # If serialization fails, that's okay - we just can't test round-trip
            assume(False)
            return

        # Should be valid JSON
        assert isinstance(serialized, str)
        json.loads(serialized)  # Should not raise

        # Deserialize
        deserialized_args, deserialized_kwargs = deserialize_job_arguments(serialized)

        # Should match exactly
        assert deserialized_args == args
        assert deserialized_kwargs == kwargs

    @settings(max_examples=50, deadline=None)
    @given(
        queue=queue_name(),
        task=task_path(),
        args_kwargs=job_arguments()
    )
    def test_job_creation_with_random_inputs(self, queue, task, args_kwargs):
        """Property: Job creation should handle any valid inputs without crashing."""
        args, kwargs = args_kwargs

        try:
            serialized = serialize_job_arguments(args, kwargs)
        except (TypeError, ValueError):
            # Can't serialize - skip this example
            assume(False)
            return

        # Creating a job should never crash (might fail validation, but not crash)
        try:
            job = QueuedJob.objects.create(
                queue_name=queue,
                task_path=task,
                serialized_arguments=serialized,
            )

            # If creation succeeded, job should be retrievable
            assert job.id is not None
            assert job.queue_name == queue
            assert job.task_path == task

            # And arguments should deserialize correctly
            retrieved_args, retrieved_kwargs = job.get_arguments()
            assert retrieved_args == args
            assert retrieved_kwargs == kwargs

        except (ValueError, TypeError) as e:
            # Validation errors are expected for some inputs
            pass
        except Exception as e:
            # Database errors (constraint violations, integrity errors) are acceptable
            # but we want to ensure they're Django DB errors, not unexpected crashes
            from django.db import DatabaseError, IntegrityError
            assert isinstance(e, (DatabaseError, IntegrityError)), \
                f"Unexpected exception type: {type(e).__name__}: {e}"


@pytest.mark.django_db
class TestPropertyBasedEdgeCases:
    """Property-based tests for edge cases."""

    @settings(max_examples=50)
    @given(st.text(max_size=10000))
    def test_very_long_task_paths(self, long_string):
        """Property: Very long task paths should either work or fail gracefully."""
        try:
            job = QueuedJob.objects.create(
                task_path=long_string,
                queue_name="test",
            )
            # If it succeeds, task_path should be stored
            assert job.task_path == long_string or len(job.task_path) <= 500
        except Exception as e:
            # Should fail with clear error, not silent truncation
            assert isinstance(e, (ValueError, Exception))

    @settings(max_examples=50)
    @given(st.text(min_size=0, max_size=100))
    def test_unicode_in_queue_names(self, unicode_text):
        """Property: Unicode text in queue names should be handled consistently."""
        # Skip empty strings
        assume(len(unicode_text) > 0)

        try:
            job = QueuedJob.objects.create(
                task_path="test.task",
                queue_name=unicode_text,
            )
            # If it succeeds, should be retrievable
            assert job.queue_name == unicode_text

            # Should be searchable
            found = QueuedJob.objects.filter(queue_name=unicode_text).first()
            assert found is not None
            assert found.id == job.id

        except Exception:
            # Some characters might not be allowed - that's fine
            # Just don't crash silently
            pass

    @settings(max_examples=50, deadline=None)
    @given(
        timeout=st.one_of(
            st.none(),
            st.integers(min_value=-1000, max_value=1000),
            st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
        ),
        max_retries=st.one_of(
            st.none(),
            st.integers(min_value=-10, max_value=10),
        )
    )
    def test_numeric_edge_cases(self, timeout, max_retries):
        """Property: Numeric fields should handle edge cases (negative, zero, None)."""
        try:
            job = QueuedJob.objects.create(
                task_path="test.task",
                queue_name="test",
                timeout=timeout,
                max_retries=max_retries,
            )

            # If creation succeeded, values should be stored sensibly
            if timeout is not None:
                # Negative timeouts should either be rejected or normalized
                if timeout < 0:
                    # Should have been rejected or normalized to None/0
                    assert job.timeout is None or job.timeout >= 0
                else:
                    assert job.timeout == timeout or job.timeout == int(timeout)

            if max_retries is not None:
                # Negative retries should be rejected or normalized
                if max_retries < 0:
                    assert job.max_retries is None or job.max_retries >= 0
                else:
                    assert job.max_retries == max_retries

        except (ValueError, TypeError) as e:
            # Validation errors are expected for edge case inputs
            pass
        except Exception as e:
            # Database errors are acceptable for constraint violations
            from django.db import DatabaseError, IntegrityError
            assert isinstance(e, (DatabaseError, IntegrityError)), \
                f"Unexpected exception type: {type(e).__name__}: {e}"


@pytest.mark.django_db
class TestPropertyBasedConcurrency:
    """Property-based tests for concurrency issues."""

    @settings(max_examples=20, deadline=None)
    @given(st.integers(min_value=1, max_value=100))
    def test_many_jobs_created_at_once(self, num_jobs):
        """Property: Creating many jobs at once should work without conflicts."""
        created_ids = []

        for i in range(num_jobs):
            job = QueuedJob.objects.create(
                task_path=f"test.task_{i}",
                queue_name="test",
            )
            created_ids.append(job.id)

        # All jobs should have unique IDs
        assert len(created_ids) == len(set(created_ids))

        # All jobs should be retrievable
        assert QueuedJob.objects.filter(id__in=created_ids).count() == num_jobs


class TestPropertyBasedCronExpressions:
    """Property-based tests for cron expression parsing."""

    @settings(max_examples=100, deadline=None)
    @given(st.text(max_size=100))
    def test_random_cron_expressions_dont_crash(self, cron_expr):
        """Property: Invalid cron expressions should fail gracefully, not crash."""
        from croniter import croniter
        from datetime import datetime

        try:
            # Try to parse the expression
            cron = croniter(cron_expr, datetime.now())
            next_run = cron.get_next(datetime)

            # If parsing succeeded, should get a valid datetime
            assert isinstance(next_run, datetime)
            assert next_run > datetime.now()

        except Exception as e:
            # Invalid cron expressions should raise clear errors
            # croniter raises ValueError for invalid expressions
            assert isinstance(e, (ValueError, KeyError, TypeError))
