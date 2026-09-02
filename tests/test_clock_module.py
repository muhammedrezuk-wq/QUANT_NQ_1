from __future__ import annotations

import math

from clock.clock import INVALID, LOCAL_FALLBACK, STALE, SYNCED, OfficialClock
from clock.pulse import PulseGuard

class FakeTime:
    def __init__(self): self.wall=1000.0;self.mono=10.0
    def wall_now(self):return self.wall
    def mono_now(self):return self.mono
    def advance(self,value):self.wall+=value;self.mono+=value

def configured(fake):
 c=OfficialClock(fake.wall_now,fake.mono_now);c.configure(max_accepted_offset_s=5,
  max_sample_age_s=30,stale_after_s=10,max_slew_per_second=.5);return c

def sample(fake,offset=.3,quorum=True):return {'median_offset_s':offset,'measured_at':fake.wall,'quorum':quorum}

def test_rejects_non_finite_stale_bound_and_wrong_writer():
 f=FakeTime();c=configured(f)
 for value in (float('nan'),float('inf'),-float('inf')):
  ok,_=c.accept_sample(sample(f,value),writer='003');assert not ok
 assert not c.accept_sample(sample(f,6),writer='003')[0]
 assert not c.accept_sample(sample(f),writer='806')[0]
 stale=sample(f);stale['measured_at']-=31;assert not c.accept_sample(stale,writer='003')[0]
 assert not c.accept_sample(sample(f,quorum=False),writer='003')[0]

def test_now_never_moves_backward_and_slews():
 f=FakeTime();c=configured(f);assert c.accept_sample(sample(f,.5),writer='003')[0]
 first=c.now();f.advance(1);second=c.now();assert second>=first and c.state()['offset_s']>0
 f.wall-=100;third=c.now();assert third>=second and c.state()['backward_clamps']>=1
 assert c.quality()==INVALID

def test_quality_transitions_local_synced_stale():
 f=FakeTime();c=configured(f);assert c.quality()==LOCAL_FALLBACK
 assert c.accept_sample(sample(f),writer='003')[0] and c.quality()==SYNCED
 f.advance(11);assert c.quality()==STALE

def test_mono_is_independent_timeout_clock():
 f=FakeTime();c=configured(f);before=c.mono();f.wall-=500;f.mono+=2;assert c.mono()-before==2


def test_pulse_guard_validates_derived_identity_and_restores():
 guard=PulseGuard('SYS_DAY');valid={'pulse_id':'SYS_DAY|86400','bucket_start':86400.0}
 assert guard.accept(valid)
 assert not guard.accept(dict(valid))
 state=guard.snapshot();restored=PulseGuard('SYS_DAY');restored.restore(state)
 assert not restored.accept(dict(valid))
 assert not restored.accept({'pulse_id':'SYS_DAY|172800','bucket_start':86400.0})
