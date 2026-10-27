"""Skipper approves routine matters for the user and escalates important ones (Arrange / Act / Assert)."""

import asyncio

from core import Approve, Bus, Decision, HumanAnswer, Proposal, Question, Said, Task, on, Node
from steward import Steward


class FakeAgent:
    def __init__(self, reply):
        self.reply = reply

    async def ask(self, prompt, cwd=".", mcp=None):
        return self.reply


class FakeLead:
    def __init__(self):
        self.answers, self.resolved = [], []

    async def answer(self, task, member, answer):
        self.answers.append((task.id, member, answer))

    async def resolve(self, task, accept):
        self.resolved.append((task.id, accept))


class FakeLinker:
    def __init__(self, props, asked):
        self.props, self.asked = props, set(asked)


class Spy(Node):
    def __init__(self, bus):
        super().__init__(bus)
        self.said, self.approved, self.answered = [], [], []

    @on(Said)
    async def said(self, e):
        self.said.append(e.text)

    @on(Approve)
    async def approve(self, e):
        self.approved.append(e.name)

    @on(HumanAnswer)
    async def answer(self, e):
        self.answered.append(e.answer)


def setup(reply, props=None, asked=()):
    bus = Bus()
    lead = FakeLead()
    steward = Steward(bus, FakeAgent(reply), lead, FakeLinker(props or {}, asked))
    return bus, steward, lead, Spy(bus)


async def settle(steward):
    await asyncio.gather(*steward.jobs)


def prop(stars=500, env=None):
    return {"stars": stars, "why": "relevance 0.9", "cfg": {"type": "stdio", "command": "npx", **({"env": env} if env else {})}}


def test_well_known_task_driven_proposal_is_approved_for_the_user():
    bus, _, _, spy = setup("", {"a/x": prop()}, ["db"])
    asyncio.run(bus.emit(Proposal("db", ["a/x"])))
    assert spy.approved == ["a/x"] and "approved a/x for you" in spy.said[0]


def test_credentialed_or_unverified_proposals_go_to_the_user():
    bus, _, _, spy = setup("", {"a/x": prop(env={"K": "v"}), "a/y": prop(stars=None)}, ["db"])
    asyncio.run(bus.emit(Proposal("db", ["a/x", "a/y"])))
    assert spy.approved == [] and len(spy.said) == 2 and "your call" in spy.said[0]


def test_proposals_nobody_asked_for_are_left_alone():
    bus, _, _, spy = setup("", {"a/x": prop()}, [])
    asyncio.run(bus.emit(Proposal("db", ["a/x"])))
    assert spy.approved == [] and spy.said == []


def test_routine_question_is_answered_for_the_user_and_work_continues():
    bus, steward, lead, spy = setup('{"decision":"answer","answer":"Yes, use that folder.","reason":"matches the task"}')
    task = Task("build ui", cwd="/tmp")

    async def go():
        await bus.emit(Question(task, "ember", "May I use the work folder?"))
        await settle(steward)

    asyncio.run(go())
    assert lead.answers and "Yes, use that folder." in lead.answers[0][2] and spy.answered


def test_important_question_is_left_to_the_user():
    bus, steward, lead, spy = setup('{"decision":"ask","answer":"","reason":"it would delete data"}')

    async def go():
        await bus.emit(Question(Task("t", cwd="/tmp"), "ember", "Delete the database?"))
        await settle(steward)

    asyncio.run(go())
    assert not lead.answers and "yours to answer" in spy.said[-1]


def test_split_review_is_settled_by_skipper_unless_important(tmp_path):
    bus, steward, lead, spy = setup('{"decision":"accept","reason":"does what was asked"}')
    task = Task("small fix", cwd=str(tmp_path))

    async def go():
        await bus.emit(Decision(task, "ember", False, True, 0.4))
        await settle(steward)

    asyncio.run(go())
    assert lead.resolved == [(task.id, True)] and "accepted it for you" in spy.said[-1]


def test_important_split_review_is_escalated(tmp_path):
    bus, steward, lead, spy = setup('{"decision":"ask","reason":"touches credentials"}')
    task = Task("rotate keys", cwd=str(tmp_path))

    async def go():
        await bus.emit(Decision(task, "ember", False, True, 0.4))
        await settle(steward)

    asyncio.run(go())
    assert lead.resolved == [] and "needs your decision" in spy.said[-1]


def test_consult_runs_members_in_parallel_and_reports_each_finding(tmp_path):
    from types import SimpleNamespace

    from core import Consulted
    from skipper import Skipper

    class Persona:
        def __init__(self, name):
            self.name = name

    class Member(FakeAgent):
        def __init__(self, name, reply):
            super().__init__(reply)
            self.persona = Persona(name)

    bus, got = Bus(), []

    class Spy2(Node):
        @on(Consulted)
        async def seen(self, e):
            got.append((e.member, e.findings))

    Spy2(bus)
    app = SimpleNamespace(bus=bus, lead=SimpleNamespace(tasks={"abcd1234": "build ui"}, root=tmp_path), cast={"vera": Member("Vera", "tests pass"), "dash": Member("Dash", "fast enough")})
    skipper = Skipper(None)
    keys, cwd, found = skipper.consult_plan(app, "Vera, dash", str(tmp_path), "abcd1234")
    asyncio.run(skipper.consult(app, keys, "verify", cwd, found))
    assert keys == ["vera", "dash"] and sorted(got) == [("dash", "fast enough"), ("vera", "tests pass")]


def test_task_board_reports_each_task_and_where_it_stands(tmp_path):
    from collections import defaultdict
    from types import SimpleNamespace

    from skipper import Skipper, StateStore

    store = StateStore(tmp_path / "s.sqlite3")
    task = {"id": "9e1696b0", "text": "Next.js UI", "cwd": "/w"}
    store.event("Task", task)
    store.event("Assigned", {"task": task, "member": "ember", "reason": "x"})
    store.event("Decision", {"task": task, "member": "ember", "accept": False, "escalate": True, "spread": 0.4})
    app = SimpleNamespace(lead=SimpleNamespace(active=defaultdict(set)))
    board = Skipper(store).task_board(app)
    assert board == [{"id": "9e1696b0", "task": "Next.js UI", "member": "ember", "state": "waiting for your decision"}]
    app.lead.active["ember"].add("9e1696b0")
    assert Skipper(store).task_board(app)[0]["state"] == "working"


def test_move_command_selects_and_grants_a_folder(tmp_path):
    from skipper import Skipper, StateStore

    target = tmp_path / "proj"
    target.mkdir()
    (target / "a.txt").write_text("x")
    store = StateStore(tmp_path / "s.sqlite3")
    result = asyncio.run(Skipper(store).chat(f"move to {target}", str(tmp_path), None, lambda *_: None))
    assert result["repo"] == str(target) and "a.txt" in result["reply"] and str(target) in store.approved_folders()
