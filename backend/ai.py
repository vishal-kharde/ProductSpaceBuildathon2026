from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any

try:
    from google import genai
except ImportError:  # pragma: no cover - exercised only when dependency is absent
    genai = None

from pydantic import BaseModel, Field

from config import settings
from models import Comparison
from safety import sanitize_json, finite_number

log = logging.getLogger("bidlens.ai")


class ExtractedVendor(BaseModel):
    name: str
    category: str = "General Procurement"
    annual_cost: float | None = None
    setup_cost: float = 0
    variable_cost_per_unit: float = 0
    renewal_increase_pct: float | None = None
    contract_years: int | None = None
    payment_terms_days: int | None = None
    sla: str = "Not stated"
    security: str = "Not stated"
    residency: str = "Not stated"
    implementation_days: int | None = None
    unit_price: float | None = None
    shipping_fees: float | None = None
    volume_discount: str = "Not stated"
    technical_capability: list[str] = Field(default_factory=list)
    reliability: list[str] = Field(default_factory=list)
    service_support: list[str] = Field(default_factory=list)
    evidence_coverage: float = Field(default=0.0, ge=0, le=1)
    missing_info: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class RiskResult(BaseModel):
    vendor: str
    risk_score: float = Field(ge=0, le=10)
    risks: list[str] = Field(default_factory=list)


class AwardResult(BaseModel):
    recommendation: str
    rationale: str
    overall_confidence: float = Field(ge=0, le=1)
    award_score: float = Field(ge=0, le=10)
    comparison_highlights: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    requirement_checks: list[dict] = Field(default_factory=list)


EXTRACT_PROMPT = '''You are BidLens Extraction Agent. Extract ONLY facts explicitly supported by the proposal. Never invent values. Use null for unknown optional numeric fields. Never use 0 to mean unknown. NEVER output scientific notation, NaN, Infinity or -Infinity. Use ordinary decimal numbers. Add missing_info when a value is not stated. Categorize facts into financial, technical, reliability, and service/support. Evidence must include page/section when available and a short factual snippet. Return ONLY valid JSON matching the schema.'''
RISK_PROMPT = '''You are BidLens Risk Agent. Review proposal facts against buyer requirements. Identify commercial, pricing, renewal, lock-in, SLA, security, implementation, reliability and omission risks. Treat missing information as uncertainty, not compliance. Do not invent details. Return ONLY valid JSON matching the schema.'''
AWARD_PROMPT = '''You are BidLens Award Agent. Compare vendors against buyer requirements. First identify mandatory requirements from words such as must, mandatory, required, shall, <=, at least. A mandatory failure is NO-GO and cannot be offset by price. For every requirement output PASS, FAIL, or UNKNOWN with evidence. Then recommend the strongest qualified vendor; if no vendor passes all mandatory gates, recommend HOLD / NO QUALIFIED VENDOR. Use Fit 45%, Commercial 30%, Terms/SLA 15%, Risk 10% among qualified vendors. Return ONLY valid JSON matching the schema.'''


class _GeminiKeyPool:
    """Thread-safe pool: one API key can be in-flight on only one worker at a time."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._available: list[str] = []
        self._in_use: set[str] = set()
        self.refresh()

    def refresh(self) -> None:
        keys = settings.api_keys()
        with self._condition:
            self._available = [k for k in keys if k not in self._in_use]
            self._condition.notify_all()
        log.info("Gemini key pool initialized configured_keys=%s", len(keys))

    @property
    def size(self) -> int:
        return len(settings.api_keys())

    @contextmanager
    def acquire(self):
        keys = settings.api_keys()
        if not keys:
            yield None
            return

        deadline = time.monotonic() + max(0.1, settings.ai_key_wait_seconds)
        with self._condition:
            while True:
                # Reconcile pool with current env settings in case tests or a reload replaced them.
                current = set(keys)
                self._available = [k for k in self._available if k in current and k not in self._in_use]
                if not self._available:
                    self._available.extend(k for k in keys if k not in self._in_use and k not in self._available)

                if self._available:
                    key = self._available.pop(0)
                    self._in_use.add(key)
                    break

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for an available Gemini API key")
                self._condition.wait(timeout=min(remaining, 0.5))

        try:
            log.debug("Gemini key acquired slot remaining=%s", self.size - len(self._in_use))
            yield key
        finally:
            with self._condition:
                self._in_use.discard(key)
                if key in settings.api_keys() and key not in self._available:
                    self._available.append(key)
                self._condition.notify()
                log.debug("Gemini key released in_flight=%s", len(self._in_use))


_KEY_POOL = _GeminiKeyPool()
_CLIENTS: dict[str, Any] = {}
_CLIENT_LOCK = threading.Lock()


def _get_client(api_key: str | None):
    if not genai:
        log.error("google-genai package is not installed")
        return None
    if not api_key:
        return None
    with _CLIENT_LOCK:
        client = _CLIENTS.get(api_key)
        if client is None:
            client = genai.Client(api_key=api_key)
            _CLIENTS[api_key] = client
            log.info("Created Gemini client for key_slot=%s", settings.api_keys().index(api_key) + 1 if api_key in settings.api_keys() else "?")
        return client


def client():
    """Backward-compatible single-client accessor using a currently available key."""
    try:
        with _KEY_POOL.acquire() as api_key:
            return _get_client(api_key)
    except TimeoutError:
        log.warning("No Gemini key available for client()")
        return None


def sanitize_numbers(obj):
    if isinstance(obj, dict):
        return {k: sanitize_numbers(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_numbers(v) for v in obj]
    if isinstance(obj, float):
        if not math.isfinite(obj) or abs(obj) < 1e-12:
            return 0
    return obj


def parse_json_output(raw: str):
    if not isinstance(raw, str):
        raise ValueError("Gemini returned no textual JSON output")
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = re.sub(r"(?i)(?<![A-Za-z])(?:NaN|Infinity|-Infinity)(?![A-Za-z])", "null", cleaned)

    def exponent_fix(m):
        try:
            value = float(m.group(0))
            return "0" if not math.isfinite(value) or abs(value) < 1e-12 else m.group(0)
        except ValueError:
            return "0"

    cleaned = re.sub(r"-?(?:\d+(?:\.\d*)?|\.\d+)[eE][+-]?\d+", exponent_fix, cleaned)
    payload = json.loads(cleaned)
    return sanitize_numbers(payload)


def _normalize_evidence_items(items: Any, coverage: Any) -> list[dict[str, Any]]:
    """Normalize common Gemini evidence variants to the API's stable Evidence shape."""
    if not isinstance(items, list):
        return []

    normalized: list[dict[str, Any]] = []
    default_confidence = min(1.0, max(0.0, finite_number(coverage, 0.5)))
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue

        category = item.get("category") or item.get("type") or "proposal"
        field = item.get("field") or item.get("fact") or item.get("label") or category
        value = item.get("value")
        if value is None:
            value = item.get("snippet")
        if value is None:
            value = item.get("claim")
        if value is None:
            value = item.get("detail")
        if value is None:
            value = "Not stated"

        source = item.get("source") or item.get("page") or item.get("section") or "Proposal"
        if isinstance(source, (int, float)):
            source = f"Page {source}"
        elif isinstance(source, dict):
            page = source.get("page")
            section = source.get("section")
            source = " / ".join(str(x) for x in (page, section) if x) or "Proposal"
        confidence = finite_number(item.get("confidence", item.get("evidence_confidence", default_confidence)), default_confidence)
        confidence = min(1.0, max(0.0, confidence))

        normalized.append({
            "field": str(field),
            "value": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
            "source": str(source),
            "confidence": confidence,
        })
        log.debug("Normalized evidence index=%s field=%s source=%s", idx, field, source)
    return normalized


