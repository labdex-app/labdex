"""labdex versions: MAJOR.MINOR.SUB, and MAJOR.MINOR.SUB-N for the N-th hotfix of that release.

    1.9.0      a release
    1.9.0-1    its first hotfix (bug fixes only; comes after 1.9.0)
    1.10.0     the next release

The hotfix suffix is what Python calls a post-release (1.9.0-1 == 1.9.0.post1), so plugin
ranges like ">=1.9,<2" and every comparison in labdex order them correctly. (In SemVer a
"-1" would be a pre-release; labdex never uses it that way.)
"""
from __future__ import annotations

import re
from typing import NamedTuple

RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(\d+))?$")


class Version(NamedTuple):
    major: int
    minor: int
    sub: int
    patch: int = 0

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.sub}"
        return f"{base}-{self.patch}" if self.patch else base

    @property
    def release(self) -> str:
        """The release this is (a hotfix belongs to): 1.9.0-2 → 1.9.0."""
        return f"{self.major}.{self.minor}.{self.sub}"

    @property
    def pep440(self) -> str:
        return f"{self.release}.post{self.patch}" if self.patch else self.release

    def next(self, part: str) -> Version:
        """The version after this one: part is major, minor, sub or patch."""
        if part == "major":
            return Version(self.major + 1, 0, 0)
        if part == "minor":
            return Version(self.major, self.minor + 1, 0)
        if part == "sub":
            return Version(self.major, self.minor, self.sub + 1)
        if part == "patch":
            return Version(self.major, self.minor, self.sub, self.patch + 1)
        raise ValueError(f"part is major, minor, sub or patch, not {part!r}")


def parse(text: str) -> Version:
    m = RE.match(str(text).strip().removeprefix("v"))
    if not m:
        raise ValueError(f"{text!r} isn't a labdex version (MAJOR.MINOR.SUB or MAJOR.MINOR.SUB-N)")
    return Version(*(int(g) if g else 0 for g in m.groups()))


def docker_tags(text: str) -> list[str]:
    """The image tags a release gets: 1.9.0-1 → 1.9.0-1, 1.9.0, 1.9, 1, latest. A hotfix moves
    the tags of its release along, since it replaces it."""
    v = parse(text)
    tags = [str(v), v.release, f"{v.major}.{v.minor}", str(v.major), "latest"]
    return list(dict.fromkeys(tags))
