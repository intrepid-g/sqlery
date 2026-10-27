### `t/unit/utils/test_graph.py`

Portable guarantee: job dependency graphs (Celery's DependencyGraph, used for group/chord result trees) order work correctly and tolerate unknown nodes.

**Dependency ordering**
- Job A (no deps), job B (no deps), job C depends on A, job D depends on C and B; compute run order; expect: A before C, C before D, B before D.
- Same graph; list the jobs that have dependencies; expect: exactly C and D.
- Same graph; list each job with its dependencies; expect: A [], B [], C [A], D [C, B].

**Unexpected input**
- Ask for the number of dependents/dependencies of a job that does not exist; expect: 0, no error.
- Render a description of a job id that is not in the graph; expect: a non-empty text, no error.

**Composition**
- Connect one graph onto another graph with the same jobs; expect: no error.

**Presentation (diagnostics)**
- Print the graph representation; expect: non-empty text.
- Export the graph as a diagram (dot format) to a stream; expect: non-empty output.
- Format a node with a custom formatter set; expect: the formatter is applied. With no formatter; expect: node returned unchanged.
