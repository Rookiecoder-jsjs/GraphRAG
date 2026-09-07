"""Template name registry — the startup self-check list (GUIDE-003 T2-1).

Every template a caller may load at request time is declared here so
app.main's lifespan can refuse to boot when one is missing from disk.
"""

TEMPLATE_NAMES = (
    "rag_system",
    "citation_instruction",
    "comparison_instruction",
    "chitchat_system",
    "rejection_template",
    "intent_classify",
    "query_rewrite",
    "query_variants",
    "history_compact",
    "judge",
)
