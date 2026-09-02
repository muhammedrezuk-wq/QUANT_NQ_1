from pathlib import Path
import asyncio,sqlite3
import pytest
from tests.test_stores_contract import B,M701,M702,start,count,trade_event,load
M706=load('706_مخزن_النماذج','ext706');M707=load('707_مخزن_القرارات','ext707');M709=load('709_مخزن_المحفظة','ext709');M712=load('712_مخزن_التحليل','ext712')
@pytest.mark.asyncio
async def test_trade_store_enforces_own_row_cap(tmp_path):
 a,b=await start(M702,702,{'db_path':str(tmp_path/'t.db'),'max_rows':3})
 for i in range(5):await b.publish(M702.EVENT_TRADE,trade_event(i+1))
 assert count(a._db_path,'trades')==3 and a._limit_state['breached']
@pytest.mark.asyncio
async def test_model_store_enforces_byte_cap(tmp_path):
 c={'db_path':str(tmp_path/'m.db'),'keep_versions_per_model':10,'max_db_bytes':1000};a,b=await start(M706,706,c)
 await b.publish(M706.EVENT_PERSIST_REQUESTED,{'model_name':'m','version':'1','data':{'blob':'x'*100000},'timestamp':1})
 assert a._limit_state['breached'] and count(a._db_path,'model_versions')==0
@pytest.mark.asyncio
async def test_decision_store_enforces_full_payload_caps(tmp_path):
 c={'db_path':str(tmp_path/'d.db'),'keep_full_payload':True,'max_rows':2,'max_db_bytes':10000000};a,b=await start(M707,707,c)
 for i in range(4):await b.publish(M707.EVENT_APPROVED,{'request_id':str(i),'timestamp':i})
 assert count(a._db_path,'decisions')==2 and a._limit_state['breached']
@pytest.mark.asyncio
async def test_small_analysis_store_uses_row_cap(tmp_path):
 c={'db_path':str(tmp_path/'a.db'),'flush_size':10,'retention_days':0,'flush_interval_s':10,'max_rows':2,'max_db_bytes':0};a,b=await start(M712,712,c)
 for i in range(4):await b.publish(M712.EVENT_IN,{'symbol':'NQ','timestamp':i})
 await a._flush_and_report(None);assert count(a._db_path,'analysis')==2;await a.stop()
@pytest.mark.asyncio
async def test_portfolio_store_uses_row_cap(tmp_path):
 c={'db_path':str(tmp_path/'p.db'),'min_write_interval_s':0,'retention_days':0,'max_rows':2};a,b=await start(M709,709,c)
 for i in range(4):await b.publish(M709.EVENT_IN,{'account_id':'A','timestamp':i,'equity':100+i})
 assert count(a._db_path,'portfolio')==2
@pytest.mark.asyncio
async def test_immediate_store_counters_restore(tmp_path):
 a,b=await start(M702,702,{'db_path':str(tmp_path/'t.db'),'max_rows':10});await b.publish(M702.EVENT_TRADE,trade_event(1));snap=await a.snapshot();a2,b2=await start(M702,702,{'db_path':str(tmp_path/'t.db'),'max_rows':10});await a2.restore(snap);assert a2.stored_count==1
