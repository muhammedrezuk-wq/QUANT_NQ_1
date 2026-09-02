#!/usr/bin/env python3
"""Static cross-platform proof for approved bridge paper 11 (17 contracts)."""
from __future__ import annotations

from pathlib import Path
import sys
import yaml

ROOT=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)

MT5=(ROOT/"mt5/QUANT_NQ.mq5").read_text(encoding="utf-8")
CTR=(ROOT/"ctrader/QuantNQ_Feed.cs").read_text(encoding="utf-8")


def atom(atom_id:int)->str:return (next((ATOM_ROOT).glob(f"{atom_id}_*"))/"atom.py").read_text()
def manifest(atom_id:int)->dict:return yaml.safe_load((next((ATOM_ROOT).glob(f"{atom_id}_*"))/"manifest.yaml").read_text())

S={i:atom(i) for i in (578,609,611,618,619,622)}
# 617 (قارئ ملفّ الجسر) أُرشفت بيد المالك 2026-08-21 وحلّت محلّها 622 (FIX
# مباشر) بنفس العقد المنشور ونفس مساعد حالات التدفّق. البرهان يتبع الحيّ.
STREAM_HELPER=next((ATOM_ROOT).glob("622_*"))/"ctrader_stream_state.py"
STREAM_SOURCE=STREAM_HELPER.read_text() if STREAM_HELPER.exists() else ""
checks=[
 ("دليل الوسيط الكامل",all(token in MT5 for token in ("TradeResultOk(false)","Trade.ResultDeal()","DEAL_POSITION_ID","fill <= 0"))),
 ("PLACED ليس fill", "allow_placed && rc == TRADE_RETCODE_PLACED" in MT5),
 ("ticket صفر مرفوض",MT5.count("ticket == 0")>=2),
 ("magic مفروض",all(token in MT5 for token in ("command_magic != InpMagic","POSITION_MAGIC","ORDER_MAGIC"))),
 ("cTrader لا ينفذ",not any(token in CTR for token in ("ExecuteMarketOrder(","PlaceLimitOrder(","ClosePosition(","ModifyPosition("))),
 ("انحراف الساعة مقاس",all(token in S[618] for token in ("broker_clock_offset_s","exchange_timestamp"))),
 ("كل القراء على v2",manifest(619)["config"]["table_name"]=="account_v2" and manifest(618)["config"]["table_name"]=="ticks_v2" and manifest(618)["config"]["spec_table"]=="symbol_specs_v2" and manifest(609)["config"]["table_name"]=="positions_v2" and manifest(611)["config"]["table_name"]=="trade_events_v2"),
 ("619 لا يرجع للقديم","FROM account_v2" in S[619] and "table = \"account\"" not in S[619]),
 ("trade_events معزول",all(token in MT5 for token in ("CREATE TABLE IF NOT EXISTS trade_events_v2","account_id TEXT NOT NULL","INSERT INTO trade_events_v2")) and "trade_events_v2" in S[611]),
 ("لا كتابة تشغيلية للقديم",not any(token in MT5 for token in ("INSERT INTO trade_events (","REPLACE INTO symbol_specs (","INSERT INTO account (","UPDATE account SET","INSERT INTO positions (","INSERT INTO ticks (","INSERT INTO prices ("))),
 ("ملكية التذكرة بالحساب",all(token in MT5 for token in ("account_id != current_account","SelectPos(ticket, sym, account_id, command_magic)"))),
 ("sequence في المصدر والقارئ",all(token in CTR for token in ("_sequence++","WithSequence")) and all(token in S[622] for token in ("sequence_gap","out_of_order"))),
 ("حالات cTrader الأربع",all(token in STREAM_SOURCE for token in ("NEVER_SEEN","ACTIVE","STALE","DEAD"))),
 ("عمر كل تدفق ظاهر",all(token in S[622] for token in ("tick_stale_after_s","depth_stale_after_s","specs_stale_after_s"))),
 ("609 ينتج stale","POSITIONS_STALE" in S[609] and "usable_for_new_exposure" in S[609]),
 ("609 لا يبتلع الحقول",all(token in S[609] for token in ("ACCOUNT_ID_UNAVAILABLE","COMMISSION_UNAVAILABLE","SCHEMA_UNAVAILABLE")) and "except sqlite3.Error:\n                pass" not in S[609]),
 ("divergence معزول",'_key(account,broker,symbol)+SEP+timeframe' in S[578]),
]
failed=0
for name,ok in checks:
 print(("PASS" if ok else "FAIL"),name)
 failed+=not ok
print(f"PROOF_BRIDGES={len(checks)-failed} PASS {failed} FAIL")
raise SystemExit(1 if failed else 0)