def _clean_ai_string(value: Any, *, max_len: int = 500) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    # Guard against generation loops where the same phrase is repeated hundreds of times.
    words = text.split()
    if len(words) >= 24:
        for n in (2, 3, 4, 5, 6, 8, 10):
            if len(words) >= n * 4:
                chunk = words[:n]
                repeats = 0
                i = 0
                while i + n <= len(words) and words[i:i+n] == chunk:
                    repeats += 1
                    i += n
                if repeats >= 4:
                    text = ' '.join(words[:n]) + ' [repetition removed]'
                    break
    return text[:max_len]


def _normalize_extraction_payload(payload: Any, name: str) -> Any:
    if not isinstance(payload, dict):
        return payload
    payload = dict(payload)
    payload.setdefault("name", name)
    payload["evidence"] = _normalize_evidence_items(payload.get("evidence"), payload.get("evidence_coverage", 0.5))
    for key in ("category", "sla", "security", "volume_discount"):
        if key in payload and payload[key] is not None:
            payload[key] = _clean_ai_string(payload[key])
    for key in ("technical_capability", "reliability", "service_support", "missing_info"):
        if isinstance(payload.get(key), list):
            payload[key] = [_clean_ai_string(x, max_len=240) for x in payload[key] if str(x or '').strip()]
    if not payload.get("name"):
        payload["name"] = name
    return payload


def _is_retryable_api_error(exc: Exception) -> bool:
    text = str(exc).lower()
    retry_markers = (
        "429", "rate limit", "quota", "resource exhausted", "too many requests",
        "503", "temporarily unavailable", "deadline exceeded", "timeout", "timed out",
        "connection reset", "internal server error",
    )
    return any(marker in text for marker in retry_markers)


def structured_call(prompt: str, user: str, model_cls, retries: int | None = None):
    models = settings.model_priority()
    if not models:
        log.error("No Gemini models configured")
        return None
    if not settings.api_keys():
        log.warning("GEMINI_API_KEY is empty; deterministic fallbacks will be used")
        return None
    if not genai:
        log.error("google-genai package is not installed; deterministic fallbacks will be used")
        return None

    retry_count = settings.ai_retries if retries is None else max(0, retries)
    total_attempts = retry_count + 1
    last: Exception | None = None

    for attempt in range(total_attempts):
        model = models[min(attempt, len(models) - 1)]
        try:
            # The key is reserved for the full API request only. Parallel workers therefore never
            # execute a Gemini request concurrently with the same key.
            with _KEY_POOL.acquire() as api_key:
                if not api_key:
                    return None
                c = _get_client(api_key)
                key_slot = settings.api_keys().index(api_key) + 1 if api_key in settings.api_keys() else "?"
                log.info(
                    "Gemini attempt=%s/%s model=%s schema=%s key_slot=%s",
                    attempt + 1, total_attempts, model, model_cls.__name__, key_slot,
                )
                extra = (
                    "\nThis is a retry. Return ONLY valid JSON matching the schema. "
                    "Never use scientific notation, NaN, Infinity or -Infinity. "
                    "Use null for unknown optional numeric fields and ordinary decimal numbers for known values."
                ) if attempt else ""
                interaction = c.interactions.create(
                    model=model,
                    system_instruction=prompt + extra,
                    input=user,
                    response_format={"type": "text", "mime_type": "application/json", "schema": model_cls.model_json_schema()},
                    generation_config={
                        "max_output_tokens": settings.max_output_tokens,
                        "top_p": settings.top_p,
                        "thinking_level": settings.thinking_level,
                    },
                )
                raw = getattr(interaction, "output_text", None)
                log.debug("Gemini raw output length=%s model=%s key_slot=%s", len(raw or ""), model, key_slot)
                payload = parse_json_output(raw or "")
                if model_cls is ExtractedVendor:
                    payload = _normalize_extraction_payload(payload, user.split("\n", 1)[0].replace("Vendor label:", "").strip())
                result = model_cls.model_validate(payload)
                log.info("Gemini success attempt=%s model=%s key_slot=%s", attempt + 1, model, key_slot)
                return result
        except TimeoutError as exc:
            last = exc
            log.warning("Gemini key pool timeout attempt=%s/%s error=%s", attempt + 1, total_attempts, exc)
        except Exception as exc:
            last = exc
            retryable = _is_retryable_api_error(exc)
            log.warning(
                "Gemini failure attempt=%s/%s model=%s retryable=%s error=%s",
                attempt + 1, total_attempts, model, retryable, exc,
                exc_info=log.isEnabledFor(logging.DEBUG),
            )
            # Validation/JSON errors can also be retried because a different model/key may return
            # a cleaner structured payload. API quota errors are especially useful with key rotation.
            if not retryable and attempt == total_attempts - 1:
                break

        if attempt < total_attempts - 1:
            delay = max(0.05, settings.ai_retry_delay_seconds) * (attempt + 1)
            if last is not None and _is_retryable_api_error(last):
                delay = max(delay, 1.0 * (attempt + 1))
            time.sleep(delay)

    log.error("Gemini exhausted attempts=%s models=%s last_error=%r", total_attempts, models, last)
    return None



