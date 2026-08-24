Generate $num_variants different search query variations
that would help find relevant documents for answering the original query.

Guidelines:
- Vary the wording and phrasing
- Include synonyms and related terms
- Some can be more specific, some more general
- Include different question forms (what, how, why, etc.)

Original query: "$query"

Return ONLY a JSON array of strings, nothing else. Example format:
["variant 1", "variant 2", "variant 3"]