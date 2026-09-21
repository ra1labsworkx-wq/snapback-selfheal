#!/usr/bin/env python3
"""
Snapback self-heal interceptor for OpenClaw (Sprint M / P0 — the flagship).

Turns Snapback from a tool an agent must REMEMBER to call into infrastructure that runs on EVERY error
automatically. Wraps your agent's tool calls; on ANY error it auto-calls diagnose_infra_error (free, <150ms,
no token, no LLM), applies the THREE-FACTOR GATE, and either self-heals (retry once with the fix) or escalates.

    "Power tools are optional. Infrastructure is not. Make it impossible to NOT use." — validated agent feedback.

THE THREE-FACTOR GATE (auto-apply a fix without a human ONLY when ALL are true):
    1. confidence >= 0.85          (not a low-confidence guess)
    2. source == "library"         (a curated verified fix, not an LLM inference)
    3. auto_safe == true           (action_class is retry|refetch|config — reversible / side-effect-free)
Snapback returns all three in the diagnose_infra_error response (+ a ready-made gate.auto_apply_ok), so this
interceptor just reads them. It NEVER auto-applies a 'mutate' or 'destructive' fix (create/change state, money,
auth grants) — those are logged + escalated to a human. Fails CLOSED: if unsure, escalate.

This is 100% CLIENT-SIDE. It talks only to the free, public diagnose_infra_error endpoint. It never touches
Snapback internals, never needs a paid token for the diagnosis, and never sends your secrets (Snapback also
scrubs server-side). Composes with bridge.py (bridge = post-mortem forwarding; this = live auto-heal).

USAGE (wrap any callable that might raise):
    from snapback_interceptor import SnapbackInterceptor
    heal = SnapbackInterceptor(auto_apply=True)        # or auto_apply=False to only suggest/log

    result = heal.run(my_tool_call, *args, **kwargs)   # auto-diagnoses + retries once on a gated-safe fix

Or as a decorator:
    @heal.guard
    def call_solana_rpc(...): ...

Or manually on a caught error:
    verdict = heal.diagnose("BlockhashNotFound")       # {matched, family, fix, confidence, auto_safe, gate, ...}
    if verdict["gate"]["auto_apply_ok"]:
        ...apply verdict["fix"] and retry...

ENV:
    SNAPBACK_MCP      default https://api.snapback.sh/mcp     (the free tools work with no token)
    SNAPBACK_TOKEN    optional; only needed if you later wire submit_feedback on outcomes
    SNAPBACK_CONF_MIN default 0.85    (the gate's confidence floor)
"""
from __future__ import annotations
import json, os, time, urllib.request, urllib.error
from typing import Any, Callable, Optional

_MCP = os.environ.get("SNAPBACK_MCP", "https://api.snapback.sh/mcp")
_TOKEN = os.environ.get("SNAPBACK_TOKEN")  # optional
_CONF_MIN = float(os.environ.get("SNAPBACK_CONF_MIN", "0.85"))
_AUTO_SAFE_CLASSES = {"retry", "refetch", "config"}


