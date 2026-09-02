"""Start the isolated Crypto/MEXC stack from the unified release."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRYPTO_RUNTIME = ROOT / "crypto_runtime"
CRYPTO_DATA_ROOT = CRYPTO_RUNTIME / "var"
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

from governance.network_preflight import validate_market  # noqa: E402

validate_market("crypto")

os.chdir(CRYPTO_RUNTIME)
os.environ.setdefault("QUANT_CORE_CONFIG", str(ROOT / "config" / "core_crypto.yaml"))
os.environ.setdefault("QUANT_ATOMS_ROOT", str(ROOT / "atoms_crypto"))
os.environ.setdefault("QUANT_CORE_DOMAIN", "crypto")
# ختم NQ 2026-09-01: كل قراءة وكتابة خاصة بالكريبتو تعيش تحت runtime نفسه.
os.environ.setdefault("QUANT_ANALYSIS_SETTINGS_DB", str(CRYPTO_DATA_ROOT / "analysis_settings.db"))
os.environ.setdefault("NQ_NEWS_DB", str(CRYPTO_DATA_ROOT / "news.db"))
os.environ.setdefault("NQ_BRIDGE_DB", str(CRYPTO_DATA_ROOT / "bridge.db"))

from governance.scripts.run_core import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
