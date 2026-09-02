import asyncio, inspect, os, struct, sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3]));sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.contracts.atom import AtomContext, HealthState
import importlib.util as _ilu
_spec=_ilu.spec_from_file_location('_atom608',_Path(__file__).resolve().parents[1]/'atom.py');_mod=_ilu.module_from_spec(_spec);sys.modules['_atom608']=_mod;_spec.loader.exec_module(_mod)
Atom=_mod.Atom;SAMPLE=_mod.EVENT_SAMPLE;DRIFT=_mod.EVENT_DRIFT
CFG={"reference_servers":["a","b","c"],"sync_interval_s":300,"query_timeout_s":3,
     "drift_alert_s":1.0,"stale_after_s":900,"max_accepted_offset_s":5.0,
     "max_sample_deviation_s":0.25,"min_samples":2}
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.published=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.published.append((n,p))
 def ctx(self):return AtomContext(608,dict(CFG),L(),self.publish,self.subscribe)
async def fresh(stub=None):
 b=B();a=Atom();await a.initialize(b.ctx())
 if stub:a._query=stub
 return a,b
def row(host,offset,rtt=.01):return {"server":host,"offset_s":offset,"round_trip_s":rtt,"stratum":2}

async def test_sync_publishes_sample_state_event():
 a,b=await fresh(lambda host:row(host,{"a":.10,"b":.11,"c":4.0}[host]));assert await a._sync_once()
 out=[p for n,p in b.published if n==SAMPLE];assert len(out)==1 and abs(out[0]["median_offset_s"]-.105)<1e-6
 assert out[0]["accepted_count"]==2 and out[0]["rejected_count"]==1 and out[0]["quorum"] is True

async def test_no_self_stamped_timestamp():
 a,b=await fresh(lambda host:row(host,.1));await a._sync_once();assert "timestamp" not in [p for n,p in b.published if n==SAMPLE][-1]

async def test_drift_alert_when_median_exceeds_threshold():
 a,b=await fresh(lambda host:row(host,2.5));await a._sync_once();assert DRIFT in [n for n,p in b.published]

async def test_all_servers_fail_degrades_without_crash():
 def boom(host):raise OSError('network unreachable')
 a,b=await fresh(boom);a._sync_interval_s=.01;await a.start()
 for _ in range(20):
  if a.failure_count:break
  await asyncio.sleep(.01)
 assert a.failure_count>=1 and (await a.health_check()).state==HealthState.DEGRADED
 await a.stop()

async def test_stop_preserves_failure_evidence():
 a,b=await fresh();a._running=True;a.failure_count=7;a._last_error='all failed';await a.stop()
 assert a.failure_count==7 and a._last_error=='all failed'

async def test_stale_event_emits_once_per_success():
 a,b=await fresh();a._last_sync_at=_mod.time.time()-901;a._last_offset_s=.2;a.sync_count=1
 await a._publish_stale_if_due();await a._publish_stale_if_due()
 rows=[p for n,p in b.published if n==_mod.EVENT_STALE]
 assert len(rows)==1 and rows[0]['clock_quality']=='STALE' and rows[0]['sync_age_s']>900

async def test_failure_retry_backoff_sequence():
 a,b=await fresh();a._sync_interval_s=300
 delays=[]
 for failures in (1,2,3,4):
  a._consecutive_failures=failures;delays.append(a._next_delay(False))
 assert delays==[5.0,15.0,60.0,300]
 assert a._next_delay(True)==300

def packet(request,*,mode=4,version=4,leap=0,stratum=2,origin=None,offset=.1):
 data=bytearray(48);data[0]=(leap<<6)|(version<<3)|mode;data[1]=stratum;data[24:32]=request[40:48] if origin is None else origin
 sec,frac=struct.unpack('!II',request[40:48]);sent=sec+frac/(2**32)-_mod._NTP_EPOCH_OFFSET;tx=sent+.01+offset
 sec=int(tx+_mod._NTP_EPOCH_OFFSET);frac=int(((tx+_mod._NTP_EPOCH_OFFSET)-sec)*(2**32));data[40:48]=struct.pack('!II',sec,frac);return bytes(data)
def run_query(responder):
 a=Atom();a._query_timeout_s=1;a._max_accepted_offset_s=5
 old_exchange=_mod.transport.udp_exchange;old_time=_mod.time.time;times=iter((1000.0,1000.02))
 _mod.transport.udp_exchange=lambda host,port,request,size,timeout:responder(request);_mod.time.time=lambda:next(times)
 try:return a._query('x')
 finally:_mod.transport.udp_exchange=old_exchange;_mod.time.time=old_time

def test_ntp_rejects_zero_packet():
 try:run_query(lambda request:bytes(48));assert False
 except ValueError as exc:assert 'NTP reply' in str(exc)
def test_ntp_rejects_wrong_length():
 try:run_query(lambda request:bytes(47));assert False
 except ValueError as exc:assert 'length' in str(exc)
def test_ntp_rejects_originate_mismatch():
 try:run_query(lambda request:packet(request,origin=b'12345678'));assert False
 except ValueError as exc:assert 'originate' in str(exc)
def test_ntp_rejects_stratum_zero_and_sixteen():
 for value in (0,16):
  try:run_query(lambda request,v=value:packet(request,stratum=v));assert False
  except ValueError as exc:assert 'NTP reply' in str(exc)

def test_ntp_rejects_bad_version_and_unsynchronized_leap():
 for kwargs in ({"version":2},{"leap":3}):
  try:run_query(lambda request,k=kwargs:packet(request,**k));assert False
  except ValueError as exc:assert 'NTP reply' in str(exc)

def test_ntp_rejects_offset_outside_bound():
 try:run_query(lambda request:packet(request,offset=6));assert False
 except ValueError as exc:assert 'outside accepted bound' in str(exc)

async def main():
 tests=[test_sync_publishes_sample_state_event,test_no_self_stamped_timestamp,
 test_drift_alert_when_median_exceeds_threshold,test_all_servers_fail_degrades_without_crash,
 test_stop_preserves_failure_evidence,test_stale_event_emits_once_per_success,
 test_failure_retry_backoff_sequence]
 for t in tests:await t()
 for t in (test_ntp_rejects_zero_packet,test_ntp_rejects_wrong_length,test_ntp_rejects_originate_mismatch,test_ntp_rejects_stratum_zero_and_sixteen,test_ntp_rejects_bad_version_and_unsynchronized_leap,test_ntp_rejects_offset_outside_bound):t()
if __name__=='__main__':asyncio.run(main())
