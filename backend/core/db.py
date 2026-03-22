"""Neo4j connection pool — singleton wrapper.

Usage::

    from core.db import get_driver

    driver = get_driver()
    with driver.session() as session:
        session.run("MERGE (u:User {id: $id})", id="alice")

Environment variables
---------------------
NEO4J_URI   : bolt URI of the Neo4j instance  (default: bolt://neo4j:7687)
NEO4J_AUTH  : "<user>/<password>" pair         (default: neo4j/password)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from neo4j import Driver, GraphDatabase

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
            uri = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
            auth_env = os.environ.get("NEO4J_AUTH", "neo4j/password")
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
    """SystemMetaProposal を Neo4j に保存し、元の StructureProposal と関連付ける。

    Parameters
    ----------
    meta_issue_id : str
        SystemMetaProposal のユニーク ID。
    issue_type : str
        MetaIssueCategory の値（例: "temporal_limitation"）。
    description : str
        表現モデルの限界に関する詳細説明。
    suggested_solution : str
        提案される解決策。
    source_proposal_id : str
        この課題を引き起こした StructureProposal の proposal_id。
    arxiv_id : str
        関連する arXiv ID（任意）。
    created_at : str
        作成日時の ISO 文字列（任意）。
    """
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
