"""Start the isolated Forex/MT5+cTrader stack from the unified release."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

from governance.network_preflight import validate_market  # noqa: E402

validate_market("forex")

os.chdir(ROOT / "forex_runtime")
os.environ.setdefault("QUANT_CORE_CONFIG", str(ROOT / "config" / "core_forex.yaml"))
os.environ.setdefault("QUANT_ATOMS_ROOT", str(ROOT / "atoms"))
os.environ.setdefault("QUANT_CORE_DOMAIN", "forex")
os.environ.setdefault("QUANT_ANALYSIS_SETTINGS_DB", str(ROOT / "var" / "forex" / "analysis_settings.db"))
os.environ.setdefault("NQ_NEWS_DB", str(Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal" / "Common" / "Files" / "nq_brain.db"))
# NQ_BRIDGE_DB مقصوص عمدًا: ذرّات جسر MetaTrader (618/619/601) لازم تقرأ/تكتب
# nq_brain.db في مجلّد MetaTrader المشترك حيث يكتب الـEA، لا bridge.db المعزولة
# الفارغة — تعيينه كان يخفي ticks_v2/account_v2 ويعطب التنفيذ (درس server.py:615).

from governance.scripts.run_core import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