def _first_number(pattern: str, text: str, flags=re.I):
    m = re.search(pattern, text, flags)
    return finite_number(m.group(1).replace(',', '')) if m else None


def _confirmation_evidence(param: str, vendor: str, details: str | None = None) -> str:
    detail = f" Evidence found: {details}." if details and details != 'Not stated' else ''
    actions = {
        'implementation': 'Ask the vendor to state the committed implementation duration in calendar days and identify any dependencies.',
        'payment': 'Ask the vendor to confirm payment terms (for example Net 60) in the commercial schedule.',
        'contract': 'Ask the vendor to confirm the committed contract term and renewal mechanics.',
        'api': 'Ask the vendor to confirm API availability, authentication method, limits, and any overage charges.',
        'sandbox': 'Ask the vendor to confirm whether a sandbox/non-production environment is included.',
        'saml': 'Ask the vendor to explicitly confirm SAML support if SAML is required rather than generic SSO.',
        'residency': 'Ask the vendor to confirm supported production data-residency regions and the contract commitment.',
        'support': 'Ask the vendor to confirm 24x7 P1 support and the contractual response SLA.',
    }
    return f"Needs vendor confirmation. {actions.get(param, 'Ask the vendor to provide explicit contractual evidence for this requirement.')}" + detail


def _enrich_extracted_vendor(v: ExtractedVendor, text: str) -> ExtractedVendor:
    """Fill high-value procurement fields from explicit proposal text when the LLM omitted them."""
    data = v.model_dump()
    money = _first_number(r'\$\s*([0-9][0-9,]*(?:\.\d+)?)\s*/\s*user\s*/\s*year', text)
    setup = _first_number(r'\$\s*([0-9][0-9,]*(?:\.\d+)?)\s+implementation', text)
    renewal = _first_number(r'(?:renewal|annual\s+(?:increase|escalation))[\s\S]{0,120}?(?:maximum|max|cap|increase|escalation)?[\s\S]{0,80}?(\d+(?:\.\d+)?)\s*%', text)
    if renewal is None:
        renewal = _first_number(r'(?:renewal|annual\s+(?:increase|escalation))[^\n]*?(\d+(?:\.\d+)?)\s*%', text)
    if renewal is None:
        renewal = _first_number(r'(?:maximum|max|cap)\s+(\d+(?:\.\d+)?)\s*%', text)
    sla = re.search(r'(?:SLA|availability|uptime)\s*[:\-]?\s*(?:\n\s*)?(\d+(?:\.\d+)?)%', text, re.I)
    if not sla:
        sla = re.search(r'(\d+(?:\.\d+)?)%\s*;?\s*(?:P1|availability|uptime|SLA)', text, re.I)
    implementation = _first_number(r'(?:implementation|rollout)\s*[:\-]?[^\n]*?(?:within|in|under|<=)?\s*(\d+)\s*days', text)
    payment = _first_number(r'(?:payment(?: terms?)?|terms?|net)\s*[:\-]?\s*(?:net\s*)?(\d+)\s*(?:days?)?', text)
    contract = _first_number(r'(?:contract|term)\s*[:\-]?[^\n]*?(\d+)\s*[- ]?years?', text)
    if money is not None: data['unit_price'] = money
    if money is not None: data['annual_cost'] = money
    if setup is not None: data['setup_cost'] = setup
    if renewal is not None: data['renewal_increase_pct'] = renewal
    # Explicit proposal facts take precedence over potentially inconsistent LLM extraction.
    if implementation is not None: data['implementation_days'] = int(implementation)
    if payment is not None: data['payment_terms_days'] = int(payment)
    if contract is not None: data['contract_years'] = int(contract)
    if sla:
        data['sla'] = sla.group(1) + '%'
    tl=text.lower()
    def section(heading: str, next_headings: tuple[str, ...]) -> str:
        stops='|'.join(re.escape(x) for x in next_headings)
        m=re.search(r'(?is)\b'+re.escape(heading)+r'\b\s*[:\n]+(.*?)(?=\n\s*(?:'+stops+r')\b|\Z)', text)
        return ' '.join(x.strip() for x in m.group(1).splitlines()).strip() if m else ''

    security_src=section('Security', ('Residency','Exit','AI','Clause','Price','Volume','Renewal','SLA'))
    residency_src=section('Residency', ('Exit','AI','Clause','Price','Volume','Renewal','SLA'))
    exit_src=section('Exit', ('AI','Clause','Price','Volume','Renewal','SLA'))
    ai_src=section('AI', ('Clause','Price','Volume','Renewal','SLA','Security','Residency','Exit'))
    sla_src=section('SLA', ('Security','Residency','Exit','AI','Clause','Price','Volume','Renewal'))

    # Replace LLM-only capability hallucinations with only facts supported by the proposal text.
    explicit_tech=[]
    if re.search(r'\b(?:REST\s+)?API\b', text, re.I): explicit_tech.append('API')
    if re.search(r'\b(?:export|JSON/CSV)\b', exit_src or text, re.I): explicit_tech.append('Data export supported')
    if re.search(r'\bsandbox\b', text, re.I): explicit_tech.append('Sandbox included')
    if re.search(r'\bOAuth(?:\s+2\.0)?\b', text, re.I): explicit_tech.append('OAuth 2.0 authentication')
    if re.search(r'\bAI sales insights\b', ai_src or text, re.I): explicit_tech.append('AI sales insights included')
    if re.search(r'\bAI sales assistant\b', ai_src or text, re.I): explicit_tech.append('AI sales assistant included')
    data['technical_capability']=explicit_tech

    explicit_support=[]
    if re.search(r'24\s*[x/]\s*7|24x7', sla_src or text, re.I): explicit_support.append('24x7 support')
    m_p1=re.search(r'(P1\s+\d+\s*min[^\n]*)', sla_src or text, re.I)
    if m_p1: explicit_support.append(m_p1.group(1).strip())
    if re.search(r'migration support', exit_src or text, re.I): explicit_support.append('Migration support stated')
    if 'sandbox' in tl: explicit_support.append('Sandbox included')
    data['service_support']=explicit_support

    # Ground security evidence exactly: SSO alone is not promoted to SAML.
    security_facts=[]
    for label, pattern in [
        ('ISO 27001', r'ISO\s*27001'),
        ('SOC 2', r'SOC\s*2'),
        ('SSO', r'\bSSO\b'),
        ('SAML', r'\bSAML\b'),
        ('SCIM', r'\bSCIM\b'),
    ]:
        if re.search(pattern, security_src or text, re.I): security_facts.append(label)
    if re.search(r'\bindia\b', residency_src or text, re.I): security_facts.append('India data residency')
    if re.search(r'\bEU\b|Europe', residency_src or text, re.I): security_facts.append('EU data residency')
    data['security']='; '.join(dict.fromkeys(security_facts)) or 'Not stated'
    if residency_src:
        residency_status=''.join([])
        data['residency']=residency_src

    if sla_src: data['sla']=re.search(r'(\d+(?:\.\d+)?)%', sla_src).group(1)+'%' if re.search(r'(\d+(?:\.\d+)?)%', sla_src) else (data.get('sla') or 'Not stated')
    # Prefer concise, proposal-grounded pricing language over hallucinated/repeated LLM text.
    flat_rate = re.search(r'flat\s+\$\s*[0-9][0-9,]*(?:\.[0-9]+)?/user/year', text, re.I)
    tiered = re.search(r'(?:1,?000[^\n]{0,100}?\$\s*[0-9][0-9,]+[^\n]*|above\s+1,?000[^\n]*\$\s*[0-9][0-9,]+[^\n]*)', text, re.I)
    if flat_rate:
        data['volume_discount'] = _clean_ai_string(flat_rate.group(0))
    elif tiered:
        data['volume_discount'] = _clean_ai_string(tiered.group(0), max_len=240)
    elif data.get('volume_discount'):
        data['volume_discount'] = _clean_ai_string(data['volume_discount'], max_len=240)
    evidence = list(data.get('evidence') or [])
    fact_pairs = [
        ('Unit price', data.get('unit_price'), r'\$\s*[0-9][0-9,]*(?:\.[0-9]+)?\s*/\s*user\s*/\s*year'),
        ('Setup cost', data.get('setup_cost'), r'\$\s*[0-9][0-9,]*(?:\.[0-9]+)?\s+implementation'),
        ('Contract term', data.get('contract_years'), r'(?:contract|term)\s*[:\-]?[^\n]{0,80}\b[0-9]+\s*[- ]?years?'),
        ('Implementation', data.get('implementation_days'), r'(?:implementation|rollout)\s*[:\-]?[^\n]*\b[0-9]+\s*days'),
        ('Payment terms', data.get('payment_terms_days'), r'(?:payment(?: terms?)?|net)\s*[:\-]?[^\n]*\b[0-9]+\s*(?:days?)?'),
        ('Renewal', data.get('renewal_increase_pct'), r'(?:renewal|escalation)[^\n]{0,100}\d+(?:\.\d+)?\s*%'),
        ('SLA', data.get('sla'), r'(?:sla|availability|uptime)[^\n]*\d+(?:\.\d+)?\s*%'),
        ('Security', data.get('security'), r'(?i)(?:sso|saml|scim|iso 27001|soc 2)'),
        ('Technical capability', data.get('technical_capability'), r'(?i)(?:api|export|sandbox|oauth|residency)'),
        ('Support', data.get('service_support'), r'(?i)(?:support|p1|24x7|24/7)'),
    ]
    for field, value, pattern in fact_pairs:
        if value not in (None, '', [], 'Not stated', 0) and re.search(pattern, text):
            if not any(e.get('field') == field for e in evidence):
                evidence.append({'field': field, 'value': str(value), 'source': f'Vendor Proposal — {v.name}', 'confidence': 0.98})
    data['evidence'] = evidence
    grounded_fields = sum(1 for _, value, _ in fact_pairs if value not in (None, '', [], 'Not stated', 0))
    data['evidence_coverage'] = round(min(1.0, grounded_fields / len(fact_pairs)), 2)
    return ExtractedVendor.model_validate(data)


