"""Skipper handles routine approvals for the user and escalates only the important ones."""

import asyncio
import logging
from collections import Counter

from core import ACCESS, CURRENT_TASK, Access, Approve, Decision, HumanAnswer, Node, Proposal, Question, Said, jsonin, on, sh

log = logging.getLogger("fam.steward")

IMPORTANT = (
    "Important means any of: deleting or overwriting user data, touching credentials or secrets, pushing, deploying or publishing, "
    "spending money, weakening security or authentication, an irreversible action, or anything you are unsure about. "
)
JUDGE = (
    "You are Skipper, deciding on behalf of the user. The family reviewers disagree about this change. "
    "Accept it only if the changes clearly do what the task asks and nothing risky; reject it if they miss or break the task; "
    f"choose ask if the matter is important. {IMPORTANT}"
    'Reply only JSON: {"decision":"accept|reject|ask","reason":"one short sentence"}.'
)
ANSWER = (
    "You are Skipper, answering a family member's question on behalf of the user. "
    "Answer only when it is a permission, approval, confirmation or routine choice that follows from the task as written "
    "(for example: yes, proceed; use that folder; use the existing approach). "
    "Choose ask when it needs the user's own preference, personal data or credentials, or is important. "
    f"{IMPORTANT}"
    'Reply only JSON: {"decision":"answer|ask","answer":"what to tell the member","reason":"one short sentence"}.'
)


class Steward(Node):
    answers_per_task = 3

    def __init__(s, bus, agent, lead, linker, min_stars=100):
        super().__init__(bus)
        s.agent, s.lead, s.linker, s.min_stars = agent, lead, linker, min_stars
        s.answered, s.decided, s.jobs = Counter(), set(), set()

    def spawn(s, task_id, coro):
        async def run():
            CURRENT_TASK.set(task_id)
            try:
                await coro
            except Exception as e:  # noqa: BLE001 - the original prompt to the user stays pending
                log.warning("steward on %s failed: %s", task_id, e)

        job = asyncio.create_task(run())
        s.jobs.add(job)
        job.add_done_callback(s.jobs.discard)

    async def tell(s, text):
        await s.bus.emit(Said("Skipper", text))

    async def judge(s, prompt, cwd):
        token = ACCESS.set(Access((cwd,), readonly=True, untrusted=True))
        try:
            return jsonin(await s.agent.ask(prompt, cwd))
        finally:
            ACCESS.reset(token)

    def concern(s, prop):
        """Why a task-driven tool proposal should go to the user, or '' when it is routine."""
        stars = prop.get("stars")
        if stars is None or stars < s.min_stars:
            return "its popularity could not be verified"
        cfg = prop.get("cfg") or {}
        if cfg.get("env"):
            return "it needs credentials"
        if cfg.get("command") == "docker":
            return "it runs a container"
        return ""

    @on(Proposal)
    async def proposal(s, p):
        if p.need not in s.linker.asked:
            return
        for name in p.names:
            prop = s.linker.props.get(name)
            if not prop:
                continue
            if why := s.concern(prop):
                await s.tell(f"{name} could fit the task but {why}, so it is your call. Say “approve {name}” to enable it.")
                continue
            await s.bus.emit(Approve(name))
            await s.tell(f"I approved {name} for you. A task needed “{p.need}” and it is a well-known server ({prop['why']}).")
            return

    @on(Decision)
    async def decision(s, d):
        if d.escalate and d.task.id not in s.decided:
            s.decided.add(d.task.id)
            s.spawn(d.task.id, s.settle(d))

    async def settle(s, d):
        t = d.task
        await sh("git", "add", "-N", ".", cwd=t.cwd)
        _, diff = await sh("git", "diff", "HEAD", cwd=t.cwd)
        r = await s.judge(f"{JUDGE}\nTask: {t.text}\nDisagreement among reviewers: {d.spread:.2f}\nChanges:\n{diff[:8000]}", t.cwd)
        verdict, why = str(r.get("decision", "ask")).lower(), str(r.get("reason", ""))[:300]
        if verdict not in ("accept", "reject"):
            await s.tell(f"Task {t.id} needs your decision: {why or 'it is important or unclear'}. Send “accept task {t.id}” or “reject task {t.id}”.")
            return
        await s.tell(f"The family was split on task {t.id}, so I {verdict}ed it for you: {why}")
        await s.lead.resolve(t, verdict == "accept")

    @on(Question)
    async def question(s, q):
        if s.answered[q.task.id] < s.answers_per_task:
            s.answered[q.task.id] += 1
            s.spawn(q.task.id, s.reply(q))

    async def reply(s, q):
        r = await s.judge(f"{ANSWER}\nTask: {q.task.text}\nQuestion from {q.member}: {q.text}", q.task.cwd)
        answer, why = str(r.get("answer", "")).strip(), str(r.get("reason", ""))[:300]
        if str(r.get("decision", "ask")).lower() != "answer" or not answer:
            await s.tell(f"{q.member}'s question on task {q.task.id} is yours to answer: {why or 'it needs your input'}.")
            return
        await s.tell(f"I answered {q.member} on task {q.task.id} for you: {answer}")
        reply = f"(Skipper, on the user's behalf) {answer}"
        await s.bus.emit(HumanAnswer(q.task, reply))
        await s.lead.answer(q.task, q.member, reply)
