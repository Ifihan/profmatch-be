"""The matching pipeline, in execution order:

    1. profile     — CV + typed interests → structured student profile
    2. discovery   — LLM-guided faculty discovery from the university site
    3. enrichment  — publications + metrics (OpenAlex / Semantic Scholar / Crossref)
    4. scoring     — embedding cosine similarity (student vs each professor)
    5. ranking     — LLM re-rank + explanation over the shortlist

The worker (app/workers/worker.py) orchestrates them and checkpoints each
stage's output to the job row for resumability.
"""
