from __future__ import annotations

from typing import Any

NEVER_SEEN="NEVER_SEEN"
ACTIVE="ACTIVE"
STALE="STALE"
DEAD="DEAD"
STREAMS=("tick","depth","spec")


class StreamTracker:
    def __init__(self, stale: dict[str,float], dead_after_s: float) -> None:
        self.stale=dict(stale);self.dead_after_s=float(dead_after_s)
        self.last:dict[str,float]={};self.last_sequence:dict[str,int]={}
        self.symbols:set[tuple[str,str]]=set();self.gaps=0;self.out_of_order=0
        self.stopped:set[str]=set()

    @staticmethod
    def key(account:str,symbol:str,kind:str)->str:return "|".join((account,symbol,kind))

    def observe(self,account:str,symbol:str,kind:str,stamp:float,sequence:int)->dict[str,Any]:
        previous=self.last_sequence.get(account);gap=0;out=False
        # سجل «انطلاق» = حدود جلسة كتابة جديدة: عدّادها يبدأ من جديد، فذاكرة
        # التسلسل القديمة تُصفَّر وإلا رُمي كل ما بعده «خارج الترتيب» إلى الأبد.
        # (عطل حي مقيس 2026-08-19: إعادة تشغيل المنصّة منتصف الجلسة صفّرت العدّاد
        # وسجل التصفير sequence=1 قد لا يصل نفسه — الانطلاق هو الحدّ الموثوق.)
        if kind=="start" and previous is not None:
            previous=None;self.last_sequence.pop(account,None);self.stopped.discard(account)
        if sequence==1 and previous is not None:
            previous=None;self.last_sequence.pop(account,None);self.stopped.discard(account)
        if previous is not None:
            if sequence<=previous:out=True;self.out_of_order+=1
            elif sequence>previous+1:gap=sequence-previous-1;self.gaps+=gap
        if not out:self.last_sequence[account]=sequence
        if kind=="start":self.stopped.discard(account)
        elif kind=="stop":self.stopped.add(account)
        if symbol and symbol!="*":self.symbols.add((account,symbol))
        return {"previous_sequence":previous,"sequence_gap":gap,"out_of_order":out}

    def mark(self,account:str,symbol:str,kind:str,stamp:float)->None:
        if kind in STREAMS:
            self.symbols.add((account,symbol));self.last[self.key(account,symbol,kind)]=stamp

    def rebase(self)->None:
        """إعادة تأسيس بعد قفزة موضع (استعادة لقطة/استبدال ملف): تسلسل الموضع
        المهجور لم يعد مرجعًا — جلسة الكتابة الحالية قد تكون صفّرت عدّادها في
        سجلٍ تخطّيناه، فيُرمى كل ما بعده «خارج الترتيب» إلى الأبد (عطل حي مقيس
        2026-08-19). تُمسح ذاكرة التسلسل وأعلام التوقف؛ تبقى الطوابع (حقيقة
        النضارة) والعدّادات التراكمية (تاريخ لا يُمحى)."""
        self.last_sequence.clear();self.stopped.clear()

    def state(self,account:str,symbol:str,kind:str,now:float)->dict[str,Any]:
        stamp=self.last.get(self.key(account,symbol,kind))
        age=None if stamp is None else now-stamp
        dead_limit=max(self.dead_after_s,self.stale[kind]*2.0)
        if stamp is None:status=NEVER_SEEN
        elif account in self.stopped or age is None or age<0 or age>dead_limit:status=DEAD
        elif age>self.stale[kind]:status=STALE
        else:status=ACTIVE
        return {"account_id":account,"symbol":symbol,"stream":kind,
                "state":status,"last_at":stamp,"age_s":age,
                "stale_after_s":self.stale[kind],"dead_after_s":dead_limit}

    def newest_stamp(self)->float:
        """The venue's own clock as WE last saw it: the newest stamp received
        on any stream. Freshness is judged on this base (owner stamp
        2026-08-21) because every stamp we compare against it was written by
        that same clock."""
        return max(self.last.values(),default=0.0)

    def view(self,now:float)->list[dict[str,Any]]:
        return [self.state(account,symbol,kind,now) for account,symbol in sorted(self.symbols)
                for kind in STREAMS]

    def snapshot(self)->dict[str,Any]:
        return {"last":dict(self.last),"last_sequence":dict(self.last_sequence),
                "symbols":[list(item) for item in sorted(self.symbols)],
                "gaps":self.gaps,"out_of_order":self.out_of_order,"stopped":sorted(self.stopped)}

    def restore(self,state:dict[str,Any])->None:
        if not isinstance(state,dict):raise ValueError("INVALID_CTRADER_STREAM_STATE")
        self.last={str(key):float(value) for key,value in (state.get("last") or {}).items()}
        self.last_sequence={str(key):int(value) for key,value in (state.get("last_sequence") or {}).items()}
        self.symbols={tuple(item) for item in state.get("symbols",[]) if isinstance(item,list) and len(item)==2}
        self.gaps=int(state.get("gaps") or 0);self.out_of_order=int(state.get("out_of_order") or 0)
        self.stopped={str(item) for item in state.get("stopped",[])}
