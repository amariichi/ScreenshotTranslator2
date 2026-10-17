"""Read the release version from installed project metadata."""
from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("screenshot-translator")
except PackageNotFoundError:  # source checkout without an installed distribution
    __version__ = "0.0.0+unknown"
