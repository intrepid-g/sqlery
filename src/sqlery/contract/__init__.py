"""Worker-liveness contract: one module per party, each rule a named, pure clause.

Decision clauses take facts and return a decision. Obligation clauses say who must do
what by when. Operative code gathers facts, calls a clause, then acts on the decision.
The matching precedents live in ``tests/precedents``.
"""
