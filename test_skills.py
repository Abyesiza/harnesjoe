"""Skills are found, offered to builders that fit, and counted (Arrange / Act / Assert)."""

import json

import skills
from agents import ClaudeCode, Persona


def add(root, folder, name, desc):
    d = root / "skills" / folder
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\nsteps\n")


def test_catalog_reads_name_and_description(tmp_path):
    add(tmp_path, "mine/css", "css-grid", "Lay out responsive pages with CSS grid")
    assert [(s["name"], s["source"]) for s in skills.catalog(tmp_path)] == [("css-grid", "mine")]


def test_builder_prompt_offers_only_fitting_skills_and_counts_them(tmp_path):
    add(tmp_path, "mine/css", "css-grid", "Lay out responsive pages with CSS grid")
    add(tmp_path, "mine/db", "sql-migrations", "Write safe database migrations")
    agent = ClaudeCode(Persona("Ember", "build"), None)
    agent.skills_root = tmp_path
    prompt = agent.prompt("lay out responsive pages with CSS grid")
    assert "css-grid" in prompt and "sql-migrations" not in prompt
    usage = json.loads((tmp_path / "skills" / "usage.json").read_text())
    assert list(usage.values()) == [1]
    assert "offered 1 time" in skills.report(tmp_path)


def test_every_member_gets_skills_even_when_the_task_trails_long_instructions(tmp_path):
    add(tmp_path, "mine/papers", "paper-search", "Search academic papers and cite sources")
    quill = ClaudeCode(Persona("Quill", "research"), None)
    quill.skills_root = tmp_path
    prompt = quill.prompt("Primary sources first, two independent sources per claim. " * 40 + "\nQuestion: search academic papers and cite sources")
    assert "paper-search" in prompt


def test_members_without_skills_root_get_a_plain_prompt(tmp_path):
    add(tmp_path, "mine/css", "css-grid", "Lay out responsive pages with CSS grid")
    assert "Skills that may help" not in ClaudeCode(Persona("Ember", "build"), None).prompt("css grid pages")


def test_scout_report_says_when_nothing_was_kept():
    import reports

    text = reports.scouted({"repo": "o/n", "score": 0.55, "skills": ["a"], "lessons": ["b"], "say": "ok", "saved": False})
    assert "none were kept" in text and "saved locally" not in text
