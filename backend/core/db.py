"""Neo4j connection pool — singleton wrapper.

Usage::

    from core.db import get_driver

    driver = get_driver()
    with driver.session() as session:
        session.run("MERGE (u:User {id: $id})", id="alice")
"""

from __future__ import annotations

import logging
from typing import Optional

from neo4j import Driver, GraphDatabase

from core.config import get_settings

logger = logging.getLogger(__name__)


class _Neo4jSingleton:
    """Lazily initialised, process-wide Neo4j driver (connection pool)."""

    _instance: Optional["_Neo4jSingleton"] = None
    _driver: Optional[Driver] = None

    def __new__(cls) -> "_Neo4jSingleton":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get_driver(self) -> Driver:
        if self._driver is None:
            settings = get_settings()
            uri = settings.neo4j_uri
            auth_env = settings.neo4j_auth
            user, _, password = auth_env.partition("/")
            logger.info("Connecting to Neo4j at %s as '%s'", uri, user)
            self._driver = GraphDatabase.driver(uri, auth=(user, password))
        return self._driver

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None
            logger.info("Neo4j driver closed")


def get_driver() -> Driver:
    """Return the shared Neo4j Driver (connection pool).

    The driver is created on first call and reused for the lifetime of the
    process.  The underlying connection pool is managed by the neo4j-python
    driver itself.
    """
    return _Neo4jSingleton().get_driver()


def create_system_meta_proposal(
    *,
    meta_issue_id: str,
    issue_type: str,
    description: str,
    suggested_solution: str,
    source_proposal_id: str,
    arxiv_id: str = "",
    created_at: str = "",
) -> None:
    """SystemMetaProposal を Neo4j に保存し、元の StructureProposal と関連付ける。"""
    driver = get_driver()
    with driver.session() as session:
        session.run(
            """
            MATCH (sp:StructureProposal {proposal_id: $proposal_id})
            MERGE (mp:SystemMetaProposal {meta_issue_id: $meta_issue_id})
            SET mp.issue_type          = $issue_type,
                mp.description         = $description,
                mp.suggested_solution  = $suggested_solution,
                mp.arxiv_id            = $arxiv_id,
                mp.created_at          = $created_at
            MERGE (sp)-[:RAISED_META_ISSUE]->(mp)
            """,
            proposal_id=source_proposal_id,
            meta_issue_id=meta_issue_id,
            issue_type=issue_type,
            description=description,
            suggested_solution=suggested_solution,
            arxiv_id=arxiv_id,
            created_at=created_at,
        )
    logger.info(
        "Created SystemMetaProposal %s (type=%s) linked to proposal %s",
        meta_issue_id,
        issue_type,
        source_proposal_id,
    )