def fallback_extract(name, text):
    nums = []
    for x in re.findall(r'(?:USD|US\$|\$|INR|₹)\s*([0-9][0-9,]*(?:\.[0-9]+)?)', text, re.I):
        nums.append(finite_number(x.replace(',', '')))
    return ExtractedVendor(
        name=name,
        annual_cost=nums[0] if nums else 0,
        evidence_coverage=.25,
        missing_info=['AI extraction unavailable: manual review needed'],
        evidence=[{
            'field': 'annual_cost',
            'value': str(nums[0]) if nums else 'Not stated',
            'source': 'Deterministic fallback',
            'confidence': 0.25,
        }] if nums else [],
    )


def extract_vendor(name, text):
    result = structured_call(EXTRACT_PROMPT, f'Vendor label: {name}\nProposal:\n{text[:50000]}', ExtractedVendor)
    if result:
        return _enrich_extracted_vendor(result, text)
    return fallback_extract(name, text)


def risk_vendor(v, requirements):
    result = structured_call(
        RISK_PROMPT,
        f'Requirements:\n{requirements}\n\nVendor facts:\n{v.model_dump_json(indent=2)}',
        RiskResult,
    )
    return result or RiskResult(vendor=v.name, risk_score=min(10, 2 + len(v.missing_info) * 1.2), risks=v.missing_info[:6])