def _mcp_call(tool: str, arguments: dict, token: Optional[str] = None, timeout: float = 8.0) -> dict:
    """Call a Snapback MCP tool via tools/call. diagnose_infra_error is free + needs no token."""
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(_MCP, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return {"matched": False, "error": f"snapback unreachable: {type(e).__name__}"}
    # unwrap the JSON-RPC + MCP content envelope -> the tool's own JSON
    try:
        text = payload["result"]["content"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return payload.get("result", payload)


class SnapbackInterceptor:
    """Wrap tool calls; auto-diagnose + gate-safe self-heal on error."""

    def __init__(self, auto_apply: bool = True, conf_min: float = _CONF_MIN,
                 token: Optional[str] = _TOKEN, on_escalate: Optional[Callable[[dict], None]] = None,
                 on_heal: Optional[Callable[[dict], None]] = None):
        """
        auto_apply   — if True, retry once with the fix when the gate passes; if False, only diagnose + log.
        conf_min     — the gate's confidence floor (default 0.85).
        on_escalate  — callback(verdict) when a fix is NOT auto-safe (log to your ops inbox / alert a human).
        on_heal      — callback(verdict) when a fix WAS auto-applied (for telemetry).
        """
        self.auto_apply = auto_apply
        self.conf_min = conf_min
        self.token = token
        self.on_escalate = on_escalate or (lambda v: None)
        self.on_heal = on_heal or (lambda v: None)

    # ---- the core: diagnose one error string ----
    def diagnose(self, error_text: str, context: str = "", action: str = "") -> dict:
        """Free, no-token, <150ms library match. Returns the verdict incl. the gate contract."""
        args = {"error": str(error_text)[:4000]}
        if context:
            args["context"] = str(context)[:2000]
        if action:
            args["action"] = str(action)[:500]
        return _mcp_call("diagnose_infra_error", args, token=None)  # diagnosis is FREE — never send the token here

    # ---- the three-factor gate ----
    def gate(self, verdict: dict) -> bool:
        """True only when confidence>=conf_min AND source=='library' AND auto_safe. Fails CLOSED.
        Prefer the server's ready-made gate.auto_apply_ok; re-check locally so a stale client can't over-trust."""
        if not verdict or not verdict.get("matched"):
            return False
        conf = float(verdict.get("confidence") or 0.0)
        source = verdict.get("source")
        action_class = verdict.get("action_class")
        auto_safe = verdict.get("auto_safe")
        # local re-derivation of the contract (don't blindly trust a single server field)
        safe = bool(auto_safe) if auto_safe is not None else (action_class in _AUTO_SAFE_CLASSES)
        local_ok = (conf >= self.conf_min) and (source == "library") and safe
        server_ok = bool((verdict.get("gate") or {}).get("auto_apply_ok"))
        return local_ok and (server_ok or verdict.get("gate") is None)

    # ---- report a gated auto-apply outcome (safety telemetry + feeds the moat). Best-effort, needs a token. ----
    def _report_outcome(self, verdict: dict, succeeded: bool) -> None:
        if not self.token:
            return  # report_outcome is token-scoped (attributes to your org); skip silently if no token
        try:
            _mcp_call("report_outcome", {
                "failure_class": verdict.get("failure_class") or verdict.get("family"),
                "family": verdict.get("family"),
                "fix": verdict.get("fix"),
                "confidence": verdict.get("confidence"),
                "action_class": verdict.get("action_class"),
                "succeeded": bool(succeeded),
            }, token=self.token)
        except Exception:
            pass  # telemetry must never break the caller

    # ---- wrap a call: auto-diagnose + optionally self-heal ----
    def run(self, fn: Callable, *args, _fix_kwarg: Optional[str] = None, **kwargs) -> Any:
        """Run fn; on error, diagnose. If the gate passes AND auto_apply, retry ONCE. Else re-raise after logging.
        _fix_kwarg: if your fn accepts a hint kwarg (e.g. retry_after / create_ata=True), name it and the fix
        text is passed through; otherwise the retry is a plain re-run (correct for transient retry/refetch)."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            verdict = self.diagnose(f"{type(e).__name__}: {e}")
            verdict["_original_error"] = f"{type(e).__name__}: {e}"
            if self.gate(verdict):
                self.on_heal(verdict)
                if self.auto_apply:
                    # gated-safe fixes are retry/refetch/config — a single re-run is the correct action for
                    # transient (retry) + re-fetch-then-reapply (refetch). For config the fix is advisory.
                    if _fix_kwarg:
                        kwargs[_fix_kwarg] = verdict.get("fix")
                    time.sleep(0.5)  # brief backoff before the single retry
                    try:
                        result = fn(*args, **kwargs)
                        self._report_outcome(verdict, succeeded=True)   # feeds safety telemetry + the moat
                        return result
                    except Exception:
                        # retry didn't fix it -> report the miss + escalate with the verdict for a human
                        self._report_outcome(verdict, succeeded=False)
                        self.on_escalate(verdict)
                        raise
                # auto_apply off: healed-verdict available but we don't act
                raise
            else:
                # NOT gated-safe (low conf, LLM source, or a mutate/destructive fix) -> escalate, never auto-act
                self.on_escalate(verdict)
                raise

    def guard(self, fn: Callable) -> Callable:
        """Decorator form of run()."""
        def wrapped(*args, **kwargs):
            return self.run(fn, *args, **kwargs)
        wrapped.__name__ = getattr(fn, "__name__", "guarded")
        return wrapped


# ---- tiny demo / smoke test ----
if __name__ == "__main__":
    heal = SnapbackInterceptor(
        auto_apply=False,  # demo: diagnose + print, don't retry
        on_escalate=lambda v: print(f"[ESCALATE] {v.get('family')} · conf={v.get('confidence')} · "
                                    f"action_class={v.get('action_class')} · reason={(v.get('gate') or {}).get('reason')}"),
        on_heal=lambda v: print(f"[AUTO-HEAL OK] {v.get('family')} · {v.get('fix')}"),
    )
    for err in ["deadlock detected 40P01",
                "unable to get local issuer certificate",
                "outcome type blocked do_not_try_again",  # mutate -> must escalate, never auto-heal
                "invalid_grant refresh token"]:            # mutate -> escalate
        v = heal.diagnose(err)
        gated = heal.gate(v)
        print(f"\n{err!r}\n  family={v.get('family')} conf={v.get('confidence')} "
              f"source={v.get('source')} action_class={v.get('action_class')} "
              f"auto_safe={v.get('auto_safe')} -> GATE {'PASS (self-heal)' if gated else 'FAIL (escalate)'}")
