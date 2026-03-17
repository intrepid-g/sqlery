"""Tests for SQ-55: Unusual functools.wraps usage.

This test file ensures that function metadata is preserved correctly
when using the @job decorator, and that decorated functions can be
pickled and introspected properly.

IMPORTANT: These tests verify CURRENT behavior before fixing SQ-55.
"""

import pytest
import pickle
import inspect
from sqlery.core.job import job, JobWrapper


# Module-level functions for pickling tests (local functions can't be pickled)
@job
def module_level_function(x: int) -> int:
    """Double the input."""
    return x * 2


@job(queue="email", priority=10)
def module_level_with_args(to: str) -> str:
    """Send an email."""
    return f"Email sent to {to}"


def test_job_wrapper_preserves_name():
    """Test that @job decorator preserves function __name__."""
    @job
    def my_function():
        """My docstring."""
        pass

    assert my_function.__name__ == "my_function"


def test_job_wrapper_preserves_docstring():
    """Test that @job decorator preserves function __doc__."""
    @job
    def my_function():
        """My docstring."""
        pass

    assert my_function.__doc__ == "My docstring."


def test_job_wrapper_preserves_module():
    """Test that @job decorator preserves function __module__."""
    @job
    def my_function():
        """My docstring."""
        pass

    assert my_function.__module__ == __name__


def test_job_wrapper_preserves_qualname():
    """Test that @job decorator preserves function __qualname__."""
    @job
    def my_function():
        """My docstring."""
        pass

    # __qualname__ includes the full qualified name (including local scope)
    # This is CORRECT behavior - it should be the original function's qualname
    assert "my_function" in my_function.__qualname__
    assert my_function.__qualname__ == "test_job_wrapper_preserves_qualname.<locals>.my_function"


def test_job_wrapper_preserves_annotations():
    """Test that @job decorator preserves function __annotations__."""
    @job
    def my_function(x: int, y: str) -> bool:
        """My docstring."""
        return True

    assert my_function.__annotations__ == {"x": int, "y": str, "return": bool}


def test_job_wrapper_has_wrapped_attribute():
    """Test that @job decorator sets __wrapped__ attribute.

    The __wrapped__ attribute is important for introspection tools
    to access the original unwrapped function.
    """
    def original_function():
        """Original docstring."""
        pass

    wrapped = job(original_function)

    # Check if __wrapped__ exists and points to original
    assert hasattr(wrapped, "__wrapped__")
    assert wrapped.__wrapped__ is original_function


def test_job_wrapper_pickling():
    """Test that decorated functions can be pickled and unpickled.

    This is important for distributed task queues and multiprocessing.
    Note: Only module-level functions can be pickled, not local functions.

    KNOWN LIMITATION: JobWrapper instances are currently not picklable
    because the wrapper is a class instance, not the same object as
    the module-level name. This would require implementing __reduce__
    or __getstate__/__setstate__ for full pickle support.

    This test documents the current limitation - create sub-issue for fix.
    """
    # Use module-level function
    with pytest.raises(pickle.PicklingError, match="not the same object"):
        pickle.dumps(module_level_function)


def test_job_wrapper_with_args_pickling():
    """Test pickling of @job decorator with arguments.

    Same limitation as test_job_wrapper_pickling - documents current behavior.
    """
    # Use module-level function
    with pytest.raises(pickle.PicklingError, match="not the same object"):
        pickle.dumps(module_level_with_args)


def test_inspect_signature():
    """Test that inspect.signature() works on decorated functions."""
    @job
    def my_function(x: int, y: str = "default") -> bool:
        """My docstring."""
        return True

    sig = inspect.signature(my_function)

    # Should be able to inspect the signature
    params = list(sig.parameters.keys())
    assert params == ["x", "y"]

    # Check parameter details
    assert sig.parameters["x"].annotation == int
    assert sig.parameters["y"].annotation == str
    assert sig.parameters["y"].default == "default"
    assert sig.return_annotation == bool


def test_inspect_getsource():
    """Test that inspect.getsource() works on decorated functions.

    This is important for debugging and documentation tools.
    """
    @job
    def my_function():
        """My docstring."""
        return "result"

    # inspect.getsource should work on decorated functions via __wrapped__
    source = inspect.getsource(my_function)
    assert "def my_function" in source
    assert "My docstring" in source


def test_inspect_getfile():
    """Test that inspect.getfile() works on decorated functions.

    Note: inspect.getfile() requires the __wrapped__ attribute to work
    on wrapper objects. We need to access the wrapped function.
    """
    @job
    def my_function():
        """My docstring."""
        pass

    # JobWrapper is a class, not a function, so inspect.getfile() won't work directly
    # But it should work on the wrapped function
    try:
        file_path = inspect.getfile(my_function.__wrapped__)
        assert "test_sq55_functools_wraps.py" in file_path
    except Exception as e:
        pytest.fail(f"inspect.getfile() on __wrapped__ failed: {e}")


