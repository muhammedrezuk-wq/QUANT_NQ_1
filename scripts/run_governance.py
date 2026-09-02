"""Start the market-specific governance dashboard.

Run one process per market. Both serve the same React build but each process
reads only its own core endpoint and runtime data root.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Forex or Crypto dashboard")
    parser.add_argument("--market", choices=("forex", "crypto"), required=True)
    parser.add_argument("--port", type=int, default=None, help="Internal governance port")
    args = parser.parse_args()
    crypto = args.market == "crypto"
    os.environ["QUANT_GOV_MARKET"] = args.market
    os.environ["QUANT_GOV_CORE"] = "http://127.0.0.1:%d" % (8020 if crypto else 8010)
    os.environ["QUANT_GOV_PORT"] = str(args.port or (8091 if crypto else 8090))
    # ٢٠٢٦-٠٨-٣١ (ختم NQ): كان هنا فرضٌ لـ`NQ_NEWS_DB` و`NQ_BRIDGE_DB` على
    # `var/<السوق>/news.db` و`bridge.db` — **وكلاهما لم يوجد قطّ** (مقاس:
    # الثلاثة مسارات غير موجودة). و`server.py` يقرأ `NQ_NEWS_DB` أوّلًا، فكان
    # الفرض يدوس الافتراض الموثَّق (جسر ميتاتريدر `nq_brain.db`) ويجعل
    # `/gov/news` و`/gov/calendar` يردّان `available:false` وقائمة فارغة —
    # بينما الجسر يحمل 391 خبرًا و1130 حدثًا مكتوبة قبل دقائق.
    # هذا نفس العطل الذي يصفه تعليق `server.py:63-67` كمُصلَح: أُصلح هناك
    # بالافتراض، ونُقض هنا بالفرض. لا يُعاد ضبطهما: الفوركس يقرأ جسر
    # ميتاتريدر، والكريبتو يختار `crypto_runtime/var/bridge.db` من فرع
    # `MARKET` في `server.py` بلا حاجة لمتغيّر بيئة.
    from governance.server import main as server_main
    server_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
