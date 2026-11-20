"""Provider choice (Claude models, Ollama) against a fake Ollama server, and the compact layout."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import time
import pytest
from PySide6.QtWidgets import QApplication, QLabel
from smftp.core import assistant as A
from test_audit_known_issues import close_window


class FakeOllama:
    """Answers /api/tags and plays scripted /api/chat replies, recording each request."""

    def __init__(self):
        self.replies = []
        self.requests = []
        self.models = [
            {"name": "qwen3:4b", "details": {"parameter_size": "4.0B"}, "capabilities": ["completion", "tools", "thinking"]},
            {"name": "deepseek-coder:6.7b", "details": {"parameter_size": "7B"}, "capabilities": ["completion"]},
        ]
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/tags":
                    self.reply(200, {"models": owner.models})
                else:
                    self.reply(404, {"error": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(body)
                if body["model"] not in {m["name"] for m in owner.models}:
                    self.reply(404, {"error": f"model '{body['model']}' not found"})
                    return
                self.reply(200, owner.replies.pop(0))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ollama():
    server = FakeOllama()
    yield server
    server.close()


def message(content="", calls=None, reason="stop"):
    data = {"role": "assistant", "content": content}
    if calls:
        data["tool_calls"] = [{"function": {"name": name, "arguments": args}} for name, args in calls]
    return {"message": data, "done_reason": reason}


# ---------- core ----------

def test_models_are_listed_tool_capable_first(ollama):
    models = A.ollama_models(ollama.url)
    assert [m["name"] for m in models] == ["qwen3:4b", "deepseek-coder:6.7b"]
    assert models[0]["tools"] and models[0]["thinking"] and not models[1]["tools"]


def test_ollama_tool_loop_and_request_shape(ollama):
    ollama.replies = [message(calls=[("list_scheduled_tasks", {})]), message("Aucune tâche prévue.")]
    seen = []
    conversation = A.OllamaConversation(lambda name, data: (seen.append((name, data)) or "[]", False),
                                        model="qwen3:4b", base_url=ollama.url, thinking=True)
    result = conversation.send("Quoi de prévu ?", context="Date : vendredi")
    assert result.text == "Aucune tâche prévue." and seen == [("list_scheduled_tasks", {})]
    first, second = ollama.requests
    assert first["think"] is True and first["stream"] is True and first["options"]["num_ctx"] == A.OLLAMA_CONTEXT
    assert first["messages"][0]["role"] == "system" and "[Contexte SMFTP]" in first["messages"][1]["content"]
    assert {t["function"]["name"] for t in first["tools"]} == {t["name"] for t in A.TOOLS}
    assert second["messages"][-1] == {"role": "tool", "tool_name": "list_scheduled_tasks", "content": "[]"}


def test_tool_call_written_as_text_is_recognised(ollama):
    ollama.replies = [message('```json\n{"name": "read_journal", "arguments": {"lines": "50"}}\n```'), message("Tout va bien.")]
    seen = []
    conversation = A.OllamaConversation(lambda name, data: (seen.append((name, data)) or "ok", False),
                                        model="qwen3:4b", base_url=ollama.url)
    assert conversation.send("Le journal ?").text == "Tout va bien."
    assert seen == [("read_journal", {"lines": 50})]  # String "50" converted to the schema's integer.
    assert "think" not in ollama.requests[0]


def test_several_calls_written_as_text(ollama):
    ollama.replies = [message('{"name": "list_profiles", "arguments": {}}\n{"name": "list_scheduled_tasks", "arguments": {}}'),
                      message("Voilà.")]
    seen = []
    conversation = A.OllamaConversation(lambda name, data: (seen.append(name) or "ok", False),
                                        model="qwen3:4b", base_url=ollama.url)
    assert conversation.send("État ?").text == "Voilà." and seen == ["list_profiles", "list_scheduled_tasks"]
    assert A.parse_text_tool_calls('{"name": "inconnu", "arguments": {}}') == []
    assert A.parse_text_tool_calls('{"name": read_journal, "arguments": {"lines": 20}}') == [("read_journal", {"lines": 20})]
    assert A.parse_text_tool_calls("Voici la réponse.") == []


def test_silent_model_after_an_action_shows_what_smftp_did(ollama):
    ollama.replies = [message(calls=[("delete_scheduled_task", {"task_id": "x"})]), message("")]
    conversation = A.OllamaConversation(lambda name, data: ("Tâche supprimée.", False),
                                        model="qwen3:4b", base_url=ollama.url)
    assert conversation.send("Supprime-la").text == "Tâche supprimée."


def test_invalid_choices_fall_back_to_safe_values():
    data = A.coerce_arguments("create_scheduled_task", {"conflict": "", "kind": "effacer", "recurrence_type": "?"})
    assert (data["conflict"], data["kind"], data["recurrence_type"]) == ("newer", "sync", "daily")


def test_stop_interrupts_a_long_answer(ollama):
    ollama.replies = [message("une très longue réflexion")]
    progress = []
    conversation = A.OllamaConversation(lambda *a: ("", False), model="qwen3:4b", base_url=ollama.url)
    conversation.on_progress = progress.append
    result = conversation.send("Bonjour", cancelled=lambda: bool(conversation.messages) and len(ollama.requests) > 0)
    assert result.stopped == "cancelled"


def test_arguments_are_fitted_to_the_schema():
    data = A.coerce_arguments("create_scheduled_task", {"name": "Nuit", "profile": "Prod", "kind": "sync",
                                                        "compare_content": "true", "days": "lundi, mardi", "minutes": None,
                                                        "local": "C:\\Site", "remote": "/www", "extra": "ignoré"})
    assert data["compare_content"] is True and data["days"] == ["lundi", "mardi"] and data["minutes"] == 0
    assert data["catch_up"] is True and data["enabled"] is True and data["time"] == "" and "extra" not in data
    assert set(data) == set(next(t for t in A.TOOLS if t["name"] == "create_scheduled_task")["input_schema"]["properties"])


def test_errors_are_explained(ollama):
    conversation = A.OllamaConversation(lambda *a: ("", False), model="absent:1b", base_url=ollama.url)
    with pytest.raises(A.ProviderError, match="ollama pull"):
        conversation.send("Bonjour")
    assert conversation.messages == []  # The failed turn is not kept.
    closed = A.OllamaConversation(lambda *a: ("", False), model="qwen3:4b", base_url="http://127.0.0.1:9")
    with pytest.raises(A.ProviderError, match="lancé"):
        closed.send("Bonjour")


def test_claude_haiku_is_sent_without_fallbacks():
    from types import SimpleNamespace as NS
    requests = []
    client = NS(beta=NS(messages=NS(create=lambda **kw: requests.append(kw) or NS(stop_reason="end_turn", content=[]))))
    A.ClaudeConversation(client, lambda *a: ("", False), model="claude-haiku-5-5").send("x")
    A.ClaudeConversation(client, lambda *a: ("", False), model="claude-sonnet-5-5").send("x")
    assert "fallbacks" not in requests[0] and "betas" not in requests[0] and requests[0]["model"] == "claude-haiku-5-5"
    assert requests[1]["fallbacks"] == "default"


def test_local_url_detection():
    assert A.is_local_url("http://localhost:11434") and A.is_local_url("http://127.0.0.1:1")
    assert not A.is_local_url("http://192.168.1.20:11434")


# ---------- window ----------

@pytest.fixture
def window():
    QApplication.instance() or QApplication([])
    from smftp.ui.main_window import MainWindow
    win = MainWindow()
    yield win
    close_window(win)


def wait_for(condition, seconds=10):
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    assert condition()


def test_settings_dialog_lists_and_saves_an_ollama_model(window, ollama):
    from smftp.ui.assistant import AssistantSettings
    dialog = AssistantSettings(window.settings, window)
    dialog.ollama_url.setText(ollama.url)
    dialog.ollama_choice.setChecked(True)
    dialog.refresh_models()
    wait_for(lambda: dialog.ollama_model.count() == 2)
    assert dialog.chosen_model() == "qwen3:4b" and "2 modèle" in dialog.ollama_status.text()
    dialog.ollama_model.setCurrentIndex(1)
    assert "ne sait pas appeler d’outils" in dialog.ollama_hint.text()
    dialog.ollama_model.setCurrentIndex(0)
    dialog.save()
    assert window.settings.value("assistant_provider") == "ollama"
    assert window.settings.value("assistant_ollama_model") == "qwen3:4b"
    assert window.settings.value("assistant_ollama_thinking", type=bool) is True


def test_panel_chats_with_ollama_and_never_asks_cloud_consent(window, ollama, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    from smftp.ui.assistant import Bubble
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.No)
    for key, value in (("assistant_provider", "ollama"), ("assistant_ollama_url", ollama.url),
                       ("assistant_ollama_model", "qwen3:4b"), ("assistant_consent", False)):
        window.settings.setValue(key, value)
    panel = window.assistant_panel
    panel.refresh_provider()
    assert panel.provider.text().startswith("Ollama · qwen3:4b · local")
    assert "restent sur ce PC" in panel.privacy.text()
    ollama.replies = [message(calls=[("list_profiles", {})]), message("Vous n’avez encore aucun profil.")]
    panel.input.setText("Quels serveurs ai-je ?")
    panel.send()
    wait_for(lambda: not panel.busy())
    QApplication.instance().processEvents()
    texts = [b.label.text() for b in panel.findChildren(Bubble)]
    assert texts == ["Quels serveurs ai-je ?", "Vous n’avez encore aucun profil."] and not asked


def test_missing_ollama_shows_an_error_bubble(window):
    from smftp.ui.assistant import Bubble
    for key, value in (("assistant_provider", "ollama"), ("assistant_ollama_url", "http://127.0.0.1:9"),
                       ("assistant_ollama_model", "qwen3:4b")):
        window.settings.setValue(key, value)
    panel = window.assistant_panel
    panel.input.setText("Bonjour")
    panel.send()
    wait_for(lambda: not panel.busy())
    QApplication.instance().processEvents()
    errors = [b for b in panel.findChildren(Bubble) if b.objectName() == "bubbleError"]
    assert errors and "Ollama" in errors[-1].label.text() and panel.conversation is None


def test_compact_layout(window):
    labels = [label.text() for label in window.findChildren(QLabel)]
    assert "Un lien direct avec vos fichiers." not in labels  # The banner is gone.
    assert window.disconnect_button.parent() is window.remote and not window.disconnect_button.isEnabled()
    assert window.connect_button.objectName() == "primary"
    assert window.menuBar().cornerWidget() is not None
    window.set_assistant_visible(True)
    assert window.assistant_panel.isVisibleTo(window) and window.assistant_toggle.isChecked()
    assert window.settings.value("assistant_visible", type=bool) is True
    window.assistant_toggle.setChecked(False)
    assert not window.assistant_panel.isVisibleTo(window)
    window.show_assistant()
    assert window.assistant_panel.isVisibleTo(window)
