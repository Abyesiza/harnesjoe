import asyncio
from collections import defaultdict
from types import SimpleNamespace

from agents import Persona
from core import Bus, HumanAnswer, Node, Question, Task, on
from skipper import Skipper, StateStore


class Planner:
    def __init__(self, plan):
        self.plan = plan

    async def ask_json(self, *args, **kwargs):
        return self.plan


class Member:
    def __init__(self, name, mission="Build software."):
        self.persona = Persona(name, mission)
        self.ledger = SimpleNamespace(cap=5, spent=0)

    async def ask(self, prompt, cwd):
        return "Here to help."


def app(tmp_path):
    members = {"spark": Member("Spark"), "sage": Member("Sage")}
    lead = SimpleNamespace(
        active=defaultdict(set),
        completed=defaultdict(int),
        reward=defaultdict(float),
        tasks={},
        members={"spark": members["spark"]},
        root=tmp_path,
        select=lambda text: "spark",
    )
    return SimpleNamespace(
        cast=members,
        lead=lead,
        skipper=Planner({"intent": "task", "task": "implement feature", "member": "spark"}),
        linker=SimpleNamespace(props={}, trusted={}),
        bus=None,
    )


def test_chat_assigns_tasks_and_persists_conversation(tmp_path):
    state, site, assigned = StateStore(tmp_path / "state.sqlite3"), app(tmp_path), []
    skipper = Skipper(state, site.skipper)

    result = asyncio.run(
        skipper.chat("Build the feature", str(tmp_path), site, assigned.append)
    )

    assert result["member"] == "spark" and result["task"]
    assert isinstance(assigned[0], Task) and assigned[0].text == "implement feature"
    assert [m["role"] for m in state.messages()] == ["user", "skipper"]
    assert StateStore(tmp_path / "state.sqlite3").messages()[1]["task"] == result["task"]


def test_status_is_reported_without_model_call(tmp_path):
    site = app(tmp_path)
    site.lead.active["spark"].add("t1")
    site.lead.completed["spark"] = 4
    site.lead.reward["spark"] = 3
    state = StateStore(tmp_path / "state.sqlite3")
    result = asyncio.run(Skipper(state, site.skipper).chat("status", str(tmp_path), site, lambda _: None))

    assert "Spark" in result["reply"] and "75%" in result["reply"] and "1 active task" in result["reply"]


def test_status_ignores_error_events_with_string_task_ids(tmp_path):
    site = app(tmp_path)
    state = StateStore(tmp_path / "state.sqlite3")
    state.event("Error", {"task": "legacy-5", "message": "Research was interrupted"})

    result = asyncio.run(
        Skipper(state, site.skipper).chat("status", str(tmp_path), site, lambda _: None)
    )

    assert "I’m watching 0 active task(s)." in result["reply"]
    assert not result.get("error")


def test_capabilities_question_is_answered_without_assigning_a_task(tmp_path):
    site = app(tmp_path)
    state = StateStore(tmp_path / "state.sqlite3")
    result = asyncio.run(Skipper(state).chat(
        "Tell me the capabilities of the family", str(tmp_path), site, lambda _: None
    ))

    assert "Spark: Build software" in result["reply"]
    assert "Sage: Build software" in result["reply"]
    assert not result.get("task")


def test_research_progress_is_visible_until_findings_arrive(tmp_path):
    site = app(tmp_path)
    state = StateStore(tmp_path / "state.sqlite3")
    state.event("ResearchRequest", {"id": "r1", "question": "UI design"})
    skipper = Skipper(state)

    assert skipper.health(site)["running_tasks"] == 1
    assert skipper.health(site)["research"][0]["status"] == "running"
    state.event("Finding", {"id": "r1", "question": "UI design", "say": "Findings ready"})
    assert skipper.health(site)["running_tasks"] == 0
    assert skipper.health(site)["research"][0]["status"] == "completed"
    assert skipper.pending_research(state.events()) == []


def test_question_about_previous_quill_findings_returns_research_summary(tmp_path):
    site = app(tmp_path)
    state = StateStore(tmp_path / "state.sqlite3")
    state.event("ResearchRequest", {"id": "r-prev", "question": "Web UI UX"})
    state.event(
        "Finding",
        {"id": "r-prev", "question": "Web UI UX", "say": "Use clear hierarchy and accessible contrast."},
    )
    assigned = []

    result = asyncio.run(
        Skipper(state, site.skipper).chat(
            "what did quill find out from the previous research",
            str(tmp_path),
            site,
            assigned.append,
        )
    )

    assert "Use clear hierarchy and accessible contrast." in result["reply"]
    assert not result.get("task")
    assert not assigned


def test_legacy_research_events_without_ids_recover_and_complete(tmp_path):
    site = app(tmp_path)
    state = StateStore(tmp_path / "state.sqlite3")
    state.event("ResearchRequest", {"question": "UI design"})
    skipper = Skipper(state)

    assert skipper.pending_research(state.events()) == [
        {"question": "UI design", "id": "legacy-1"}
    ]
    assert skipper.health(site)["research"][0]["id"] == "legacy-1"

    state.event("Finding", {"question": "UI design", "say": "Findings ready"})
    assert skipper.pending_research(state.events()) == []
    assert skipper.health(site)["research"][0]["status"] == "completed"


def test_folder_access_requires_approval_and_is_limited_to_granted_subtree(tmp_path):
    parent = tmp_path / "approved"
    child = parent / "project"
    sibling = tmp_path / "private"
    child.mkdir(parents=True)
    sibling.mkdir()
    state = StateStore(tmp_path / "state.sqlite3")
    skipper = Skipper(state)
    request = state.request_folder(str(parent))

    assert not skipper.authorized(child)
    state.approve_folder(request["id"], str(parent))
    assert skipper.authorized(child)
    assert not skipper.authorized(sibling)


