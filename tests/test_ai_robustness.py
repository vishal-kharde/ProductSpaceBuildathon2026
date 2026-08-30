import os
import sys
import json
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from config import Settings
from ai import _normalize_extraction_payload, parse_json_output


def test_api_keys_support_json_list_and_deduplicate():
    s = Settings(gemini_api_key='["k1", "k2", "k1"]')
    assert s.api_keys() == ["k1", "k2"]


def test_api_keys_support_comma_separated_values():
    s = Settings(gemini_api_key="k1, k2, k3")
    assert s.api_keys() == ["k1", "k2", "k3"]


def test_gemini_evidence_category_snippet_is_normalized():
    payload = {
        "name": "Vendor A",
        "annual_cost": 150000,
        "evidence_coverage": 0.9,
        "evidence": [
            {
                "category": "financial",
                "snippet": "$150,000 annual subscription.",
                "page": 4,
            }
        ],
    }
    normalized = _normalize_extraction_payload(payload, "Vendor A")
    assert normalized["evidence"] == [
        {
            "field": "financial",
            "value": "$150,000 annual subscription.",
            "source": "Page 4",
            "confidence": 0.9,
        }
    ]


def test_parse_json_output_handles_code_fence_and_nonfinite_values():
    raw = '```json\n{"x": NaN, "y": 1e-20}\n```'
    assert parse_json_output(raw) == {"x": None, "y": 0}


def test_key_pool_never_gives_same_key_to_concurrent_workers(monkeypatch):
    import ai
    import time

    monkeypatch.setattr(ai.settings, "gemini_api_key", '["k1", "k2", "k3"]', raising=False)
    pool = ai._GeminiKeyPool()
    slots = []

    def worker():
        with pool.acquire() as key:
            slots.append(key)
            time.sleep(0.05)

    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(lambda _: worker(), range(3)))

    assert sorted(slots) == ["k1", "k2", "k3"]


def test_crm_requirement_checks_capture_known_mandatory_facts():
    import ai
    from models import Vendor
    v=Vendor(name='ApexWorks CRM', annual_cost=650000, unit_price=650, setup_cost=150000, renewal_increase_pct=5, fit_score=8, commercial_score=8, risk_score=2, evidence_coverage=0.9,
             sla='99.95%', security='ISO 27001, SOC 2, SSO, SCIM; India data residency; EU data residency',
             technical_capability=['API', 'Data export supported'], implementation_days=None, payment_terms_days=None)
    req=('Mandatory: SSO/SAML, SCIM, API, 99.9% SLA, India/EU residency option, annual escalation <=5%, data export.')
    checks=ai.deterministic_requirement_checks(v, req)
    by={c['requirement']:c for c in checks}
    assert by['SSO/SAML']['status']=='UNKNOWN'
    assert by['SCIM']['status']=='PASS'
    assert by['API']['status']=='PASS'
    assert by['India data residency']['status']=='UNKNOWN'
    assert by['EU data residency']['status']=='UNKNOWN'
    assert by['99.9% SLA']['status']=='PASS'
    assert by['Renewal/escalation <= 5%']['status']=='PASS'
    assert by['Data export']['status']=='PASS'


def test_missing_terms_are_unknown_not_zero():
    import ai
    from models import Vendor
    v=Vendor(name='Vendor', security='Not stated', sla='Not stated', fit_score=5, commercial_score=5, risk_score=5, evidence_coverage=0)
    checks=ai.deterministic_requirement_checks(v, 'Must support implementation within 60 days and renewal cap <= 5%. Prefer 60+ day payment terms.')
    by={c['requirement']:c for c in checks}
    assert by['Implementation <= 60 days']['status']=='UNKNOWN'
    assert by['Renewal/escalation <= 5%']['status']=='UNKNOWN'
    assert by['Payment terms >= 60 days']['status']=='UNKNOWN'

def test_unknowns_have_actionable_confirmation_text():
    import ai
    from models import Vendor
    v=Vendor(name='Vendor', security='Not stated', sla='Not stated', fit_score=5, commercial_score=5, risk_score=5, evidence_coverage=0)
    checks=ai.deterministic_requirement_checks(v, 'Mandatory: API, India data residency. Must support implementation within 60 days.')
    by={c['requirement']:c for c in checks}
    assert by['API']['status']=='UNKNOWN'
    assert 'Needs vendor confirmation' in by['API']['action']
    assert by['India data residency']['status']=='UNKNOWN'
    assert by['Implementation <= 60 days']['status']=='UNKNOWN'


def test_paid_scim_addon_is_not_a_clean_mandatory_pass():
    import ai
    from models import Vendor
    v=Vendor(name='CloudPeak', security='SOC 2; SSO; SCIM is paid add-on.', fit_score=5, commercial_score=5, risk_score=5, evidence_coverage=.8)
    checks=ai.deterministic_requirement_checks(v, 'Mandatory: SCIM.')
    assert checks[0]['status']=='UNKNOWN'


def test_explicit_proposal_facts_override_llm_values():
    import ai
    text = '''Vendor Proposal — Demo
Price
$510/user/year; $80,000 implementation.
Implementation: 60 days from kickoff.
Payment: Net 45.
Contract: 3-year initial term.
'''
    v = ai.ExtractedVendor(name='Demo', unit_price=999, implementation_days=5, payment_terms_days=10, contract_years=1, fit_score=5 if False else 0)
    # model only validates extraction fields; enrichment must trust explicit proposal facts.
    out = ai._enrich_extracted_vendor(v, text)
    assert out.unit_price == 510
    assert out.setup_cost == 80000
    assert out.implementation_days == 60
    assert out.payment_terms_days == 45
    assert out.contract_years == 3


def test_crm_enrichment_preserves_explicit_source_facts():
    import ai
    raw=ai.ExtractedVendor(name='ApexWorks CRM', security='Not stated')
    text='''Vendor Proposal — ApexWorks CRM
Price
$650/user/year; $150,000 implementation.
SLA
99.95%; P1 15 min, 24x7.
Security
ISO 27001, SOC 2, SSO, SCIM.
Residency
EU and India.
Exit
Export + 80 hours migration support.
AI
AI sales insights included.
'''
    v=ai._enrich_extracted_vendor(raw,text)
    assert v.implementation_days==None
    assert v.payment_terms_days==None
    assert 'SSO' in v.security and 'SAML' not in v.security
    assert 'SCIM' in v.security
    assert 'India data residency' in v.security
    assert 'EU data residency' in v.security
    assert 'Data export supported' in v.technical_capability
    assert 'AI sales insights included' in v.technical_capability
    assert '24x7 support' in v.service_support


def test_cloudpeak_roadmap_residency_needs_confirmation_and_northstar_passes():
    import ai
    from models import Vendor
    cloud=Vendor(name='CloudPeak', security='SOC 2; SSO; SCIM', residency='US/EU; India on roadmap, no date.', fit_score=0, commercial_score=0, risk_score=0, evidence_coverage=0.5)
    north=Vendor(name='Northstar', security='SOC 2; SSO; SAML; SCIM', residency='US, EU and India', fit_score=0, commercial_score=0, risk_score=0, evidence_coverage=0.5)
    req='Must support EU/India data residency.'
    c={x['requirement']:x for x in ai.deterministic_requirement_checks(cloud, req)}
    n={x['requirement']:x for x in ai.deterministic_requirement_checks(north, req)}
    assert c['India data residency']['status']=='UNKNOWN'
    assert c['EU data residency']['status']=='PASS'
    assert n['India data residency']['status']=='PASS'
    assert n['EU data residency']['status']=='PASS'
