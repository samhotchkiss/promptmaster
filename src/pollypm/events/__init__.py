"""Core event-payload helpers shared by the plugin host, runtime, and
plugins.

Lives outside ``plugins_builtin/`` so that core modules (``plugin_host``,
``job_runner``, ``messaging``, ``version_check``) can package structured
activity-feed payloads without taking a dependency on a specific
plugin's private module layout (#805). Plugins consume the same
helpers and re-export them for backward compatibility.
"""

from pollypm.events.summaries import activity_summary

__all__ = ["activity_summary"]
