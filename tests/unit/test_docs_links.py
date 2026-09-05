"""Every relative link in the documentation resolves to something that exists.

The cheap half of "the docs are not broken". It cannot tell whether a page says anything
true, but it does catch the failure that actually happens: a page is renamed or split and
four links elsewhere go on pointing at where it used to be.

External links are not followed. A test that needs the network is a test that fails on a
train, and this project's own constitution puts an explicit timeout on anything that leaves
the machine — a link checker would have neither.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: Every page a reader can arrive at, and the two entry points into them.
PAGES = sorted(
    [
        REPO / "README.md",
        REPO / "CLAUDE.md",
        REPO / "docs" / "index.md",
        *(REPO / "docs" / "guide").glob("*.md"),
    ]
)

#: ``[text](target)``, skipping image embeds, which this project has none of but which
#: would otherwise be resolved as pages.
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")


def relative_links(page: Path) -> list[str]:
    """The link targets worth checking: not external, not a bare anchor, not a mailto."""
    found = []
    for target in LINK.findall(page.read_text(encoding="utf-8")):
        target = target.split(" ", 1)[0].strip()
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        found.append(target)
    return found


def test_there_are_pages_to_check():
    """Guards the test itself: a glob that matched nothing would pass silently."""
    assert len(PAGES) >= 10, f"only found {[p.name for p in PAGES]}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(REPO)))
def test_every_relative_link_resolves(page):
    """A link to a file that does not exist, named with both ends of the problem."""
    broken = []
    for target in relative_links(page):
        # Strip a fragment: the file half is what the filesystem can answer for. Checking
        # anchors would mean parsing every heading of every target and agreeing with
        # GitHub's slug rules, which is more machinery than the failure justifies.
        path = (page.parent / target.split("#", 1)[0]).resolve()
        if not path.exists():
            broken.append(target)
    assert not broken, f"{page.relative_to(REPO)} links to {broken}, which do not exist"


def test_the_guide_index_reaches_every_guide_page():
    """A page nobody links to is a page nobody finds (SC-004)."""
    index = REPO / "docs" / "guide" / "index.md"
    linked = {t.split("#", 1)[0] for t in relative_links(index)}
    for page in sorted((REPO / "docs" / "guide").glob("*.md")):
        if page.name == "index.md":
            continue
        assert page.name in linked, (
            f"docs/guide/{page.name} exists but the guide index does not link to it"
        )


def test_the_readme_points_at_the_published_guide():
    """FR-005: the README's job is to be short and to point somewhere."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "jantman.github.io/robot-army" in readme


def test_the_readme_stays_short():
    """It was 1,180 lines, which is the problem this feature exists to fix."""
    lines = (REPO / "README.md").read_text(encoding="utf-8").splitlines()
    assert len(lines) < 150, f"README.md is {len(lines)} lines; it is meant to be an overview"


def test_the_project_history_is_excluded_from_the_published_site():
    """FR-007: it stays in git, and stays out of the guide's navigation."""
    config = (REPO / "docs" / "_config.yml").read_text(encoding="utf-8")
    for name in ("roadmap.md", "initial-planning/"):
        assert name in config, f"docs/_config.yml does not exclude {name} from the site"
    assert (REPO / "docs" / "roadmap.md").exists(), "the roadmap must stay in the repository"
