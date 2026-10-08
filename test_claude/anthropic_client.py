"""Standalone Claude (Anthropic API) client for the test_claude/ sandbox.

Deliberately NOT part of packages/integrations/ and NOT registered with the
app's integration_registry - this file exists only so triage_via_claude.py
can pass it into packages.workflows.triage.run_triage_bugs_workflow() in
place of a GeminiClient (duck-typed: that function only ever calls
`.assess_bug(...)` on whatever client it's given). Nothing in the main app
imports this module, so it cannot affect existing functionality.

Mirrors GeminiClient.assess_bug()'s prompt/response-shape exactly, so a
side-by-side comparison between Gemini and Claude is apples-to-apples.
"""

import asyncio
import json
import platform
from typing import Any, Optional

# Workaround for a bug in the `truststore` package (a transitive dependency
# of the anthropic SDK's bundled HTTP stack): on some macOS setups
# `platform.mac_ver()` returns an empty version string, which crashes
# truststore's SSL context setup at import time with
# `ValueError: invalid literal for int() with base 10: ''`. Only patch it
# when it's actually broken - leave normal platform detection alone
# everywhere else.
if not platform.mac_ver()[0]:
    _real_mac_ver = platform.mac_ver

    def _safe_mac_ver():
        version, versioninfo, machine = _real_mac_ver()
        return (version or "14.0.0", versioninfo, machine)

    platform.mac_ver = _safe_mac_ver

import anthropic

_RATE_LIMIT_RETRY_SECONDS = 10


class AnthropicClient:
    """Minimal Claude client - just enough surface for the triage workflow."""

    def __init__(self, config: dict[str, Any]):
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "claude-sonnet-5")
        self.max_tokens = int(config.get("max_tokens", 2048))
        self._client: Optional[anthropic.AsyncAnthropic] = (
            anthropic.AsyncAnthropic(api_key=self.api_key) if self.api_key else None
        )

    async def _generate(self, prompt: str) -> Optional[str]:
        if not self._client:
            return None
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if getattr(block, "type", "") == "text")

    async def _generate_with_retry(
        self, prompt: str, *, max_attempts: int = 3, base_backoff_seconds: int = _RATE_LIMIT_RETRY_SECONDS
    ) -> Optional[str]:
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await self._generate(prompt)
            except Exception as e:
                last_exc = e
                msg = str(e)
                retryable = any(token in msg for token in ["429", "overloaded", "500", "502", "503", "529"])
                if not retryable or attempt == max_attempts:
                    break
                wait_s = base_backoff_seconds * attempt
                await asyncio.sleep(wait_s)
        if last_exc:
            raise last_exc
        return None

    def _parse_json(self, text: str) -> Any:
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text.strip())

    async def test_connection(self) -> tuple[bool, Optional[str]]:
        if not self.api_key:
            return False, "ANTHROPIC_API_KEY not configured"
        try:
            text = await self._generate("Say 'OK' if you can read this.")
            if text:
                return True, None
            return False, "Empty response from Claude"
        except Exception as e:
            return False, str(e)

    async def assess_bug(self, us_content: str, bug_title: str, bug_description: str) -> dict[str, Any]:
        """Same prompt/contract as GeminiClient.assess_bug() - see that file for the guides."""
        if not self.api_key:
            return {
                "is_real_bug": False, "severity": "Major", "impact": "Moderate / Limited",
                "priority": "Medium", "reasoning": "ANTHROPIC_API_KEY not configured",
            }

        prompt = f"""You are a QA expert reviewing a bug ticket for triage.

Given a user story specification and a bug report, determine:
1. Is this a REAL bug (not a feature request, documentation issue, or user misunderstanding)?
2. If real: What is the severity level?
3. If real: What is the business impact?

User Story (Specification):
{us_content[:8000]}

Bug Title:
{bug_title}

Bug Description:
{bug_description[:4000]}

Respond with valid JSON only (no markdown):
{{
    "is_real_bug": true or false,
    "severity": "Critical" or "Major" or "Minor",
    "impact": "Extensive / Widespread" or "Significant / Large" or "Moderate / Limited" or "Minor / Localized",
    "priority": "Highest" or "High" or "Medium" or "Low" or "Lowest",
    "reasoning": "Brief explanation"
}}

SEVERITY GUIDE:
- Critical: system crash, data loss, security breach, or complete feature unavailability
- Major: core feature broken but workaround exists; significant user impact
- Minor: cosmetic issues, edge cases, minor UX problems

IMPACT GUIDE:
- Extensive / Widespread: affects all or most users / all environments
- Significant / Large: affects many users or multiple key workflows
- Moderate / Limited: affects some users or a non-critical workflow
- Minor / Localized: affects very few users or a rarely used feature

PRIORITY GUIDE:
- Highest: Critical severity + Extensive impact; blocks a release or causes data loss
- High: Critical/Major severity + Significant impact; core feature broken
- Medium: Major severity + Moderate impact; workaround available
- Low: Minor severity or Minor/Localized impact
- Lowest: Cosmetic or very edge-case issues

If NOT a real bug, set priority to "Low" and explain why in reasoning."""

        try:
            text = await self._generate_with_retry(prompt, max_attempts=3)
            if text:
                result = self._parse_json(text)
                severity = result.get("severity", "Major")
                impact = result.get("impact", "Moderate / Limited")
                priority = result.get("priority", "Medium")

                if severity not in ("Critical", "Major", "Minor"):
                    severity = "Major"
                if impact not in (
                    "Extensive / Widespread", "Significant / Large", "Moderate / Limited", "Minor / Localized",
                ):
                    impact = "Moderate / Limited"
                if priority not in ("Highest", "High", "Medium", "Low", "Lowest"):
                    priority = "Medium"

                return {
                    "is_real_bug": bool(result.get("is_real_bug", False)),
                    "severity": severity, "impact": impact, "priority": priority,
                    "reasoning": str(result.get("reasoning", "")),
                }
        except Exception as e:
            return {
                "is_real_bug": False, "severity": "Major", "impact": "Moderate / Limited",
                "priority": "Medium", "reasoning": f"Assessment failed: {e}",
            }

        return {
            "is_real_bug": False, "severity": "Major", "impact": "Moderate / Limited",
            "priority": "Medium", "reasoning": "Assessment failed",
        }


__all__ = ["AnthropicClient"]
