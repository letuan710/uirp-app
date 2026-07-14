"""Pipeline: Evidence → Observation → Claim/Entity/Relationship."""

from __future__ import annotations

from uirp.ai.adapter import AIClient
from uirp.config import Config
from uirp.core import jobs
from uirp.pipeline import extract, parse


def register_all(cfg: Config) -> None:
    """Đăng ký handler pipeline vào scheduler. AIClient chọn backend theo config."""
    client = AIClient(cfg)
    jobs.register("parse", parse.make_handler(client))
    jobs.register("extract", extract.make_handler(client))
