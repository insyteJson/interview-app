import inspect
import json
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from anthropic.resources.messages import Messages  # noqa: E402

import classifier  # noqa: E402
import engine  # noqa: E402


def make_classifier():
    return classifier.Classifier("sk-test", "claude-opus-5-5", "medium")


def state():
    s, _ = engine.start(engine.new_state("X", "m"))
    return s


def test_request_matches_installed_sdk_signature():
    """The bug that broke the first real run: a keyword the SDK does not define (temperature)."""
    kwargs = make_classifier().request_kwargs(state(), "I worked as a team lead.")
    sig = inspect.signature(Messages.create)
    sig.bind(None, **kwargs)  # raises TypeError for any unknown keyword
    for forbidden in ("temperature", "top_p", "top_k", "tool_choice", "tools", "thinking"):
        assert forbidden not in kwargs


def test_request_uses_structured_output_and_effort():
    kwargs = make_classifier().request_kwargs(state(), "x")
    oc = kwargs["output_config"]
    assert oc["effort"] == "medium"
    assert oc["format"]["type"] == "json_schema"
    assert kwargs["max_tokens"] >= 4000  # thinking tokens count toward max_tokens


def _walk(node, path="schema"):
    if isinstance(node, dict):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, f"{path}: additionalProperties must be false"
            assert set(node["required"]) == set(node["properties"]), f"{path}: all properties must be required"
        yield node
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def test_schema_respects_documented_limits():
    nodes = list(_walk(classifier.SCHEMA))
    unions = [n for n in nodes if isinstance(n.get("type"), list)]
    assert len(unions) <= 16  # union-type parameters per request
    props = classifier.SCHEMA["properties"]
    assert len(props) == len(classifier.SCHEMA["required"])  # zero optional parameters (limit is 24)
    json.dumps(classifier.SCHEMA)


def _msg(payload, stop="end_turn", text_first=False):
    blocks = [NS(type="thinking", thinking="", signature="s"),
              NS(type="text", text=payload if isinstance(payload, str) else json.dumps(payload))]
    return NS(model="claude-opus-5-5", content=blocks, stop_reason=stop,
              usage=NS(input_tokens=1200, output_tokens=300, cache_read_input_tokens=1000))


GOOD = {"rationale": "r", "label": "Answer", "situation": None, "action": "I planned", "outcome": None,
        "outcome_definite": False, "restatement": "you planned", "tools": [], "confirm": "N/A"}


def test_parse_normalises_case_and_skips_thinking_block():
    d = classifier.parse_response(_msg(GOOD))
    assert d["label"] == "ANSWER" and d["confirm"] == "n/a"


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_unusable_stop_reasons_raise(stop):
    with pytest.raises(ValueError):
        classifier.parse_response(_msg(GOOD, stop=stop))


def test_bad_label_and_bad_json_raise():
    with pytest.raises(ValueError):
        classifier.parse_response(_msg({**GOOD, "label": "MAYBE"}))
    with pytest.raises(json.JSONDecodeError):
        classifier.parse_response(_msg("not json"))


def test_classify_retries_then_succeeds_and_logs_meta():
    c = make_classifier()
    calls = []

    def create(**kw):
        calls.append(kw)
        return _msg("garbage") if len(calls) == 1 else _msg(GOOD)

    c.client = NS(messages=NS(create=create))
    d, meta = c.classify(state(), "I planned it")
    assert d["label"] == "ANSWER" and meta["attempt"] == 2
    assert meta["effort"] == "medium" and meta["output_tokens"] == 300 and "temperature" not in meta


def test_classify_gives_up_with_clear_error():
    c = make_classifier()
    c.client = NS(messages=NS(create=lambda **kw: _msg("garbage")))
    with pytest.raises(classifier.ClassifierError):
        c.classify(state(), "x")


def test_invalid_effort_rejected():
    with pytest.raises(ValueError):
        classifier.Classifier("k", "m", "turbo")


def test_roles_are_no_longer_extracted_by_the_model():
    props = classifier.SCHEMA["properties"]
    assert "positions" not in props and "paid" not in props
    assert "listing" not in classifier.SYSTEM and "current_positions" not in json.dumps(
        classifier.build_input(dict(state(), phase="aim"), "x"))
