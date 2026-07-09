# LLM Cost Control

## LLM Usage Scope

- Optional scenario generation from natural language
- Test case suggestion based on coverage gaps
- Anomaly detection support (prefer local model path)

## Cost Control Strategy

| Strategy | Implementation |
|---|---|
| Local inference first | Use `llama.cpp` or `ollama`; remote LLM only as fallback |
| Prompt caching | Cache prompt/response by `SHA256(prompt)` in SQLite |
| Token minimization | Use structured JSON schema output; avoid free-form prose |
| Rate limiting | Max 10 remote LLM calls/hour per user; configurable |
| Model selection | Small models (7B) by default; large models for complex analysis only |
| Opt-in only | LLM features disabled by default; enabled via `--enable-ai` |

