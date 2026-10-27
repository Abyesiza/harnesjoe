"""Read-only research web access: what the allowlist permits (Arrange / Act / Assert)."""

from agents import ClaudeCode
from core import Access


def _allowed(access):
    args = ClaudeCode.access_args(None, access)
    return args[args.index("--allowedTools") + 1:args.index("--disallowedTools")]


def test_research_allows_web_search_fetch_and_gh_reads():
    allowed = _allowed(Access(readonly=True))
    assert {"WebSearch", "WebFetch", "Bash(gh search:*)", "Bash(gh repo view:*)", "Bash(gh release view:*)"} <= set(allowed)


def test_untrusted_review_withholds_all_web_access():
    allowed = _allowed(Access(readonly=True, untrusted=True))
    assert not [a for a in allowed if "gh" in a or a.startswith("Web")]


def test_research_does_not_allow_gh_api_wholesale():
    # `gh api -X POST ...` matches the prefix rule Bash(gh api:*), so a blanket entry permits writes.
    assert "Bash(gh api:*)" not in _allowed(Access(readonly=True))
