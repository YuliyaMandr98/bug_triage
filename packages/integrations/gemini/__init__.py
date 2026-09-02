"""Google Gemini integration client using the google-genai SDK"""

import asyncio
import json
import textwrap
import time
from typing import Any, Optional

from google import genai

from packages.common import IntegrationConnectionStatus, IntegrationType, get_logger
from packages.integrations import IntegrationClient

logger = get_logger(__name__)

# Rate limiting: 10 second delay between all Gemini API requests
_RATE_LIMIT_SECONDS = 10
_last_request_time: float = 0.0
_rate_limit_lock = asyncio.Lock()


class GeminiClient(IntegrationClient):
    """Google Gemini API client for test case generation"""

    provider_type = IntegrationType.GEMINI

    _COVERAGE_PROMPT = textwrap.dedent(
        """\
                Ты — опытный QA-инженер. Выполни анализ тестового покрытия User Story
                и сгенерируй страницу для Confluence.

                ВСЕ текстовые элементы страницы должны быть на РУССКОМ языке.
                Используй Confluence Storage Format — XHTML-подобная разметка.

                РАЗРЕШЁННЫЕ ТЕГИ (только они): <h2>, <p>, <ul>, <li>, <table>, <tr>, <th>, <td>, <strong>, <br/>.
                ЗАПРЕЩЕНО:
                - Любые HTML-атрибуты на тегах (НЕ добавляй class, style, id, data-* и пр.).
                - Вложенные списки (НЕ помещай <ul> или <ol> внутрь <li>).
                - Вложенные таблицы (НЕ помещай <table> внутрь <td> или <th>).
                - Теги <ac:*>, <ri:*> или любые другие нестандартные XML-теги.
                - Любые теги, не перечисленные выше (<div>, <span>, <em>, <b>, <code>, <pre> и т.д.).

                ─── User Story ──────────────────────────────────────────────────────────

                Заголовок: {us_title}

                Описание:
                {us_content}

                Официальный список критериев приёмки (источник ID AC):
                {ac_catalog}

                {api_doc_section}
                ─── API-тест-кейсы ({api_tc_count} шт.) ─────────────────────────────────
                {api_tc_text}

                ─── UI-тест-кейсы ({ui_tc_count} шт.) ──────────────────────────────────
                {ui_tc_text}

                ─── Задача ──────────────────────────────────────────────────────────────

                Сформируй страницу со следующими разделами (<h2> для заголовков):

                Покрытие критериев приёмки
                     - Извлеки все критерии приёмки (acceptance criteria) из описания
                         User Story.
                     - Для каждого критерия определи, какие тест-кейсы его покрывают
                         (ориентируйся на названия и шаги тест-кейсов).
                     Таблица: Критерий приёмки | Покрывающие тест-кейсы | Статус
                     - «Покрывающие тест-кейсы»: перечисли названия через <br/>.
                         Если тест-кейс не найден — оставь ячейку пустой.
                     - «Статус»: ✅ Покрыт / ⚠️ Частично / ❌ Не покрыт

                Пробелы в покрытии
                     Маркированный список критериев приёмки, которые не покрыты или
                     покрыты лишь частично, с пояснением, чего не хватает.
                     Если пробелов нет — написать «Все критерии приёмки покрыты тест-кейсами».

                Итоговая сводка
                     Таблица «Метрика | Значение» со строками:
                     Всего критериев приёмки | Покрыто полностью | Покрыто частично |
                     Не покрыто | API тест-кейсов | UI тест-кейсов

                ─── Требования ──────────────────────────────────────────────────────────

                - Не включай заголовок страницы (он задаётся в Confluence отдельно).
                - Не оборачивай ответ в ```html … ``` — только чистая разметка.
                - Технические идентификаторы (HTTP-методы, endpoint-пути, коды ошибок)
                    можно оставлять на латинице.
                                - Используй AC-ID ТОЛЬКО из официального списка выше.
                                - Не переноси замечание одного AC в другой AC-ID.
                                - Если выводишь AC-ID, он обязан соответствовать описанию именно этого AC.
                                - Считай AC «Покрыт» только если в переданных тест-кейсах есть ЯВНОЕ
                                        доказательство в названии/шаге/ожидании.
                                - Если доказательство косвенное или неоднозначное — ставь «⚠️ Частично».
            """
        )

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "gemini-2.0-flash")
        self._client: Optional[genai.Client] = (
            genai.Client(api_key=self.api_key) if self.api_key else None
        )

    async def _generate(self, prompt: str) -> Optional[str]:
        """Generate content using the google-genai SDK asynchronously with rate limiting."""
        global _last_request_time
        
        if not self._client:
            return None
        
        # Enforce 10-second rate limit between all requests
        async with _rate_limit_lock:
            elapsed = time.time() - _last_request_time
            if elapsed < _RATE_LIMIT_SECONDS:
                sleep_time = _RATE_LIMIT_SECONDS - elapsed
                logger.debug(f"Rate limiting: sleeping {sleep_time:.2f}s before next Gemini request")
                await asyncio.sleep(sleep_time)
            _last_request_time = time.time()
        
        response = await self._client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        return response.text

    async def _generate_with_retry(
        self,
        prompt: str,
        *,
        max_attempts: int = 5,
        base_backoff_seconds: int = 10,
    ) -> Optional[str]:
        """Generate content with bounded retries for transient provider failures."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await self._generate(prompt)
            except Exception as e:
                last_exc = e
                msg = str(e)
                retryable = any(token in msg for token in ["429", "500", "502", "503", "504", "UNAVAILABLE"])
                if not retryable or attempt == max_attempts:
                    break
                wait_s = max(_RATE_LIMIT_SECONDS, base_backoff_seconds * attempt)
                logger.warning(
                    f"Gemini transient error on attempt {attempt}/{max_attempts}; retrying in {wait_s}s: {msg}"
                )
                await asyncio.sleep(wait_s)

        if last_exc:
            raise last_exc
        return None

    async def test_connection(self) -> tuple[bool, Optional[str]]:
        """Test connection to Gemini."""
        if not self.api_key:
            return False, "API key not configured"
        try:
            text = await self._generate("Say 'OK' if you can read this.")
            if text:
                return True, None
            return False, "Empty response from Gemini"
        except Exception as e:
            return False, str(e)

    async def get_health_status(self) -> IntegrationConnectionStatus:
        """Get current health status."""
        if not self.api_key:
            return IntegrationConnectionStatus.UNCONFIGURED
        success, _ = await self.test_connection()
        return (
            IntegrationConnectionStatus.HEALTHY
            if success
            else IntegrationConnectionStatus.UNHEALTHY
        )

    def _parse_json(self, text: str) -> Any:
        """Extract and parse JSON that may be wrapped in markdown code fences."""
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text.strip())

    async def generate_test_cases(
        self, spec: str, test_type: str = "api"
    ) -> list[dict[str, Any]]:
        """Generate multiple test cases from a specification"""
        if not self.api_key:
            logger.error("Gemini API key not configured")
            return []

        prompt = f"""Generate {test_type.upper()} test cases for the following specification:

