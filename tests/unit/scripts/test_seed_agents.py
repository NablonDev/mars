"""Regression test for scripts/seed/seed_agents.py's D3 interpolation-boundary
guard (see the approved Phase 1 plan's D3 and the module docstring).

The guard used to be a bare `assert`, silently stripped under `python -O`,
which would let a stray `{`-carrying prompt reach `process.agent.system_prompt`
-- a system-level instruction store, not somewhere interpolation-placeholder
text belongs. It now raises `ValueError` unconditionally instead.
"""

from scripts.seed import seed_agents


def test_cmir_extractor_system_prompt_has_no_interpolation_placeholder():
    """The seeded, static portion of the CMIR extractor's prompt must not
    contain the trailing `Email:\\n\\n{body}` interpolation slot -- that
    slot carries user-controlled email content and must remain a per-call
    user-message interpolation, never stored system-prompt content."""
    assert "{" not in seed_agents.CMIR_EXTRACTOR_SYSTEM_PROMPT


def test_agent_seeds_cover_all_six_rows():
    """Guards against a silent drop of one of the six documented seed rows."""
    keys = {(seed.agent_code, seed.prompt_version) for seed in seed_agents.AGENT_SEEDS}
    assert keys == {
        ("penalty_projection_summary", "v1"),
        ("penalty_projection_summary", "v2"),
        ("penalty_projection_summary", "v3"),
        ("penalty_mitigation_summary", "v1"),
        ("cmir_extractor", "v1"),
        ("po_validation", "v1"),
    }


def test_all_agent_seeds_use_a_valid_domain():
    """`process.agent.domain` is restricted by `ck_agent_domain` to
    ('cmir', 'penalties') -- a seed row with any other value would fail to
    apply against a real Postgres database."""
    assert {seed.domain for seed in seed_agents.AGENT_SEEDS} <= {"cmir", "penalties"}
