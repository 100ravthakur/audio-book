import sys
import warnings

# urllib3 warns about optional SOCKS support; nothing here uses a SOCKS proxy.
warnings.filterwarnings("ignore", message=".*SOCKS.*")
warnings.filterwarnings("ignore", category=DeprecationWarning)

from .cli import main  # noqa: E402

sys.exit(main())
