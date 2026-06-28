"""CLI entrypoint for M8 offline batch jobs.

Usage:
    python -m agent_kit.jobs dedup        --config config.yaml --users alice,bob
    python -m agent_kit.jobs resummarize  --config config.yaml --users alice,bob

``--users`` accepts a comma-separated list of user IDs.  There is no
``--all-users`` option in v1 because the VectorStore Protocol has no
``list_users()`` method; callers must supply the target user IDs explicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from llm_kit import LLMClient, OpenAICompatibleEmbedder
from llm_kit.http.session import build_async_client

from agent_kit.config import AgentKitConfig
from agent_kit.stores.factory import build_stores
from agent_kit.jobs.dedup import EpisodicDeduplicator
from agent_kit.jobs.resummarize import EpisodicResummarizer

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
_log = logging.getLogger("agent_kit.jobs")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="agent_kit offline memory jobs")
    parser.add_argument("command", choices=["dedup", "resummarize"])
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument(
        "--users",
        required=True,
        help="Comma-separated list of user IDs to process",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    cfg = AgentKitConfig.from_yaml(args.config)
    user_ids = [u.strip() for u in args.users.split(",") if u.strip()]
    if not user_ids:
        _log.error("--users must contain at least one user ID")
        sys.exit(1)

    http_client = build_async_client(cfg.llm_kit.http)
    llm = LLMClient(cfg.llm_kit, client=http_client, owns_client=False)
    embedder = OpenAICompatibleEmbedder(cfg.llm_kit, client=http_client, owns_client=False)
    stores = build_stores(cfg)

    try:
        if args.command == "dedup":
            job = EpisodicDeduplicator(stores.vectors, embedder, llm, cfg.jobs.deduplication)
            results = await job.run_for_all_users(user_ids)
            total_merged = sum(r.clusters_merged for r in results)
            total_deleted = sum(r.points_deleted for r in results)
            print(f"dedup complete: {total_merged} clusters merged, {total_deleted} points deleted")

        elif args.command == "resummarize":
            job = EpisodicResummarizer(stores.vectors, embedder, llm, cfg.jobs.resummarization)
            results = await job.run_for_all_users(user_ids)
            total_updated = sum(r.updated for r in results)
            total_scanned = sum(r.scanned for r in results)
            print(f"resummarize complete: {total_updated}/{total_scanned} points refreshed")

    finally:
        await http_client.aclose()


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