def award(vendors, risks, requirements):
    user = (
        'Requirements:\n' + requirements + '\n\nVendors:\n' +
        json.dumps(sanitize_json([v.model_dump() for v in vendors]), indent=2, allow_nan=False) +
        '\n\nRisks:\n' +
        json.dumps(sanitize_json([r.model_dump() for r in risks]), indent=2, allow_nan=False)
    )
    result = structured_call(AWARD_PROMPT, user, AwardResult)
    return result or AwardResult(
        recommendation='HOLD / NO QUALIFIED VENDOR',
        rationale='AI award analysis unavailable. Manual procurement review required.',
        overall_confidence=.35,
        award_score=0,
    )


def deterministic_requirement_checks(v, requirements):
    text = requirements.lower(); checks = []

    def add(req, status, evidence, mandatory=True, action=None):
        # UNKNOWN is intentionally presented as NEEDS CONFIRMATION to the UI.
        display_status = 'NEEDS CONFIRMATION' if status == 'UNKNOWN' else status
        checks.append({'requirement': req, 'status': status, 'display_status': display_status,
                       'mandatory': mandatory, 'evidence': evidence,
                       'action': action or ('No action required.' if status != 'UNKNOWN' else 'Vendor confirmation required.'),
                       'confidence': .92 if status != 'UNKNOWN' else .55})

    combined = ' '.join([
        v.security or '', v.residency or '', v.sla or '', ' '.join(v.technical_capability or []),
        ' '.join(v.reliability or []), ' '.join(v.service_support or [])
    ]).lower()

    mandatory_text = re.search(r'mandatory\s*:\s*(.+?)(?:\n|$)', requirements, re.I)
    mandatory_clause = mandatory_text.group(1).lower() if mandatory_text else ''
    mandatory_words = ('must', 'mandatory', 'required', 'shall')
    def is_mand(req_terms):
        if any(t in mandatory_clause for t in req_terms): return True
        for line in requirements.splitlines():
            low=line.lower()
            if any(t in low for t in req_terms) and any(w in low for w in mandatory_words): return True
        return any(re.search(r'\bmust\b.{0,140}'+re.escape(t), requirements, re.I) for t in req_terms)

    if 'sso' in text or 'saml' in text:
        ok = bool(re.search(r'\bsaml\b', combined)) if ('saml' in requirements.lower() and 'sso' in requirements.lower()) else bool(re.search(r'\bsso\b', combined) or re.search(r'\bsaml\b', combined))
        evidence = 'Security: ' + (v.security or 'Not stated')
        add('SSO/SAML', 'PASS' if ok else 'UNKNOWN', evidence, is_mand(['sso','saml']), _confirmation_evidence('saml', v.name, evidence) if not ok else None)

    if 'scim' in text:
        sec = combined
        status = 'PASS' if re.search(r'\bscim\b', sec) else 'UNKNOWN'
        evidence = 'Security/technical: ' + (v.security or 'Not stated')
        # Paid add-on is not a clean mandatory PASS.
        if status == 'PASS' and 'paid add-on' in combined:
            status = 'UNKNOWN'
            evidence += ' — SCIM is described as a paid add-on.'
        add('SCIM', status, evidence, is_mand(['scim']), _confirmation_evidence('api', v.name, evidence) if status == 'UNKNOWN' else None)

    if re.search(r'\bapi\b', text):
        ok = bool(re.search(r'\bapi\b', combined))
        evidence = 'Technical capability: ' + (', '.join(v.technical_capability) if v.technical_capability else 'Not stated')
        add('API', 'PASS' if ok else 'UNKNOWN', evidence, is_mand(['api']), _confirmation_evidence('api', v.name, evidence) if not ok else None)

    if 'india' in text and 'residency' in text:
        source=v.residency or v.security or 'Not stated'
        ok = bool(re.search(r'\bindia\b', source, re.I))
        roadmap = bool(re.search(r'roadmap|planned|future|no date|coming soon', source, re.I))
        status='UNKNOWN' if not ok or roadmap else 'PASS'
        evidence=source + (' — roadmap/future language detected.' if roadmap else '')
        add('India data residency', status, evidence, is_mand(['india']), _confirmation_evidence('residency', v.name, evidence) if status == 'UNKNOWN' else None)

    if ('eu' in text or 'europe' in text) and 'residency' in text:
        source=v.residency or v.security or 'Not stated'
        ok = bool(re.search(r'\bEU\b|Europe', source, re.I))
        evidence=source
        add('EU data residency', 'PASS' if ok else 'UNKNOWN', evidence, is_mand(['eu','europe']), _confirmation_evidence('residency', v.name, evidence) if not ok else None)

    if '99.9' in text:
        sla=v.sla or ''
        m=re.search(r'(\d+(?:\.\d+)?)\s*%', sla)
        status='PASS' if m and float(m.group(1)) >= 99.9 else ('FAIL' if m else 'UNKNOWN')
        add('99.9% SLA', status, sla or 'Not stated', is_mand(['99.9','sla']), _confirmation_evidence('support', v.name, sla or None) if status == 'UNKNOWN' else None)

    m = re.search(r'implementation\s+(?:within|<=|under|in)\s*(\d+)\s*days', text, re.I)
    if m:
        limit=int(m.group(1)); val=v.implementation_days
        status='UNKNOWN' if val is None else ('PASS' if 0 < val <= limit else 'FAIL')
        add(f'Implementation <= {limit} days', status, f'{val} days stated' if val is not None else 'Not stated', is_mand(['implementation']), _confirmation_evidence('implementation', v.name) if status == 'UNKNOWN' else None)

    m = re.search(r'(?:renewal|escalation)[^.\n]{0,120}(?:<=|less than|at most|maximum|max|cap)\s*(\d+(?:\.\d+)?)\s*%', text, re.I)
    if m:
        limit=float(m.group(1)); val=v.renewal_increase_pct
        status='UNKNOWN' if val is None else ('PASS' if 0 <= val <= limit else 'FAIL')
        add(f'Renewal/escalation <= {limit:g}%', status, f'{val:g}% stated' if val is not None else 'Not stated', is_mand(['escalation','renewal']), _confirmation_evidence('contract', v.name) if status == 'UNKNOWN' else None)

    if 'data export' in text or re.search(r'\bexport\b', text):
        ok='export' in combined
        evidence='Technical/exit: ' + ('; '.join(v.technical_capability) if v.technical_capability else 'Not stated')
        add('Data export', 'PASS' if ok else 'UNKNOWN', evidence, is_mand(['export']), _confirmation_evidence('contract', v.name, evidence) if not ok else None)

    horizon_m = re.search(r'horizon\s*:\s*(\d+)\s*years?', requirements, re.I)
    horizon = int(horizon_m.group(1)) if horizon_m else None
    if horizon:
        val = v.contract_years
        status = 'UNKNOWN' if val is None else ('PASS' if val >= horizon else 'FAIL')
        add(f'Contract term >= {horizon} years', status, f'{val} years stated' if val is not None else 'Not stated', True, _confirmation_evidence('contract', v.name) if status == 'UNKNOWN' else None)

    if 'sandbox' in text:
        ok = bool(re.search(r'\bsandbox\b', combined))
        add('Sandbox environment', 'PASS' if ok else 'UNKNOWN', 'Technical capability: ' + (', '.join(v.technical_capability) if v.technical_capability else 'Not stated'), False, _confirmation_evidence('sandbox', v.name) if not ok else None)

    if '24x7' in text or '24/7' in text:
        ok = bool(re.search(r'24\s*[x/]\s*7|24x7', combined) or re.search(r'24\s*[x/]\s*7|24x7', (v.sla or '').lower()))
        add('24x7 P1 support', 'PASS' if ok else 'UNKNOWN', v.sla or 'Not stated', False, _confirmation_evidence('support', v.name) if not ok else None)

    if 'ai sales insights' in text:
        ok = 'ai sales insights' in combined
        add('AI sales insights', 'PASS' if ok else 'UNKNOWN', 'Technical capability: ' + (', '.join(v.technical_capability) if v.technical_capability else 'Not stated'), False, _confirmation_evidence('support', v.name) if not ok else None)

    if 'transparent pricing' in text:
        status = 'PASS' if v.unit_price is not None or v.annual_cost not in (None,0) else 'UNKNOWN'
        add('Transparent pricing', status, f'Annual/unit cost: {v.annual_cost if v.annual_cost is not None else "Not stated"}', False, _confirmation_evidence('contract', v.name) if status == 'UNKNOWN' else None)

    if '60+ day' in text or '60 day payment' in text or 'payment terms' in text:
        val=v.payment_terms_days
        status='UNKNOWN' if val is None else ('PASS' if val >= 60 else 'FAIL')
        add('Payment terms >= 60 days', status, f'{val} days stated' if val is not None else 'Not stated', False, _confirmation_evidence('payment', v.name) if status == 'UNKNOWN' else None)

    return checks

