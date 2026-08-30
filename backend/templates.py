TEMPLATES = [
    {
        "id":"saas","name":"Enterprise SaaS / CRM","category":"Software / SaaS",
        "description":"Compare SaaS pricing, implementation, security, residency, SLA and renewal terms.",
        "prompt":"RFP: Enterprise CRM for 1,000 users initially, scalable to 8,000 users. Weights: Functional fit 35%, TCO 30%, Security 15%, SLA/Support 10%, Contract flexibility 10%. Mandatory: SSO/SAML, SCIM included in base price, API, 99.9% SLA, India/EU residency option, annual escalation <=5%, data export. Preferred: sandbox, AI sales insights, 24x7 P1 support. Horizon: 3 years. Evidence policy: PASS only when the proposal explicitly states the capability is available; missing or roadmap-only statements must be NEEDS CONFIRMATION, never PASS. Confirm implementation timeline, contract term, payment terms, API, sandbox, authentication method, residency commitment, data export and migration support. Evaluate mandatory gates before award eligibility."
    },
    {
        "id":"cloud","name":"Cloud / Data Platform","category":"Cloud & Data",
        "description":"Normalize storage, compute, usage, egress, support and commitment costs.",
        "prompt":"RFP: Cloud analytics platform. Workload: 20 TB storage, 50M queries/month, 200 analysts. Weights: Technical 40%, 3-year TCO 30%, Security 15%, Support 10%, Contract 5%. Mandatory: encryption, SSO, audit logs, 99.9% availability, RBAC, export. Preferred: India region, autoscaling, predictable pricing. Compare usage costs, egress/export fees, minimum commitments, annual uplift, support and security. Treat missing or roadmap-only evidence as NEEDS CONFIRMATION."
    },
    {
        "id":"cybersecurity","name":"Cybersecurity / MDR","category":"Security",
        "description":"Test hard security gates, SOC coverage, response SLAs, residency and incident terms.",
        "prompt":"RFP: Managed Detection & Response for 3,000 endpoints and 250 servers. Weights: Security 35%, Technical 25%, TCO 20%, SLA 15%, Terms 5%. Hard gates: 24x7 SOC, P1 <=15 min, India residency, ISO 27001 or SOC 2, incident escalation. Preferred: threat hunting and ransomware response. Treat hard-gate failures as NO-GO regardless of price; missing or roadmap-only evidence is NEEDS CONFIRMATION."
    },
    {
        "id":"hrpayroll","name":"HR / Payroll","category":"Business Applications",
        "description":"Compare compliance, statutory support, employee features, support and contractual gaps.",
        "prompt":"RFP: Payroll + HR for 12,000 employees across India. Weights: Payroll/compliance 35%, Functional 25%, TCO 20%, Security 10%, Support 10%. Mandatory: Indian payroll, statutory support, SSO, audit logs, 99.9% availability, self-service. Preferred: mobile app, AI assistant, configurable workflows. Horizon: 5 years. Highlight missing renewal caps, unclear liability and data export limitations. Missing evidence must be NEEDS CONFIRMATION."
    },
    {
        "id":"hardware","name":"Hardware / Equipment","category":"Hardware / Logistics",
        "description":"Compare unit economics, shipping, volume discounts, delivery, warranty and support.",
        "prompt":"RFP: 3-year equipment procurement for 1,000 units. Weights should emphasize total landed cost and functional fit. Compare unit price, total landed cost, shipping fees, volume discounts, payment terms, delivery lead time, warranty, technical specifications, quality certifications and service SLA. Flag price breaks that only activate above the committed volume and surface missing commercial terms as NEEDS CONFIRMATION."
    },
    {
        "id":"professional-services","name":"Professional Services / SI","category":"Services",
        "description":"Compare implementation plans, staffing, milestones, fees, SLAs and delivery risk.",
        "prompt":"RFP: Professional-services procurement over 12 months. Compare implementation cost, day rates, staffing model, milestones, delivery timeline, assumptions, change-request exposure, warranty/support, SLA, references and past performance. Identify scope and schedule risk, and distinguish explicit evidence from missing information."
    },
    {
        "id":"negotiation","name":"Negotiation Copilot","category":"Commercial Strategy",
        "description":"Turn proposal differences into evidence-backed negotiation levers.",
        "prompt":"RFP: Contact-center platform for 500 agents initially, potentially 2,500. Weights: Functional 30%, TCO 25%, SLA 15%, Security 15%, Contract flexibility 15%. Mandatory: SSO, recording, API, 99.95% uptime, 24x7 P1. Preferred: AI summaries, workforce forecasting, omnichannel. Analyze the proposals as a procurement negotiation: identify the top five evidence-backed levers, buyer target, fallback position, vendor leverage and concise negotiation question. Do not invent leverage unsupported by proposal evidence."
    },
]
