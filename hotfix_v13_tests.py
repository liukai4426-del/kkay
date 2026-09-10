from pathlib import Path

p=Path('test_engine.py'); s=p.read_text()
old="""    def test_protection_missing_locks(self):
        p=self.active(); self.x.pos=[self.position(p)]; self.e.cycle()
        self.assertFalse(self.e.enabled); self.assertIn('保护',self.e.store.data['halt'])
"""
new="""    def test_protection_missing_locks(self):
        p=self.active(); self.x.pos=[self.position(p)]; self.e.cycle()
        p['filled_at']=time.time()-20; self.e.store.save(); self.e.cycle()
        self.assertFalse(self.e.enabled); self.assertIn('保护',self.e.store.data['halt'])
"""
if s.count(old)!=1: raise RuntimeError('protection-missing legacy test mismatch')
s=s.replace(old,new,1)
old="""    def test_undersized_protection_locks(self):
        p=self.active(); self.x.pos=[self.position(p)]; a=self.protection(p); a['sz']='0.00001'; self.x.protections=[a]
        self.e.cycle(); self.assertFalse(self.e.enabled)
"""
new="""    def test_undersized_protection_locks(self):
        p=self.active(); self.x.pos=[self.position(p)]; a=self.protection(p); a['sz']='0.00001'; self.x.protections=[a]
        self.e.cycle(); p['filled_at']=time.time()-20; self.e.store.save(); self.e.cycle(); self.assertFalse(self.e.enabled)
"""
if s.count(old)!=1: raise RuntimeError('undersized-protection legacy test mismatch')
s=s.replace(old,new,1)
old="""    def test_loss_count_persisted(self):
        self.active(); self.x.equity=99; self.e.reconcile()
        self.assertEqual(Store(self.e.store.path).data['streak'],1)
"""
new="""    def test_loss_count_persisted(self):
        self.active(); self.x.equity=99; self.e.reconcile()
        p=self.e.store.data['active']; p['filled_at']=time.time()-20; self.e.store.save(); self.e.reconcile()
        self.assertEqual(Store(self.e.store.path).data['streak'],1)
"""
if s.count(old)!=1: raise RuntimeError('loss-count legacy test mismatch')
s=s.replace(old,new,1)
p.write_text(s)
print('Legacy engine tests aligned with V1.3 limit-fill timing')
