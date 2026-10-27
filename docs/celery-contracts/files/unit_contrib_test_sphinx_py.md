### `t/unit/contrib/test_sphinx.py`

This file tests Celery's Sphinx documentation extension. It has no portable queue guarantee. The only portable idea is that job definitions are discoverable and documented.

**Documentation of job definitions**
- Build docs for a project with a job defined by a plain decorator and a job defined by a shared decorator, each with a docstring; expect: both docstrings appear in the generated docs.
- Build docs where a job is defined in a module that was not selected for documentation; expect: its docstring is absent from the output.
- Reference a job by name using the cross-reference role; expect: the name renders as a link and the reference text appears in the output.

**Environment**
- If the documentation tool is not installed; expect: the test is skipped, not failed.
