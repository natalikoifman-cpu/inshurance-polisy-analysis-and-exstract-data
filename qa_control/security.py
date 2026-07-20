"""Capability 3 (security half) — Runtime security guardrails.

``SecurityGuard`` screens three surfaces continuously:

- **Inbound** (``screen_input``): prompt-injection patterns in user text or
  retrieved documents before they reach a model.
- **Outbound** (``screen_output``): data-leak patterns (secrets, PII, internal
  system prompt fragments) in anything an agent is about to return.
- **Actions** (``authorize_action``): a deny-by-default list of financial
  actions agents may never trigger autonomously (trades, transfers, ...).

Every decision is recorded so the control plane can compute a breach
prevention rate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import SecurityPolicy
from .contracts import Finding, Severity

_INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ignore_instructions", re.compile(
        r"(ignore|disregard|forget)\s+(all\s+|your\s+|previous\s+|prior\s+)*"
        r"(instructions|rules|prompts?|guidelines)", re.I)),
    ("role_override", re.compile(
        r"(you\s+are\s+now|act\s+as|pretend\s+to\s+be)\s+(a\s+|an\s+)?"
        r"(different|new|unrestricted|jailbroken|dan\b)", re.I)),
    ("system_prompt_probe", re.compile(
        r"(reveal|show|print|repeat|leak)\s+(me\s+|your\s+|the\s+)*"
        r"(system\s+prompt|instructions|hidden\s+rules)", re.I)),
    ("delimiter_smuggling", re.compile(
        r"(<\s*/?system\s*>|\[/?INST\]|```\s*system|<\|im_start\|>)", re.I)),
    ("embedded_command", re.compile(
        r"(when\s+you\s+read\s+this|upon\s+processing)\s+.{0,40}"
        r"(execute|run|send|transfer|call)", re.I)),
]

_LEAK_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("api_key", re.compile(
        r"\b(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,})\b")),
    ("password_field", re.compile(
        r"(password|passwd|secret[_ ]?key)\s*[:=]\s*\S+", re.I)),
    ("israeli_id", re.compile(r"\bת\.?ז\.?\s*:?\s*\d{9}\b|\bid\s+number\s*:?\s*\d{9}\b", re.I)),
    ("credit_card", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
    ("system_prompt_fragment", re.compile(
        r"(my\s+system\s+prompt\s+(is|says)|here\s+are\s+my\s+instructions)", re.I)),
]


@dataclass
class SecurityDecision:
    allowed: bool
    surface: str                     # input | output | action
    findings: list[Finding] = field(default_factory=list)


class SecurityGuard:
    def __init__(self, policy: SecurityPolicy) -> None:
        self.policy = policy
        self.decisions: list[SecurityDecision] = []

    # -- inbound ---------------------------------------------------------
    def screen_input(self, text: str, source: str = "user") -> SecurityDecision:
        findings = [
            Finding(f"security.injection.{name}", Severity.CRITICAL,
                    f"prompt-injection pattern '{name}' detected in "
                    f"{source} input", {"match": m.group(0)[:80]})
            for name, pattern in _INJECTION_PATTERNS
            if (m := pattern.search(text))
        ]
        if len(text) > self.policy.max_payload_chars:
            findings.append(Finding(
                "security.input_too_large", Severity.BLOCKING,
                f"input of {len(text)} chars exceeds "
                f"{self.policy.max_payload_chars}"))
        decision = SecurityDecision(allowed=not findings, surface="input",
                                    findings=findings)
        self.decisions.append(decision)
        return decision

    # -- outbound --------------------------------------------------------
    def screen_output(self, text: str, agent: str = "unknown") -> SecurityDecision:
        findings = [
            Finding(f"security.leak.{name}", Severity.CRITICAL,
                    f"potential data leak ('{name}') in output of {agent}",
                    {"match": m.group(0)[:40]})
            for name, pattern in _LEAK_PATTERNS
            if (m := pattern.search(text))
        ]
        decision = SecurityDecision(allowed=not findings, surface="output",
                                    findings=findings)
        self.decisions.append(decision)
        return decision

    # -- actions ---------------------------------------------------------
    def authorize_action(self, agent: str, action: str,
                         params: dict | None = None) -> SecurityDecision:
        findings: list[Finding] = []
        if action.lower() in (a.lower() for a in self.policy.blocked_actions):
            findings.append(Finding(
                "security.unauthorized_action", Severity.CRITICAL,
                f"agent '{agent}' attempted blocked financial action "
                f"'{action}'", {"params": params or {}}))
        decision = SecurityDecision(allowed=not findings, surface="action",
                                    findings=findings)
        self.decisions.append(decision)
        return decision

    # -- KPIs ------------------------------------------------------------
    def breach_stats(self) -> dict[str, float | int]:
        attempts = [d for d in self.decisions if d.findings]
        blocked = [d for d in attempts if not d.allowed]
        return {
            "screened": len(self.decisions),
            "threats_detected": len(attempts),
            "threats_blocked": len(blocked),
            "breach_prevention_rate":
                round(len(blocked) / len(attempts), 4) if attempts else 1.0,
        }
