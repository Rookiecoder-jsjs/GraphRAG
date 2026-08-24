Classify the user's query for a knowledge-graph RAG assistant.
Return ONLY a JSON object: {"intent": "fact_retrieval" | "chitchat" | "should_reject", "reason": "one short clause"}
- fact_retrieval: asks about facts / definitions / relationships retrievable from documents.
- chitchat: greetings, small talk, meta questions about the assistant itself, "thanks", "who are you".
- should_reject: opinions / advice / personal feelings / unsafe or out-of-scope requests that cannot be grounded in the knowledge base and aren't simple chitchat.