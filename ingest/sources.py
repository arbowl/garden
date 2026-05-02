import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Source:
    name: str
    url: str
    type: str
    tags: list[str] = field(default_factory=list)
    trust_level: float = 0.5


def load_sources(path: str) -> list[Source]:
    data = tomllib.loads(Path(path).read_text())
    return [
        Source(
            name=s["name"],
            url=s["url"],
            type=s.get("type", "rss"),
            tags=s.get("tags", []),
            trust_level=s.get("trust_level", 0.5),
        )
        for s in data.get("sources", [])
    ]


def _qs(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_sources(path: str, sources: list[Source]) -> None:
    blocks = []
    for s in sources:
        lines = ["[[sources]]"]
        lines.append(f"name = {_qs(s.name)}")
        lines.append(f"url = {_qs(s.url)}")
        lines.append(f'type = "{s.type}"')
        if s.tags:
            tags = "[" + ", ".join(f'"{t}"' for t in s.tags) + "]"
            lines.append(f"tags = {tags}")
        lines.append(f"trust_level = {s.trust_level}")
        blocks.append("\n".join(lines))
    Path(path).write_text("\n\n".join(blocks) + "\n")