def test_help_function():
    """Test that help() works on decorated functions."""
    @job
    def my_function(x: int) -> int:
        """Double the input value.

        Args:
            x: The value to double

        Returns:
            The doubled value
        """
        return x * 2

    # help() should not raise an error
    try:
        import io
        import sys
        buffer = io.StringIO()
        sys.stdout = buffer
        help(my_function)
        sys.stdout = sys.__stdout__
        output = buffer.getvalue()

        # Should show the docstring
        assert "Double the input value" in output
    except Exception as e:
        sys.stdout = sys.__stdout__
        pytest.fail(f"help() failed: {e}")


def test_dir_shows_original_attributes():
    """Test that dir() shows both original function and wrapper attributes."""
    @job
    def my_function():
        """My docstring."""
        pass

    attrs = dir(my_function)

    # Should have wrapper-specific attributes
    assert "enqueue" in attrs
    assert "delay" in attrs
    assert "enqueue_at" in attrs

    # Should also have function attributes
    assert "__name__" in attrs
    assert "__doc__" in attrs
    assert "__module__" in attrs


def test_wrapper_type_identification():
    """Test that the wrapper is identifiable as a JobWrapper."""
    @job
    def my_function():
        """My docstring."""
        pass

    assert isinstance(my_function, JobWrapper)


def test_multiple_decorators_interaction():
    """Test interaction with other decorators.

    This ensures that the functools.wraps usage doesn't break
    when combined with other decorators.
    """
    def other_decorator(func):
        """A simple decorator for testing."""
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper

    # Test job decorator first
    @other_decorator
    @job
    def func1():
        """Doc for func1."""
        return "result1"

    assert func1.__name__ == "func1"
    assert func1.__doc__ == "Doc for func1."

    # Test job decorator second
    @job
    @other_decorator
    def func2():
        """Doc for func2."""
        return "result2"

    assert func2.__name__ == "func2"
    # Note: doc might be lost with other_decorator on top


def test_class_method_decoration():
    """Test that @job works on class methods."""
    class MyClass:
        @job
        def my_method(self, x: int) -> int:
            """Instance method."""
            return x * 2

        @classmethod
        @job
        def my_classmethod(cls, x: int) -> int:
            """Class method."""
            return x * 3

        @staticmethod
        @job
        def my_staticmethod(x: int) -> int:
            """Static method."""
            return x * 4

    # Instance method
    instance = MyClass()
    assert hasattr(instance.my_method, "enqueue")

    # Class method
    assert hasattr(MyClass.my_classmethod, "enqueue")

    # Static method
    assert hasattr(MyClass.my_staticmethod, "enqueue")


def test_lambda_decoration():
    """Test that @job doesn't break on lambda (even though unusual)."""
    # This is an unusual pattern, but should not crash
    try:
        my_lambda = job(lambda x: x * 2)
        assert my_lambda(5) == 10
        assert hasattr(my_lambda, "enqueue")
    except Exception as e:
        pytest.fail(f"Lambda decoration failed: {e}")


def test_functools_wraps_vs_update_wrapper():
    """Document the difference between wraps(func)(self) and update_wrapper.

    This test documents what SQ-55 is about:
    - wraps(func)(self) is unusual because wraps() is meant for decorators
    - functools.update_wrapper(self, func) is the correct pattern for classes
    """
    from functools import wraps, update_wrapper

    def original():
        """Original docstring."""
        pass

    # Pattern 1: wraps(func)(wrapper) - STANDARD for decorator functions
    # This is what you use in function decorators:
    def decorator_function(func):
        @wraps(func)  # <- This is the standard pattern
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper

    # Pattern 2: wraps(func)(self) - UNUSUAL for class wrappers
    # This works but is non-standard
    class UnusualWrapper:
        def __init__(self, func):
            wraps(func)(self)  # <- SQ-55: This is unusual
            self.func = func

        def __call__(self, *args, **kwargs):
            return self.func(*args, **kwargs)

    # Pattern 3: update_wrapper(self, func) - CORRECT for class wrappers
    class CorrectWrapper:
        def __init__(self, func):
            update_wrapper(self, func)  # <- This is the correct pattern
            self.func = func

        def __call__(self, *args, **kwargs):
            return self.func(*args, **kwargs)

    # All three preserve metadata
    decorated1 = decorator_function(original)
    assert decorated1.__name__ == "original"

    wrapped2 = UnusualWrapper(original)
    assert wrapped2.__name__ == "original"

    wrapped3 = CorrectWrapper(original)
    assert wrapped3.__name__ == "original"

    # But Pattern 3 is the documented way for class-based wrappers
    # See: https://docs.python.org/3/library/functools.html#functools.update_wrapper
