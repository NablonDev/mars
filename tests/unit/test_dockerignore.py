"""The Dockerfile uses `COPY . .`, so .dockerignore is the only thing keeping
secrets out of the image. It has silently been replaced with unrelated content
before; these tests fail loudly if that happens again.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
DOCKERFILE = REPO_ROOT / "Dockerfile"


def _patterns() -> set[str]:
    lines = DOCKERIGNORE.read_text().splitlines()
    return {line.strip() for line in lines if line.strip() and not line.strip().startswith("#")}


@pytest.mark.parametrize("pattern", [".env", ".env.*", ".git", ".venv/"])
def test_dockerignore_excludes_secrets_and_vcs(pattern):
    assert pattern in _patterns(), (
        f"{pattern!r} missing from .dockerignore. The Dockerfile uses `COPY . .`, "
        f"so anything not listed here is baked into the image."
    )


def test_dockerignore_is_not_a_dockerfile():
    """It was once overwritten with Dockerfile content, which parses as a set of
    meaningless patterns rather than failing, so the leak is silent."""
    directives = {"FROM", "RUN", "CMD", "ENTRYPOINT", "WORKDIR", "EXPOSE", "HEALTHCHECK"}
    found = {p.split()[0] for p in _patterns() if p.split()[0] in directives}
    assert not found, f".dockerignore contains Dockerfile directives {sorted(found)}"


def test_dockerfile_has_no_unfilled_placeholders():
    content = DOCKERFILE.read_text()
    assert "<PINNED" not in content and "<YOUR" not in content, (
        "Dockerfile contains an unfilled <PLACEHOLDER>; the build fails at that line."
    )