def negotiate(vendors, requirements):
    prompt = '''You are BidLens Negotiation Copilot. Return evidence-backed negotiation levers. For each lever include vendor, lever, evidence, buyer_target, fallback, and vendor_question. Never invent leverage. Return ONLY valid JSON matching the schema.'''

    class N(BaseModel):
        negotiation: list[dict] = Field(default_factory=list)

    result = structured_call(
        prompt,
        'Requirements:\n' + requirements + '\n\nVendors:\n' + json.dumps(sanitize_json([v.model_dump() for v in vendors]), indent=2, allow_nan=False),
        N,
    )
    return result.negotiation if result else []


def _parallel_map(func, items, stage: str):
    if not items:
        return []
    worker_count = min(settings.worker_count(len(items)), max(1, _KEY_POOL.size)) if _KEY_POOL.size else settings.worker_count(len(items))
    log.info("Starting parallel stage=%s tasks=%s workers=%s configured_keys=%s", stage, len(items), worker_count, _KEY_POOL.size)
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix=f"bidlens-{stage}") as ex:
        future_items = [(item, ex.submit(func, item)) for item in items]
        results = []
        for item, future in future_items:
            try:
                results.append(future.result())
            except Exception:
                log.exception("Parallel stage failed stage=%s item=%s", stage, getattr(item, 'name', item[0] if isinstance(item, tuple) and item else '?'))
                raise
    log.info("Completed parallel stage=%s", stage)
    return results


