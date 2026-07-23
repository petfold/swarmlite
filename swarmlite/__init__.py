"""swarmlite — verifiable serverless SQLite hosting on Ethereum Swarm.

Read path::

    con = swarmlite.connect("bzz://<64-hex-ref>/site.db")
    con.execute("SELECT ...")

Publish path::

    root = swarmlite.publish("site.db", feed="site/root")

Design contract: the VFS is strictly read-only; all Swarm transport goes
through swarmfs (fsspec); the publisher is the only write path. See
CLAUDE.md and docs/DESIGN.md before changing anything.
"""

from .vfs import SwarmVFS, connect
from .publish import publish

__version__ = "0.0.1"

__all__ = ["connect", "publish", "SwarmVFS", "__version__"]
