"""Example questions for an agent's page: the template's own, the model's for an uploaded document, then the headings."""
from services import llm
from tests.test_offline_agents import LOCAL, upload_txt
from tests.test_templates import env  # noqa: F401  (fixture: fake embeddings, no language model)


def direct_agent(env):
    return env.local_db.insert("agents", {"user_id": "local-user", "project_id": "local_project", "name": "Q",
                                          "system_instructions": "x", "greeting": "", "supported_languages": ["en"]})


def make_qa(client):
    r = client.post("/api/agents/from-template", headers=LOCAL, json={"template_id": "clinic"})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_fresh_template_agent_gets_the_template_questions(app_client, env):
    a = make_qa(app_client)
    r = app_client.get(f"/api/agents/{a['id']}/example-questions", headers=LOCAL).json()
    assert r["source"] == "template" and "What are the clinic timings?" in r["questions"]


def test_a_template_without_a_sample_pdf_has_no_pdf_to_download(app_client, env):
    r = app_client.get("/api/agent-templates/clinic/sample.pdf", headers=LOCAL)
    assert r.status_code == 404


def test_questions_for_an_uploaded_pdf_come_from_the_model(app_client, env, monkeypatch):
    a = direct_agent(env)
    upload_txt(app_client, a["id"], "## Refunds\nThe refund window is thirty days from purchase.\n" * 3)

    def chat(messages, **kw):
        assert "refund window" in messages[0]["content"]  # the model reads the document's text
        return llm.ChatResult('["How long is the refund window?", "Is there a refund for a late return?", '
                              '"Not a question", "Who pays for shipping?"]', "fake", "fake-model", 1, {})

    monkeypatch.setattr(llm, "chat", chat)
    r = app_client.get(f"/api/agents/{a['id']}/example-questions", headers=LOCAL).json()
    assert r["source"] == "model"
    assert r["questions"] == ["How long is the refund window?", "Is there a refund for a late return?",
                              "Who pays for shipping?"]
    assert app_client.get(f"/api/agents/{a['id']}/example-questions", headers=LOCAL).json()["source"] == "cache"


def test_headings_are_the_fallback_when_no_model_answers(app_client, env, monkeypatch):
    a = direct_agent(env)
    upload_txt(app_client, a["id"], "## Late fees\nTwo rupees a day.\n\n## Opening hours\nNine to seven.\n" * 2)

    def broken(*_a, **_k):
        raise RuntimeError("no model")

    monkeypatch.setattr(llm, "chat", broken)
    r = app_client.get(f"/api/agents/{a['id']}/example-questions", headers=LOCAL).json()
    assert r["source"] == "headings"
    assert "What does the document say about late fees?" in r["questions"]


def test_an_agent_with_no_documents_has_no_questions(app_client, env):
    a = direct_agent(env)
    assert app_client.get(f"/api/agents/{a['id']}/example-questions", headers=LOCAL).json()["questions"] == []