def test_member_can_reply_directly_without_assigning_work(tmp_path):
    site = app(tmp_path)
    state = StateStore(tmp_path / "state.sqlite3")
    called = []
    result = asyncio.run(Skipper(state).chat("talk to Sage: how are you?", str(tmp_path), site, called.append))

    assert result["reply"] == "Sage: Here to help."
    assert not called and state.messages()[-1]["role"] == "member"


def test_unknown_intent_fails_loudly_and_is_not_reported_as_done(tmp_path):
    site = app(tmp_path)
    site.skipper = Planner({"intent": "unexpected", "reply": "Done"})
    state = StateStore(tmp_path / "state.sqlite3")
    try:
        asyncio.run(Skipper(state, site.skipper).chat("do a thing", str(tmp_path), site, lambda _: None))
    except ValueError as exc:
        assert "safe next step" in str(exc)
    else:
        raise AssertionError("an unknown planner intent should not be treated as success")


def test_events_persist_and_recovery_only_reports_unfinished_tasks(tmp_path):
    state = StateStore(tmp_path / "state.sqlite3")
    task = Task("unfinished")
    state.event("Task", task)
    state.event("Task", Task("finished", id="finished"))
    state.event("Decision", {"task": {"id": "finished"}})
    state.event("Error", {"task": task.id, "message": "Work was interrupted by a Skipper restart. preserved."})

    restored = StateStore(tmp_path / "state.sqlite3")
    events = restored.events()
    assert Skipper.interrupted_tasks(events) == [task.id]
    assert events[0]["data"]["id"] == task.id


def test_recovery_marks_unfinished_tasks_once(tmp_path):
    state = StateStore(tmp_path / "state.sqlite3")
    state.event("Task", Task("unfinished", id="u1"))
    events = state.events()
    assert Skipper.interrupted_tasks(events) == ["u1"]
    state.event("Error", {"task": "u1", "message": "Work was interrupted by a Skipper restart. preserved."})
    assert Skipper.interrupted_tasks(state.events()) == ["u1"]


def test_escalated_decision_waits_for_human_resolution(tmp_path):
    task = Task("needs a call", id="t1")
    events = [{"type": "Decision", "data": {"task": {"id": task.id}, "escalate": True}}]
    assert Skipper.pending_decisions(events) == [events[0]["data"]["task"]]
    events.append({"type": "HumanDecision", "data": {"task": {"id": task.id}, "accept": False}})
    assert Skipper.pending_decisions(events) == []


def test_accepted_work_is_reconciled_after_restart(tmp_path):
    task = {"id": "t1", "text": "feature", "cwd": "/repo/work/t1", "repo": "/repo"}
    events = [{"type": "Decision", "data": {"task": task, "accept": True}}]
    assert Skipper.pending_apply(events) == [task]
    events.append({"type": "Applied", "data": {"task": task}})
    assert Skipper.pending_apply(events) == []


def test_clean_repository_retry_tracks_accepted_changes_waiting_to_apply(tmp_path):
    task = Task("feature", id="t1")
    events = [
        {"type": "ApplyPending", "data": {"task": {"id": task.id, "cwd": "/work/t1", "repo": "/repo"}}}
    ]
    assert "t1" in Skipper.pending_local_apply(events)
    events.append({"type": "Applied", "data": {"task": {"id": task.id}}})
    assert Skipper.pending_local_apply(events) == {}


def test_escalated_work_waiting_on_user_is_not_misreported_as_interrupted(tmp_path):
    task = {"id": "t1", "text": "feature"}
    events = [
        {"type": "Task", "data": task},
        {"type": "Decision", "data": {"task": task, "escalate": True}},
    ]
    assert Skipper.interrupted_tasks(events) == []
    assert Skipper.pending_decisions(events) == [task]


def test_family_questions_wait_for_an_answer_and_then_clear(tmp_path):
    state = StateStore(tmp_path / "state.sqlite3")
    task = Task("configure database", id="t1")
    state.event("Task", task)
    state.event("Question", Question(task, "spark", "Which database?"))
    events = state.events()
    assert Skipper.pending_questions(events)[0]["text"] == "Which database?"
    assert Skipper.interrupted_tasks(events) == []
    state.event("HumanAnswer", HumanAnswer(task, "Postgres"))
    assert Skipper.pending_questions(state.events()) == []


def test_chat_routes_a_user_answer_back_to_the_asking_member(tmp_path):
    site = app(tmp_path)
    task = Task("configure database", id="t1")
    state = StateStore(tmp_path / "state.sqlite3")
    state.event("Task", task)
    state.event("Question", Question(task, "spark", "Which database?"))
    site.bus = Bus()
    site.lead.answers = []

    class PersistAnswer(Node):
        @on(HumanAnswer)
        async def save(s, event):
            state.event("HumanAnswer", event)

    PersistAnswer(site.bus)

    async def answer(self, work, member, response):
        self.answers.append((work.id, member, response))

    site.lead.answer = answer.__get__(site.lead)
    site.skipper = Planner({"intent": "answer_question", "task": "Postgres", "member": "spark"})

    async def run():
        result = await Skipper(state, site.skipper).chat(
            "Postgres", str(tmp_path), site,
            lambda coroutine, task_id="": asyncio.create_task(coroutine),
        )
        await asyncio.sleep(0.01)
        return result

    result = asyncio.run(run())
    assert "passed your answer" in result["reply"]
    assert site.lead.answers == [("t1", "spark", "Postgres")]
    assert Skipper.pending_questions(state.events()) == []
