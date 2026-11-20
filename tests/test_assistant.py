"""Assistant: tool loop, request shape, confirmations and the chat tab (scripted fake client, no API calls)."""
from types import SimpleNamespace as NS
import time
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QLabel
from smftp.core import assistant as A
from smftp.core import schedule as S
from test_clients import ftp_server  # noqa: F401  (fixture)
from test_audit_known_issues import close_window


def text(value):
    return NS(type="text", text=value)


def call(name, data, ident="toolu_1"):
    return NS(type="tool_use", id=ident, name=name, input=data)


def reply(*blocks, stop="end_turn"):
    return NS(stop_reason=stop, content=list(blocks))


class FakeClient:
    """Plays scripted responses and records each request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kwargs):
        # Copy the history as sent: the conversation keeps appending to the same list.
        self.requests.append(dict(kwargs, messages=list(kwargs["messages"])))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ---------- core loop ----------

def test_request_uses_opus_fallbacks_effort_and_strict_tools():
    client = FakeClient(reply(text("Bonjour")))
    conversation = A.Conversation(client, lambda name, data: ("", False))
    result = conversation.send("Salut", context="Date : lundi")
    assert result.text == "Bonjour" and not result.stopped
    request = client.requests[0]
    assert request["model"] == "claude-opus-5-5" and request["max_tokens"] == 16000
    assert request["fallbacks"] == "default" and request["betas"] == ["server-side-fallback-2026-07-01"]
    assert request["output_config"] == {"effort": "medium"} and "thinking" not in request
    assert request["messages"][0]["content"][0]["text"].startswith("[Contexte SMFTP]")
    for tool in request["tools"]:
        schema = tool["input_schema"]
        assert tool["strict"] and schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])


def test_tool_calls_run_and_all_results_return_in_one_message():
    seen = []
    def execute(name, data):
        seen.append((name, data))
        return f"résultat {name}", name == "read_journal"
    client = FakeClient(
        reply(text("Je regarde."), call("list_scheduled_tasks", {}, "a"), call("read_journal", {"lines": 50}, "b"),
              stop="tool_use"),
        reply(text("Aucune tâche.")))
    conversation = A.Conversation(client, execute)
    result = conversation.send("Quoi de prévu ?")
    assert result.text == "Aucune tâche." and seen == [("list_scheduled_tasks", {}), ("read_journal", {"lines": 50})]
    results = client.requests[1]["messages"][-1]
    assert results["role"] == "user" and [r["tool_use_id"] for r in results["content"]] == ["a", "b"]
    assert results["content"][1]["is_error"] is True
    assert conversation.messages[1]["content"][0].text == "Je regarde."  # Assistant content sent back unchanged.


def test_refusal_and_api_errors_remove_the_turn():
    client = FakeClient(reply(text("ok")), reply(stop="refusal"), RuntimeError("réseau"))
    conversation = A.Conversation(client, lambda name, data: ("", False))
    conversation.send("premier")
    kept = list(conversation.messages)
    assert conversation.send("second").stopped == "refusal"
    assert conversation.messages == kept
    with pytest.raises(RuntimeError):
        conversation.send("troisième")
    assert conversation.messages == kept


def test_cancel_answers_pending_tool_calls():
    flags = {"stop": False}
    def execute(name, data):
        flags["stop"] = True
        return "fait", False
    client = FakeClient(reply(call("list_profiles", {}, "x"), call("list_scheduled_tasks", {}, "y"), stop="tool_use"))
    conversation = A.Conversation(client, execute)
    result = conversation.send("go", cancelled=lambda: flags["stop"])
    assert result.stopped == "cancelled"
    last = conversation.messages[-1]["content"]
    assert [r["tool_use_id"] for r in last] == ["x", "y"] and last[1]["is_error"]  # Second call never ran.


def test_task_from_tool_maps_recurrences():
    base = dict(name="n", kind="sync", direction="distant_vers_local", local="C:\\a", remote="/b", directory=False,
                compare_content=False, conflict="newer", catch_up=True, enabled=True, time="", days=[], minutes=0, at="")
    task = A.task_from_tool(dict(base, recurrence_type="daily", time="23:00", days=["lundi", "vendredi"]))
    assert task["recurrence"] == {"type": "daily", "time": "23:00", "days": [0, 4]} and task["upload"] is False
    # Round trip through the vocabulary the model reads back.
    assert A.task_from_tool(dict(A.task_to_tool(task), name="n"))["recurrence"] == task["recurrence"]
    assert A.task_from_tool(dict(base, recurrence_type="daily", time="07:00"))["recurrence"]["days"] == list(range(7))
    assert A.task_from_tool(dict(base, recurrence_type="interval", minutes=30))["recurrence"] == {"type": "interval", "minutes": 30}


# ---------- tools in the window ----------

@pytest.fixture
def window(monkeypatch, tmp_path):
    QApplication.instance() or QApplication([])
    import smftp.utils.profiles as profiles
    from smftp.ui.main_window import MainWindow
    win = MainWindow()
    site = profiles.normalize_profile(dict(host="127.0.0.1", user="test", protocol="FTP", port=21,
                                           name="Production", remember=True))
    win.sites = [site]
    monkeypatch.setattr(profiles, "read_credential", lambda key: "secret")
    yield win
    close_window(win)


def task_input(tmp_path, **changes):
    data = dict(name="Site chaque soir", profile="Production", kind="sync", direction="local_vers_distant",
                local=str(tmp_path), remote="/www", directory=False, compare_content=False, conflict="newer",
                recurrence_type="daily", time="23:00", days=["lundi", "mardi", "mercredi", "jeudi", "vendredi"],
                minutes=0, at="", catch_up=True, enabled=True)
    data.update(changes)
    return data


def test_every_tool_has_an_implementation(window):
    for tool in A.TOOLS:
        assert hasattr(window.assistant_panel.toolbox, "tool_" + tool["name"])


def test_profiles_never_include_secrets_and_unknown_names_are_explained(window):
    toolbox = window.assistant_panel.toolbox
    output, error = toolbox.execute("list_profiles", {})
    assert not error and "Production" in output and "secret" not in output and '"mot_de_passe_memorise": true' in output
    output, error = toolbox.execute("list_remote_directory", {"profile": "Inconnu", "path": "/"})
    assert error and "Production" in output


def test_create_task_needs_confirmation(window, tmp_path, monkeypatch):
    toolbox = window.assistant_panel.toolbox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    output, error = toolbox.execute("create_scheduled_task", task_input(tmp_path))
    assert not error and "refusé" in output and not window.scheduler.store.tasks
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    output, error = toolbox.execute("create_scheduled_task", task_input(tmp_path))
    assert not error and "Tâche créée" in output
    task = window.scheduler.store.tasks[0]
    assert task["name"] == "Site chaque soir" and S.describe(task) == "En semaine à 23:00"
    listed, _ = toolbox.execute("list_scheduled_tasks", {})
    assert task["id"] in listed
    output, error = toolbox.execute("update_scheduled_task", dict(task_input(tmp_path, recurrence_type="interval",
                                                                             minutes=60), task_id=task["id"]))
    assert not error and window.scheduler.store.tasks[0]["recurrence"] == {"type": "interval", "minutes": 60}
    output, error = toolbox.execute("delete_scheduled_task", {"task_id": task["id"]})
    assert not error and not window.scheduler.store.tasks


def test_invalid_task_is_reported_before_any_confirmation(window, tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.Yes)
    output, error = window.assistant_panel.toolbox.execute("create_scheduled_task", task_input(tmp_path, remote="www"))
    assert error and "distant" in output and not asked


def test_remote_listing_against_a_real_server(window, ftp_server):
    import smftp.utils.profiles as profiles
    port, _ = ftp_server
    window.sites = [profiles.normalize_profile(dict(host="127.0.0.1", user="test", protocol="FTP", port=port,
                                                    name="Local", remember=True))]
    output, error = window.assistant_panel.toolbox.execute("list_remote_directory", {"profile": "Local", "path": "/"})
    assert not error and "bonjour.txt" in output and "dossier/" in output


def test_journal_and_queue_status(window):
    window.log("Erreur : 530 Login incorrect.")
    output, _ = window.assistant_panel.toolbox.execute("read_journal", {"lines": 20})
    assert "530" in output
    output, _ = window.assistant_panel.toolbox.execute("get_transfer_status", {})
    assert "vide" in output


# ---------- chat tab ----------

def wait_for(condition, seconds=10):
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    assert condition()


def test_chat_tab_runs_tools_from_the_worker_thread(window, tmp_path):
    panel = window.assistant_panel
    window.settings.setValue("assistant_consent", True)
    client = FakeClient(reply(call("create_scheduled_task", task_input(tmp_path)), stop="tool_use"),
                        reply(text("C’est planifié : chaque soir de semaine à 23 h.")))
    panel.client_factory = lambda: client
    panel.input.setText("Synchronise mon site chaque soir à 23 h")
    assert panel.send_button.isEnabled()
    panel.send()
    assert panel.busy() and panel.input.isReadOnly() and panel.send_button.toolTip().startswith("Arrêter")
    wait_for(lambda: not panel.busy())
    QApplication.instance().processEvents()
    from smftp.ui.assistant import Bubble
    texts = [bubble.label.text() for bubble in panel.findChildren(Bubble)]
    assert texts[0] == "Synchronise mon site chaque soir à 23 h" and "C’est planifié" in texts[-1]
    steps = [label.text() for label in panel.findChildren(QLabel) if label.text().startswith("✓ ")]
    assert steps == ["✓ Propose une nouvelle tâche planifiée"]
    assert window.scheduler.store.tasks and window.scheduler.store.tasks[0]["name"] == "Site chaque soir"
    assert "Date et heure locales" in client.requests[0]["messages"][0]["content"][0]["text"]
    panel.reset()
    assert not panel.conversation.messages and panel.empty.isVisibleTo(panel)


def test_declined_consent_sends_nothing(window, monkeypatch):
    panel = window.assistant_panel
    window.settings.setValue("assistant_consent", False)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    client = FakeClient()
    panel.client_factory = lambda: client
    panel.input.setText("Bonjour")
    panel.send()
    assert not panel.busy() and not client.requests and panel.conversation is None
