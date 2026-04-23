"""PollyPM package."""

from importlib.metadata import PackageNotFoundError, version as _installed_version

__all__ = ["__version__"]

try:
    __version__ = _installed_version("pollypm")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
