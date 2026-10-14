"""AgentPlane control plane: stores Chronicle traces and serves TokenOps governance over HTTP.

Only this process opens the database. Entities (``Trace``, ``Span``, ``Envelope``) come from
``agentplane-primitives``; the HTTP wrappers and the governance tables belong here.
"""

from control_plane._version import __version__

__all__ = ["__version__"]
