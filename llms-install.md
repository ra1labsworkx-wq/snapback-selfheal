# Installing Snapback (for AI agents / Cline)

Snapback is a **hosted remote MCP server** — there is nothing to build or run locally. You connect to the hosted
endpoint. Most tools are **free and need no token**.

## The one thing to do: add the MCP server

**Endpoint:** `https://api.snapback.sh/mcp` (Streamable HTTP, JSON-RPC 2.0)

### Claude Code / Cursor / Windsurf / any MCP client — add this to the MCP config:
```json
{
  "mcpServers": {
    "snapback": {
      "url": "https://api.snapback.sh/mcp"
    }
  }
}
```

That's it. No install, no token, no API key for the free tools.

## Verify it works
Call `tools/list` — you should get 23 tools. Then try the free diagnosis:
- Tool: `diagnose_infra_error`
- Argument: `{"error": "dns nxdomain servfail unable to resolve host"}`
- You should get back `family: dns-tls` with a verified fix.

## Optional: a token for the deep tools
The free tools (diagnose_infra_error, detect_loop, budget_guard, search_docs, what_others_did) need **no token**.
Only the deep multi-step trace tools (diagnose_trace, diagnose_batch) are metered. To use those:
- **Free starter token:** `POST https://api.snapback.sh/v1/agents/register` with `{"accepted_terms": true}` → returns a `vdk_` token. Send it as `Authorization: Bearer vdk_...`.
- **Pay-per-call (x402):** POST to `https://api.snapback.sh/mcp/x402` with no token → get a 402 with payment details → sign and retry. No account needed.

## What it does
Diagnoses why an AI agent failed across 46 infrastructure-error families (DNS, Kubernetes, Redis, Stripe, OAuth,
Solana, and more) and returns the **verified fix** — library-first, ~150ms, no LLM on a known error. The self-heal
interceptor (`pip install snapback-selfheal`) can auto-apply the safe, reversible fixes — it never auto-applies a
destructive or state-changing one.

Docs: https://snapback.sh/for-agents · Discovery: https://snapback.sh/.well-known/mcp.json