{spec}

Return a JSON array of test case objects with these fields:
- id: unique identifier (e.g., TC_001)
- name: test case name
- description: what is being tested
- preconditions: setup required
- steps: list of action steps
- expected_result: expected outcome
- priority: HIGH, MEDIUM, or LOW
- tags: array of relevant tags

Return ONLY valid JSON array, no markdown or code blocks."""

        try:
            text = await self._generate(prompt)
            if text:
                try:
                    result = self._parse_json(text)
                    return result if isinstance(result, list) else [result]
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse Gemini response as JSON: {text}")
            return []
        except Exception as e:
            logger.error(f"Error generating test cases: {str(e)}")
            return []

    async def generate_test_case(
        self, user_story: str, test_type: str = "ui"
    ) -> dict[str, Any]:
        """Generate a single test case from a user story"""
        if not self.api_key:
            logger.error("Gemini API key not configured")
            return {}

        prompt = f"""Create a single {test_type.upper()} test case for this user story:

{user_story}

Return a JSON object with:
- id: unique identifier
- name: test case name
- description: detailed description
- preconditions: list of prerequisites
- steps: ordered list of test steps with expected results for each
- expected_result: final expected outcome
- priority: HIGH, MEDIUM, or LOW
- tags: array of tags

Return ONLY valid JSON, no markdown."""

        try:
            text = await self._generate(prompt)
            if text:
                try:
                    return self._parse_json(text)
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse Gemini response: {text}")
            return {}
        except Exception as e:
            logger.error(f"Error generating test case: {str(e)}")
            return {}

    async def analyze_coverage(
        self, requirements: list[str], tests: list[str]
    ) -> dict[str, Any]:
        """Analyze test coverage vs requirements"""
        if not self.api_key:
            logger.error("Gemini API key not configured")
            return {}

        prompt = f"""Analyze test coverage for these requirements:

