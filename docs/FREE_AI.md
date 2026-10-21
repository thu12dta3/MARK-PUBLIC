# MARK Free AI Router (experimental)

The module src/mark/free_ai.py is disabled by default.
It never downloads remote model weights. When enabled, it picks among
fixed free-tier text routes for isolated public questions, otherwise
it lets the original local Qwen pipeline run unchanged.

**No app can independently guarantee an external account will never bill.**
Even a fixed free-model route may incur charges if the account is upgraded or
the provider changes its plan. Only use accounts with billing fully disabled.

## Routes

| Route | Provider | Model | Account confirmation environment |
|---|---|---|---|
| groq-reason | GroqCloud | openai/gpt-oss-120b | MARK_GROQ_FREE_ACCOUNT |
| groq-code | GroqCloud | qwen/qwen3.8-27b | MARK_GROQ_FREE_ACCOUNT |
| groq-fast | GroqCloud | openai/gpt-oss-20b | MARK_GROQ_FREE_ACCOUNT |
| openrouter-free | OpenRouter | openrouter/free | MARK_OPENROUTER_FREE_ACCOUNT |
| cerebras-free | Cerebras | gpt-oss-120b | MARK_CEREBRAS_FREE_ACCOUNT |
| cloudflare-free | Cloudflare Workers AI | @cf/meta/llama-3.1-8b-instruct | MARK_CLOUDFLARE_FREE_ACCOUNT |

Providers require a key in the MARK **PC process environment**:
GROQ_API_KEY, OPENROUTER_API_KEY, CEREBRAS_API_KEY, CLOUDFLARE_API_TOKEN.
Cloudflare also requires a 32-hex-character CLOUDFLARE_ACCOUNT_ID.
A provider will stay unavailable until both the key and its explicit
MARK_*_FREE_ACCOUNT=1 confirmation exist. No key goes in this repository or
in Android/Wear apps. Confirm you are using a *free, no-billing account*.

## Configuration and use

Add or update this section in ~/.config/mark/config.json:

    "free_ai": {
      "enabled": false,
      "mode": "explicit",
      "timeout": 15,
      "max_attempts": 2,
      "daily_per_endpoint": 10
    }

Set enabled to true only after configuring free-tier credentials.
In explicit mode, enter a standalone PC chat question beginning with:

    @free Explique a diferença entre algoritmos de ordenação.

Mode auto_public allows longer public questions about reasoning/programming
to route automatically. Short/simple questions stay local. This mode may
occasionally misclassify sensitive material: the filter is only heuristic.
Do not use auto_public for confidential workflows. Only the isolated question
is transmitted; conversation history, memory, PDFs, RAG and internal prompts
are never transmitted. The free router currently only operates for PC-origin
text without images or attachments. Mobile/Wear remain local.

## Limitations

- Models are ranked by simple task heuristics, not measured intelligence.
- The daily allowance database is private SQLite at
  ~/.local/state/mark/free-models.sqlite3 and stores **no message text**.
- Each failed attempt also counts against the local daily allowance.
- HTTP rate-limit errors cause temporary cooling off for the route.
- No remote tools, provider-side search, paid model names, arbitrary
  endpoints, paid fallback, credit purchase or subscription upgrade.
- When remote models cannot respond, the ordinary local Qwen workflow runs.
- The account still must be kept on its provider's free plan. Local request
  counts do not prove cost is zero, especially with neuron-based Cloudflare.
- Nothing has been deployed to the actual MARK installation.
- Real providers were NOT called: there were no account keys or approvals.
- This version has no desktop provider management UI yet.
- The privacy classifier is not an anonymization guarantee. Do not send
  secrets, personal history, confidential documents or identifying details.
- The external model only returns informational text; it cannot authorize
  actions or command the local tools.

## Docs checked October 2026

Groq rate limits: https://console.groq.com/docs/rate-limits

Groq chat API: https://console.groq.com/docs/api-reference

OpenRouter pricing: https://openrouter.ai/pricing

OpenRouter free router: https://openrouter.ai/collections/free-models

Cloudflare free limit: https://developers.cloudflare.com/workers-ai/platform/pricing/

Cloudflare API compatibility: https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/

Cerebras free plan: https://inference-docs.cerebras.ai/support/pricing
