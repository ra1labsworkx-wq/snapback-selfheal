# Snapback self-heal interceptor for OpenClaw

<!-- MCP Registry ownership proof (https://github.com/modelcontextprotocol/registry) -->
mcp-name: io.github.ra1labsworkx-wq/snapback


**Make Snapback automatic.** Instead of remembering to call the diagnosis tool, wrap your agent's tool calls
once — and every error is auto-diagnosed, and gated-safe fixes are applied and retried *without a human*.

> "Power tools are optional. Infrastructure is not." This turns Snapback from a tool you reach for into
> infrastructure that runs on every failure.

## Connect the MCP server

Snapback is a **hosted remote MCP server** — nothing to run locally. Add it to any MCP client (Claude Code,
Cursor, Cline, VS Code, Windsurf). Most tools are **free and need no token**.

```json
{
  "mcpServers": {
    "snapback": {
      "url": "https://api.snapback.sh/mcp"
    }
  }
}
```

Endpoint: `https://api.snapback.sh/mcp` (Streamable HTTP, JSON-RPC 2.0). Then call `tools/list` (23 tools) and try
`diagnose_infra_error` with `{"error": "dns nxdomain servfail"}` → `family: dns-tls` + a verified fix.

## Tools

Free (no token): `diagnose_infra_error`, `detect_loop`, `budget_guard`, `search_docs`, `what_others_did`,
`recommend_failover`, `cascade_root`, `suggest_budget_recovery`, `convert_trace`, `preflight`, `session_start`,
`session_step`, `session_end`, `discovery`.

Metered (free starter token or x402): `diagnose_trace`, `diagnose_batch`, `get_verdict`, `submit_feedback`,
`report_outcome`, `request_pattern`, `my_usage`, `my_impact`, `agent_memory`.

- **diagnose_infra_error** — a cryptic infra error → its family + the verified fix (library-first, ~150ms, no LLM on a hit). 46 families.
- **detect_loop** — catch a repeating tool-call loop mid-run before the step limit.
- **budget_guard** — catch token / step / context / cost overruns mid-run.
- **what_others_did** — anonymized cross-agent outcomes for a failure class.
- **diagnose_trace** — full multi-step trace diagnosis (metered).

## What it does
On **any** tool-call error, the interceptor:
1. Calls `diagnose_infra_error` (free, no token, no LLM, <150ms).
2. Applies the **three-factor gate** — auto-apply a fix only when **all** are true:
   - `confidence >= 0.85`
   - `source == "library"` (a curated verified fix, not an LLM guess)
   - `auto_safe == true` (the fix is `retry` / `refetch` / `config` — reversible, side-effect-free)
3. If the gate passes → applies the fix and **retries once**. If it fails → **escalates to a human** (logs the
   verdict). It **never** auto-applies a `mutate` or `destructive` fix (create/change state, money, auth grants).

It fails **closed**: when unsure, it escalates rather than acting.

## Install
```bash
# no install needed beyond the stdlib; drop the file in, or:
cp snapback_interceptor.py your_agent/
export SNAPBACK_MCP=https://api.snapback.sh/mcp   # default; the free tools need no token
```

## Use
```python
from snapback_interceptor import SnapbackInterceptor

heal = SnapbackInterceptor(
    auto_apply=True,
    on_escalate=lambda v: my_ops_inbox.log(v),   # human sees non-auto-safe verdicts
    on_heal=lambda v: metrics.count("snapback.autoheal", family=v["family"]),
)

# wrap any call that might raise:
result = heal.run(call_solana_rpc, wallet, amount)

# or as a decorator:
@heal.guard
def call_solana_rpc(wallet, amount): ...

# or manually on a caught error:
verdict = heal.diagnose("BlockhashNotFound")
if verdict["gate"]["auto_apply_ok"]:
    ...apply verdict["fix"] and retry...
```

## The gate contract (returned by Snapback)
`diagnose_infra_error` returns `{matched, family, fix, confidence, source, action_class, auto_safe, gate}`.
`action_class` is one of:

| class | meaning | auto-safe? |
|---|---|---|
| `retry` | re-run with backoff (idempotent) | ✅ |
| `refetch` | re-fetch/re-price/re-sync then re-apply | ✅ |
| `config` | client-side param change (raise max_tokens, serve intermediate cert) | ✅ (to suggest) |
| `mutate` | creates/changes external state, money, auth | ❌ escalate |
| `destructive` | deletes/reverts/irreversible | ❌ never |

**Defense in depth (1.8.3):** even a reversible-class fix is down-graded to escalate if its text contains a
destructive command (delete a pod, flush keys, drop a table). The gate returns `gate.contains_destructive_step`,
so a fix can never auto-run a state-destroying step regardless of its action_class.

`gate.auto_apply_ok` is the ready-made verdict; the interceptor also re-derives it locally so a stale client
can't over-trust.

## Privacy & safety
- 100% client-side. Talks only to the free public `diagnose_infra_error` endpoint.
- Never sends your Snapback token for the diagnosis (diagnosis is free).
- Snapback scrubs trace content server-side; this sends only the error string you pass.
- Composes with `bridge.py` (post-mortem forwarding) — this is the live auto-heal layer.

## Roadmap
- `submit_feedback` on the retry outcome (did the fix work?) → feeds the shared library (the network effect).
- Hermes + LangChain adapters (same gate, different framework hook).