def _requested_user_count(requirements: str) -> int:
    m = re.search(r'(\d[\d,]*)\s+users?', requirements, re.I)
    return int(m.group(1).replace(',', '')) if m else 1000


def _normalize_vendor_economics(v: ExtractedVendor, requirements: str) -> ExtractedVendor:
    """Make annual cost comparable when proposals quote a per-user annual price."""
    data=v.model_dump()
    units=_requested_user_count(requirements)
    unit=data.get('unit_price')
    annual=data.get('annual_cost')
    if unit is not None and (annual is None or annual <= unit * 1.1):
        data['annual_cost']=round(float(unit) * units, 2)
    return ExtractedVendor.model_validate(data)


def analyze(requirements, proposals):
    log.info("Starting analysis proposals=%s requirements_chars=%s key_count=%s", len(proposals), len(requirements), _KEY_POOL.size)

    # Proposal extraction is independent per file, so run it concurrently.
    vendors = _parallel_map(lambda p: extract_vendor(p[0], p[1]), proposals, "extraction")
    vendors = [_normalize_vendor_economics(v, requirements) for v in vendors]

    # Risk analysis is also independent per vendor; run these concurrently.
    risks = _parallel_map(lambda v: risk_vendor(v, requirements), vendors, "risk")

    # Award and negotiation are independent once extraction + risk stages finish.
    with ThreadPoolExecutor(max_workers=2 if _KEY_POOL.size >= 2 else 1, thread_name_prefix="bidlens-final") as ex:
        if _KEY_POOL.size >= 2:
            award_future = ex.submit(award, vendors, risks, requirements)
            negotiation_future = ex.submit(negotiate, vendors, requirements)
            award_result = award_future.result()
            neg = negotiation_future.result()
        else:
            award_result = award(vendors, risks, requirements)
            neg = negotiate(vendors, requirements)

    vendor_models = []
    base_cost = sum(finite_number(x.annual_cost) + finite_number(x.setup_cost) / 3 + finite_number(x.variable_cost_per_unit) * 1000 for x in vendors)
    for v, r in zip(vendors, risks):
        checks = deterministic_requirement_checks(v, requirements)
        total = finite_number(v.annual_cost) + finite_number(v.setup_cost) / 3 + finite_number(v.variable_cost_per_unit) * 1000
        commercial = round(max(0, min(10, 10 - total / max(1, base_cost) * 10)), 2)
        pass_count = sum(1 for c in checks if c['status']=='PASS')
        fail_count = sum(1 for c in checks if c['status']=='FAIL')
        unknown_count = sum(1 for c in checks if c['status']=='UNKNOWN')
        fit = round(max(0, min(10, 6.0 + pass_count*0.55 - fail_count*2.0 - unknown_count*0.2 - len(v.missing_info)*0.5)), 2)
        vendor_models.append({
            **v.model_dump(),
            'risk_score': finite_number(r.risk_score),
            'risks': r.risks,
            'requirement_checks': checks,
            'fit_score': fit,
            'commercial_score': commercial,
        })

    all_checks = [{'vendor': vm['name'], **c} for vm in vendor_models for c in vm['requirement_checks']]
    hard_fail = any(c['mandatory'] and c['status'] == 'FAIL' for c in all_checks)
    rec = award_result.recommendation
    rationale = award_result.rationale
    qualified = [
        vm['name'] for vm in vendor_models
        if all((not c['mandatory']) or c['status'] == 'PASS' for c in vm['requirement_checks'])
    ]
    if not qualified:
        rec = 'HOLD / NO QUALIFIED VENDOR'
        rationale = 'No vendor is fully qualified against all mandatory requirements. Resolve failed or unverified mandatory gates before award.'
    elif rec not in qualified:
        rec = qualified[0]
        rationale = f'{rec} is the strongest fully qualified vendor after applying mandatory requirement gates and deterministic scoring.'

    # Never trust the LLM's headline score when deterministic vendor facts and gates are available.
    for vm in vendor_models:
        mandatory_fails = sum(1 for c in vm['requirement_checks'] if c['mandatory'] and c['status']=='FAIL')
        vm['deterministic_award_score'] = round(max(0, min(10, vm['fit_score']*0.45 + vm['commercial_score']*0.30 + (10-vm['risk_score'])*0.25 - mandatory_fails*3)), 2)
    if vendor_models:
        eligible=sorted([vm for vm in vendor_models if all((not c['mandatory']) or c['status']=='PASS' for c in vm['requirement_checks'])], key=lambda x:x['deterministic_award_score'], reverse=True)
        if eligible:
            rec=eligible[0]['name']
            rationale=f"{rec} ranks highest among vendors that fully satisfy the mandatory requirement gates. The score combines deterministic fit, commercial value, terms/SLA evidence and risk."
            headline_score=eligible[0]['deterministic_award_score']
        else:
            rec='HOLD / NO QUALIFIED VENDOR'
            rationale='No vendor fully satisfies the mandatory requirement gates. Resolve failed or unverified mandatory requirements before award.'
            headline_score=0
    else:
        headline_score=0
    for vm in vendor_models:
        vm.pop('deterministic_award_score', None)

    return Comparison(
        recommendation=rec,
        rationale=rationale,
        overall_confidence=min(1.0, max(float(award_result.overall_confidence), 0.70 if vendor_models else 0.35)),
        award_score=headline_score,
        vendors=vendor_models,
        comparison_highlights=award_result.comparison_highlights,
        contradictions=award_result.contradictions,
        requirement_checks=all_checks,
        negotiation=neg,
    )


