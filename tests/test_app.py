import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

from streamlit.testing.v1 import AppTest  # noqa: E402

import engine  # noqa: E402


class FakeClassifier:
    def __init__(self, script):
        self.script = list(script)

    def classify(self, state, answer):
        return self.script.pop(0), {"model": "fake", "input_tokens": 10, "output_tokens": 5}


def test_participant_flow_and_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("ALLOWED_CODES", "P01")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    monkeypatch.chdir(APP_DIR)

    fake = FakeClassifier([
        {"label": "ANSWER"},
        {"label": "ANSWER", "tools": ["Python"]},
        {"label": "DECLINE"},
    ])
    at = AppTest.from_file(os.path.join(APP_DIR, "app.py"), default_timeout=30)
    at.query_params["code"] = "P01"
    at.session_state["_classifier_override"] = fake
    at.run()
    assert not at.exception
    # consent screen (a form: ticking does not rerun; the check happens on submit)
    at.button[0].click().run()
    assert at.error and "tick statements 1 to 4" in at.error[0].value and at.checkbox(key="consent_1")
    for k in ("consent_1", "consent_2", "consent_3", "consent_4"):
        at.checkbox(key=k).check()
    at.button[0].click().run()
    assert not at.exception
    texts = [m.markdown[0].value for m in at.chat_message if m.markdown]
    assert any(engine.Q["Q_ROLES"] in x for x in texts)
    assert not at.chat_input                                   # the form replaces the chat box at this step

    # incomplete job row: error, nothing saved
    at.text_input(key="rf_0_title").input("IT team lead").run()
    at.button(key="rf_submit").click().run()
    assert not at.exception
    assert at.error and "add the company" in at.error[0].value and "when it started" in at.error[0].value
    assert "choose the type of job" in at.error[0].value
    at.selectbox(key="rf_0_kind").select("Full-time")
    # complete it, add a hobby with no period
    at.text_input(key="rf_0_org").input("a startup")
    at.text_input(key="rf_0_start").input("01/2024")
    at.text_input(key="rf_0_end").input("now").run()
    at.button(key="rf_add_hobby").click().run()
    at.text_input(key="rf_1_title").input("formation dancer")
    assert not [w for w in at.text_input if w.key and w.key.startswith("rf_1_") and w.key.endswith(("start", "end"))]
    at.button(key="rf_submit").click().run()
    assert not at.exception, at.exception
    msgs = [m.markdown[0].value for m in at.chat_message if m.markdown]
    assert any("**Job:** IT team lead (Full-time), a startup, since 01/2024" in x and "**Hobby:** formation dancer" in x
               for x in msgs)
    assert engine.Q["Q_AIM"].format(label="IT team lead at a startup") in at.chat_message[-1].markdown[0].value
    assert at.chat_input

    at.chat_input[0].set_value("Build the product").run()
    assert at.chat_message[-1].markdown[0].value == engine.Q["Q_TOOLS_JOB"]
    at.chat_input[0].set_value("Python").run()
    assert engine.Q["P_ABILITY_1"].format(label="IT team lead at a startup") in at.chat_message[-1].markdown[0].value

    at.chat_input[0].set_value("no").run()
    assert at.chat_message[-1].markdown[0].value == engine.Q["P_ABILITY_2"].format(label="IT team lead at a startup")

    # invalid code is refused
    bad = AppTest.from_file(os.path.join(APP_DIR, "app.py"), default_timeout=30)
    bad.query_params["code"] = "X99"
    bad.session_state["_classifier_override"] = fake
    bad.run()
    assert bad.error and "not valid" in bad.error[0].value

    # admin view
    adm = AppTest.from_file(os.path.join(APP_DIR, "app.py"), default_timeout=30)
    adm.query_params["admin"] = "1"
    adm.run()
    adm.text_input(key="admin_pw").input("pw").run()
    assert not adm.exception
    assert any("Turn log" in s.value for s in adm.subheader)


def test_database_down_shows_generic_message(monkeypatch):
    import streamlit as st
    st.cache_resource.clear()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://u:p@127.0.0.1:1/x")
    monkeypatch.chdir(APP_DIR)
    at = AppTest.from_file(os.path.join(APP_DIR, "app.py"), default_timeout=30)
    at.query_params["code"] = "P01"
    at.run()
    assert not at.exception
    assert at.error and "not available" in at.error[0].value
    assert "127.0.0.1" not in at.error[0].value
    st.cache_resource.clear()


def test_admin_profile_flow(tmp_path, monkeypatch):
    import streamlit as st
    from storage import Store
    sys.path.insert(0, os.path.join(APP_DIR, "tests"))
    import test_profile as tp
    import profile_gen as pg

    st.cache_resource.clear()
    url = f"sqlite:///{tmp_path}/p.db"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.chdir(APP_DIR)
    store = Store(url)
    state = dict(tp.STATE, turn=14, pos_idx=1, phase="done", dimension=None, level=None, model="m", prompt_version="t")
    base = {"rule": "r", "phase_before": "prompt", "meta": None, "accepted": 0, "declined": 0, "audit": "",
            "audit_note": ""}
    store.create_session("P01", state, dict(base, turn=0, answer_raw="", question_shown="start", qid="Q_LIST",
                                             classification=None))
    for k, t in enumerate(tp.TURNS, start=1):
        store.save_turn("P01", k, state, dict(base, turn=t["turn"], answer_raw=t["answer_raw"],
                                              question_shown=t["question_shown"], qid=t["qid"],
                                              classification=t["classification"]))

    fake = tp.FakeClient([tp.GOOD])
    at = AppTest.from_file(os.path.join(APP_DIR, "app.py"), default_timeout=60)
    at.query_params["admin"] = "1"
    at.session_state["_generator_override"] = lambda: pg.ProfileGenerator("k", "m", client=fake)
    at.run()
    at.text_input(key="admin_pw").input("pw").run()
    assert not at.exception
    gen_btn = at.button(key="gen_P01")
    assert gen_btn.disabled                                   # no redaction saved yet
    at.text_area(key="rules_P01").input("Anna => a colleague").run()
    at.checkbox(key="redok_P01").check().run()
    at.button(key="redsave_P01").click().run()
    assert not at.exception
    assert not at.button(key="gen_P01").disabled
    at.button(key="gen_P01").click().run()
    assert not at.exception, at.exception
    assert len(fake.calls) == 1
    prof = store.list_profiles("P01")[0]
    assert prof["ok"] and prof["pdf"] and not prof["approved"]
    at.checkbox(key=f"appr_P01_{prof['id']}").check().run()
    assert not at.exception
    assert store.list_profiles("P01")[0]["approved"] == 1
    st.cache_resource.clear()


def test_consent_version_is_read_from_the_shown_text():
    src = open(os.path.join(APP_DIR, "app.py"), encoding="utf-8").read()
    ns = {"re": __import__("re")}
    exec(src[src.index("def consent_version"):src.index("try:\n    store = get_store()")], ns)
    cv = ns["consent_version"]
    assert cv("*Version 3.1 (app version, DRAFT)*") == "3.1"
    assert cv("*Version 3.1*\n **[CONFIRM: x]**") == "3.1-draft"
    assert cv("no header") == "unknown"
