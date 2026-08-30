import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from config import Settings

def test_model_priority_comma_list():
    s=Settings(gemini_models="model-a, model-b, model-c")
    assert s.model_priority()==["model-a","model-b","model-c"]

def test_model_priority_bracket_list():
    s=Settings(gemini_models="[model-a, model-b, model-c]")
    assert s.model_priority()==["model-a","model-b","model-c"]

def test_single_model_backward_compatibility():
    s=Settings(gemini_models="", gemini_model="model-a")
    assert s.model_priority()==["model-a"]
