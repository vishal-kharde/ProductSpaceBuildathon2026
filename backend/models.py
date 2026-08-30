from typing import List, Literal
from pydantic import BaseModel, Field

class Evidence(BaseModel):
    field: str
    value: str
    source: str
    confidence: float = Field(ge=0, le=1)

class Vendor(BaseModel):
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
    technical_capability: List[str] = Field(default_factory=list)
    reliability: List[str] = Field(default_factory=list)
    service_support: List[str] = Field(default_factory=list)
    fit_score: float = Field(ge=0, le=10)
    commercial_score: float = Field(ge=0, le=10)
    risk_score: float = Field(ge=0, le=10)
    evidence_coverage: float = Field(ge=0, le=1)
    missing_info: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    requirement_checks: List[dict] = Field(default_factory=list)

class Comparison(BaseModel):
    recommendation: str
    rationale: str
    overall_confidence: float = Field(ge=0, le=1)
    award_score: float = Field(ge=0, le=10)
    vendors: List[Vendor]
    comparison_highlights: List[str] = Field(default_factory=list)
    contradictions: List[str] = Field(default_factory=list)
    requirement_checks: List[dict] = Field(default_factory=list)
    negotiation: List[dict] = Field(default_factory=list)

class AnalysisRequest(BaseModel):
    requirements: str
    volume_units_per_year: int = 1000
    horizon_years: int = 3

class ScenarioRequest(BaseModel):
    volume_units_per_year: int = 1000
    horizon_years: int = 3
    renewal_increase_override: float | None = None

class ScenarioVendor(BaseModel):
    name: str
    tco: float
    award_score: float
    risk_score: float
    eligible: bool = True
    eligibility_reason: str = "Eligible"
    rank_change: int = 0

class ScenarioResponse(BaseModel):
    winner: str
    vendors: List[ScenarioVendor]
    explanation: str
    decision_change: bool = False

class ProposalText(BaseModel):
    name: str
    text: str

class Template(BaseModel):
    id: str
    name: str
    category: str
    description: str
    prompt: str
