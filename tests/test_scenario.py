from types import SimpleNamespace
import sys
sys.path.insert(0, "backend")
from ai import scenario

def test_scenario_runs():
    result=SimpleNamespace(vendors=[
        {"name":"A","annual_cost":10000,"setup_cost":1000,"variable_cost_per_unit":1,"renewal_increase_pct":5,"fit_score":8,"risk_score":3},
        {"name":"B","annual_cost":7000,"setup_cost":500,"variable_cost_per_unit":4,"renewal_increase_pct":2,"fit_score":7,"risk_score":2},
    ])
    out=scenario(result, 1000, 3, None)
    assert out["winner"] in {"A","B"}
    assert len(out["vendors"])==2


def test_scenario_uses_volume_tco_and_respects_mandatory_gates():
    result=SimpleNamespace(recommendation='A', vendors=[
        {'name':'A','annual_cost':1000,'unit_price':2,'setup_cost':0,'shipping_fees':0,'renewal_increase_pct':0,'fit_score':9,'risk_score':2,'requirement_checks':[{'mandatory':True,'status':'PASS'}]},
        {'name':'B','annual_cost':1000,'unit_price':1,'setup_cost':0,'shipping_fees':0,'renewal_increase_pct':0,'fit_score':10,'risk_score':1,'requirement_checks':[{'mandatory':True,'status':'FAIL'}]},
    ])
    out=scenario(result,1000,3,None)
    assert out['winner']=='A'
    assert out['vendors'][0]['name']=='A' or out['vendors'][1]['name']=='A'


def test_scenario_prefers_only_eligible_vendor_even_if_ineligible_raw_score_is_higher():
    result=SimpleNamespace(recommendation='Northstar CRM', vendors=[
        {'name':'ApexWorks CRM','unit_price':650,'annual_cost':650000,'setup_cost':150000,'shipping_fees':0,'renewal_increase_pct':5,'fit_score':10,'risk_score':2.5,
         'requirement_checks':[{'requirement':'SSO/SAML','mandatory':True,'status':'NEEDS CONFIRMATION'}]},
        {'name':'CloudPeak CRM','unit_price':510,'annual_cost':510000,'setup_cost':80000,'shipping_fees':0,'renewal_increase_pct':9,'fit_score':5.9,'risk_score':7,
         'requirement_checks':[{'requirement':'Renewal/escalation <= 5%','mandatory':True,'status':'FAIL'}]},
        {'name':'Northstar CRM','unit_price':720,'annual_cost':720000,'setup_cost':120000,'shipping_fees':0,'renewal_increase_pct':4,'fit_score':9.9,'risk_score':2.5,
         'requirement_checks':[{'requirement':'SSO/SAML','mandatory':True,'status':'PASS'}]},
    ])
    out=scenario(result,1000,1,None)
    assert out['winner']=='Northstar CRM'
    assert any(v['name']=='CloudPeak CRM' and not v['eligible'] for v in out['vendors'])
    assert any(v['name']=='ApexWorks CRM' and not v['eligible'] for v in out['vendors'])