REQUIREMENTS:
{chr(10).join(f"- {r}" for r in requirements)}

TESTS:
{chr(10).join(f"- {t}" for t in tests)}

Provide a JSON analysis with:
- total_requirements: number of requirements
- covered_count: how many are covered
- coverage_percentage: percent covered
- gaps: list of uncovered requirements
- recommendations: list of suggested improvements

Return ONLY valid JSON."""

        try:
            text = await self._generate(prompt)
            if text:
                try:
                    return self._parse_json(text)
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse coverage analysis: {text}")
            return {}
        except Exception as e:
            logger.error(f"Error analyzing coverage: {str(e)}")
            return {}

    async def choose_best_match(self, query_text: str, candidates: list[str]) -> dict[str, Any]:
        """Choose the best matching title from a candidate list."""
        if not candidates:
            return {"title": None, "reason": "no_candidates"}
        if not self.api_key:
            return {"title": candidates[0], "reason": "no_api_key"}

        prompt = f"""Given the user story text, select the single best matching API document title.
Only match if the API document is clearly relevant to the user story.
If none of the candidates is a good match, return null for title.

USER STORY:
{query_text}

CANDIDATE TITLES:
{json.dumps(candidates, ensure_ascii=True)}

Return ONLY valid JSON object with keys:
- title: one exact title from the candidate list, or null if no candidate is relevant
- reason: short reason
"""
        try:
            text = await self._generate_with_retry(
                prompt,
                max_attempts=5,
                base_backoff_seconds=_RATE_LIMIT_SECONDS,
            )
            if text:
                parsed = self._parse_json(text)
                title = parsed.get("title")
                if title in candidates:
                    return {"title": title, "reason": parsed.get("reason", "")}
                # title is null or not in candidates → no match
                return {"title": None, "reason": parsed.get("reason", "no_match")}
        except Exception as e:
            logger.warning(f"Best match prompt failed: {str(e)}")

        return {"title": None, "reason": "fallback_no_match"}

    async def generate_confluence_coverage_page(self, payload: dict[str, Any]) -> str:
        """Generate Confluence storage HTML for coverage page content."""
        if not self.api_key:
            return (
                f"<h1>{payload.get('tc_title', 'Coverage Page')}</h1>"
                f"<p><strong>User Story:</strong> {payload.get('us_code', '')}</p>"
                f"<p>Gemini API key not configured; generated fallback content.</p>"
            )

        def _tests_to_text(items: list[Any]) -> str:
            if not items:
                return "Нет тест-кейсов."
            lines: list[str] = []
            for idx, item in enumerate(items, 1):
                if isinstance(item, dict):
                    title = str(item.get("title") or item.get("name") or f"Тест-кейс {idx}")
                    lines.append(f"{idx}. {title}")
                    for step in item.get("steps", []) or []:
                        action = str(step.get("action") or "").strip()
                        expected = str(step.get("expected") or "").strip()
                        if action:
                            lines.append(f"   Действие: {action}")
                        if expected:
                            lines.append(f"   Ожидание: {expected}")
                else:
                    lines.append(f"{idx}. {str(item)}")
            return "\n".join(lines)

        ac_items = payload.get("acceptance_criteria") or []
        if ac_items:
            if isinstance(ac_items[0], dict):
                ac_catalog = "\n".join(
                    f"- {str(item.get('id') or f'AC-{idx:02d}')}: {str(item.get('text') or '')}".rstrip()
                    for idx, item in enumerate(ac_items, 1)
                )
            else:
                ac_catalog = "\n".join(f"- AC-{idx:02d}: {str(item)}" for idx, item in enumerate(ac_items, 1))
        else:
            ac_catalog = "Нет явного списка AC (не удалось распарсить)."

        api_doc_title = str(payload.get("api_doc_title") or "")
        api_doc_content = str(payload.get("api_doc_content") or "")
        api_doc_section = ""
        if api_doc_title and api_doc_content:
            api_doc_section = (
                f"─── API-документация: «{api_doc_title}» ──────────────────────────────\n"
                f"{api_doc_content[:12000]}\n\n"
            )

        prompt = self._COVERAGE_PROMPT.format(
            us_title=str(payload.get("us_title") or payload.get("tc_title") or payload.get("us_code") or ""),
            us_content=str(payload.get("user_story_text") or "")[:12000],
            ac_catalog=ac_catalog,
            api_doc_section=api_doc_section,
            api_tc_count=len(payload.get("api_tests") or []),
            api_tc_text=_tests_to_text(payload.get("api_tests") or []),
            ui_tc_count=len(payload.get("ui_tests") or []),
            ui_tc_text=_tests_to_text(payload.get("ui_tests") or []),
        )

        text = await self._generate_with_retry(
            prompt,
            max_attempts=5,
            base_backoff_seconds=_RATE_LIMIT_SECONDS,
        )
        if not text:
            raise RuntimeError("Gemini returned empty response while generating Confluence HTML")
        cleaned = text.strip()
        if cleaned.startswith("```html"):
            cleaned = cleaned[len("```html"):].strip()
        elif cleaned.startswith("```"):
            cleaned = cleaned[len("```"):].strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[: -len("```")].strip()
        return cleaned

    async def assess_bug(
        self,
        us_content: str,
        bug_title: str,
        bug_description: str,
    ) -> dict[str, Any]:
        """Assess whether a bug ticket describes a real defect and determine severity/impact.

        Returns a dict with:
        - is_real_bug: bool
        - severity: "Critical" | "Major" | "Minor"
        - impact: one of the four JIRA impact values
        - reasoning: short explanation
        """
        if not self.api_key:
            return {
                "is_real_bug": False,
                "severity": "Major",
                "impact": "Moderate / Limited",
                "priority": "Medium",
                "reasoning": "Gemini API key not configured",
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
            text = await self._generate_with_retry(
                prompt, max_attempts=3, base_backoff_seconds=_RATE_LIMIT_SECONDS
            )
            if text:
                result = self._parse_json(text)
                severity = result.get("severity", "Major")
                impact = result.get("impact", "Moderate / Limited")
                priority = result.get("priority", "Medium")

                # Validate against allowed JIRA values
                if severity not in ("Critical", "Major", "Minor"):
                    severity = "Major"
                if impact not in (
                    "Extensive / Widespread",
                    "Significant / Large",
                    "Moderate / Limited",
                    "Minor / Localized",
                ):
                    impact = "Moderate / Limited"
                if priority not in ("Highest", "High", "Medium", "Low", "Lowest"):
                    priority = "Medium"

                return {
                    "is_real_bug": bool(result.get("is_real_bug", False)),
                    "severity": severity,
                    "impact": impact,
                    "priority": priority,
                    "reasoning": str(result.get("reasoning", "")),
                }
        except Exception as e:
            logger.error(f"Gemini bug assessment failed: {str(e)}")

        return {
            "is_real_bug": False,
            "severity": "Major",
            "impact": "Moderate / Limited",
            "priority": "Medium",
            "reasoning": "Assessment failed",
        }


__all__ = ["GeminiClient"]