def _scenario_vendor_eligibility(v):
    checks = v.get('requirement_checks', []) if isinstance(v, dict) else getattr(v, 'requirement_checks', [])
    mandatory = [c for c in checks if c.get('mandatory')]
    failed = [c for c in mandatory if c.get('status') == 'FAIL']
    unresolved = [c for c in mandatory if c.get('status') in {'UNKNOWN', 'NEEDS CONFIRMATION'}]
    if failed:
        reqs = ', '.join(c.get('requirement', 'requirement') for c in failed[:3])
        return False, f'Mandatory fail: {reqs}'
    if unresolved:
        reqs = ', '.join(c.get('requirement', 'requirement') for c in unresolved[:3])
        return False, f'Mandatory confirmation needed: {reqs}'
    return True, 'Eligible'

def scenario(result, volume_units_per_year, horizon_years, renewal_override):
    volume = max(1, int(volume_units_per_year)); horizon = max(1, int(horizon_years))
    base_name = getattr(result, 'recommendation', None)
    raw=[]
    for v in result.vendors:
        vd = v if isinstance(v, dict) else v.model_dump()
        renewal = finite_number(vd.get('renewal_increase_pct', 0)) if renewal_override is None else finite_number(renewal_override)
        unit_price = finite_number(vd.get('unit_price', 0))
        annual_base = unit_price * volume if unit_price > 0 else finite_number(vd.get('annual_cost', 0))
        shipping = finite_number(vd.get('shipping_fees', 0))
        annual = annual_base + (shipping * volume if shipping > 0 else 0)
        tco = finite_number(vd.get('setup_cost', 0)) + sum(annual * ((1 + renewal / 100) ** year) for year in range(horizon))
        eligible, eligibility_reason = _scenario_vendor_eligibility(vd)
        raw.append({
            'name':vd['name'], 'tco':round(tco,2),
            'fit_score':finite_number(vd.get('fit_score',0)),
            'risk_score':finite_number(vd.get('risk_score',0)),
            'qualified':eligible, 'eligibility_reason':eligibility_reason
        })
    if not raw:
        return {'winner':base_name,'vendors':[],'explanation':'No vendors are available for scenario analysis.','decision_change':False}
    tcos=[x['tco'] for x in raw]; low=min(tcos); high=max(tcos)
    for x in raw:
        commercial=10 if high==low else 10 - ((x['tco']-low)/(high-low))*10
        # Same core weights as the main deterministic award engine: fit 45%, commercial 30%, risk 25%.
        x['award_score']=round(max(0,min(10,0.45*x['fit_score']+0.30*commercial+0.25*(10-x['risk_score']))),2)
    eligible=sorted([x for x in raw if x['qualified']],key=lambda x:(x['award_score'],-x['tco']),reverse=True)
    winner=eligible[0]['name'] if eligible else 'HOLD / NO QUALIFIED VENDOR'
    for x in raw:
        x['eligible']=bool(x.pop('qualified'))
        x['rank_change']=0
    ranked=sorted(raw,key=lambda x:((1 if x['eligible'] else 0),x['award_score'],-x['tco']),reverse=True)
    explanation=(f'At {volume:,} units/year for {horizon} years, the deterministic scenario engine recalculates volume-based TCO, '
                 f'commercial score, fit and risk using the same 45/30/25 award weights as the main decision. '
                 f'Mandatory requirement eligibility is preserved; a lower-priced but failed/unverified vendor cannot win the scenario.')
    return {'winner':winner,'vendors':sanitize_json(ranked),'explanation':explanation,'decision_change':winner != base_name}

class RedTeamResult(BaseModel):
    verdict: str
    summary: str
    challenges: list[dict] = Field(default_factory=list)

def red_team(result_data: dict):
    prompt = "You are BidLens Red-Team Agent. Actively try to disprove the current procurement recommendation using ONLY supplied vendor evidence and requirement gates. Look for weak evidence, contradictions, mandatory-gate mistakes, pricing traps, lock-in, residency/security ambiguity, SLA wording, and scale economics. Do not invent facts. Return verdict DEFEND, REOPEN, or HOLD and up to 5 concise challenges with title, evidence, impact, and recommended verification. Return ONLY valid JSON."
    result=structured_call(prompt, json.dumps(sanitize_json(result_data), indent=2, allow_nan=False), RedTeamResult)
    return result or RedTeamResult(verdict='HOLD',summary='Red-team analysis unavailable; manual challenge recommended.',challenges=[])
