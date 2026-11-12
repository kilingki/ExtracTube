"""T4 E02 bounded evidence gate: exact audio clip, one ASR call, up to 3 review frames."""
from pathlib import Path
from datetime import datetime,timezone
import sys,uuid,fcntl,argparse,wave,time,urllib.request,urllib.error,subprocess
import run_e00 as b
from extractube.infer import build_multipart,_NoRedirect
HERE=b.HERE
PARENT=HERE/'runs/20261101T162340Z_e01_88caa2'
WAV=b.ROOT/'data/Fw-8hZhnH3Q/source/source.wav'
VIDEO=WAV.parent/'video.mp4'
WAV_SHA='6290cf9bde352a5024efa0092d75f6c59ca2d09eb4bac29656d8dd5c4d205721'
VIDEO_SHA='360018e72a1f4395a03136d5d96b02958ad0985567b43c4d310bab261105dcf3'
def current():return HERE/'runs'/b.read(HERE/'e02_campaign.json')['run_id']
def clip(source,dest,start=610,end=642):
    with wave.open(str(source),'rb') as w:
        if (w.getframerate(),w.getnchannels(),w.getsampwidth())!=(16000,1,2):raise ValueError('pcm_contract')
        if not 0<=start<end<=w.getnframes()/16000:raise ValueError('clip_range')
        w.setpos(start*16000);data=w.readframes((end-start)*16000)
    if len(data)!=(end-start)*32000:raise ValueError('incomplete_samples')
    with wave.open(str(dest),'wb') as out:out.setparams((1,2,16000,0,'NONE','not compressed'));out.writeframes(data)
    return {'start_seconds':start,'end_seconds':end,'start_sample':start*16000,'end_sample':end*16000,'num_samples':len(data)//2,'sample_rate':16000,'channels':1,'sample_width':2,'sha256':b.sha(dest)}
def prepare():
    if (HERE/'e02_campaign.json').exists():raise ValueError('existing_E02')
    b.identity(PARENT);pc=b.read(PARENT/'config.json')
    prev=pc['prior_records']+b.old.records(PARENT)
    if len(prev)!=5 or b.read(PARENT/'reviews/G1.json')['decision']!='rejected':raise ValueError('parent_gate')
    b.bound_sha(WAV,WAV_SHA);b.bound_sha(VIDEO,VIDEO_SHA)
    critical=dict(pc['critical_files'])
    for p in PARENT.rglob('*'):
        if p.is_file():critical[str(p)]=b.sha(p)
    for p in [WAV,VIDEO,HERE/'run_e02.py']:critical[str(p)]=b.sha(p)
    rid=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_e02_'+uuid.uuid4().hex[:6]
    run=HERE/'runs'/rid;run.mkdir()
    cm=clip(WAV,run/'source_610_642.wav')
    req=build_multipart([('file','file','review.wav','audio/wav',(run/'source_610_642.wav').read_bytes()),('field','response_format','verbose_json'),('field','include_chunks','true'),('field','language','ko')],'T4E02Evidence')
    (run/'request.multipart').write_bytes(req)
    config={'candidate':'E02','policy':'E02.evidence_checked_source_and_scoped_prose.v1','critical_files':critical,'prior_records':prev,
       'limits':{'candidate_calls':3,'candidate_http_seconds':900,'family_calls':27,'family_http_seconds':7200,'timeout':300,'retry':0},
       'endpoint':'http://127.0.0.1:8095/infer?model=qwen-asr','request_sha256':b.sha(run/'request.multipart'),
       'content_type':'multipart/form-data; boundary=T4E02Evidence','authorization':'2026-11-02 E02 execute/review; if failed design E03 only'}
    b.create(run/'config.json',config);b.create(run/'clip_manifest.json',cm)
    (run/'spec_snapshot.md').write_bytes(b.SPEC.read_bytes());(run/'source_code').mkdir()
    (run/'source_code/run_e02.py').write_bytes((HERE/'run_e02.py').read_bytes())
    b.create(run/'state.json',{'frozen':{n:b.sha(run/n) for n in ['config.json','clip_manifest.json','source_610_642.wav','request.multipart','spec_snapshot.md']}})
    b.create(HERE/'e02_campaign.json',{'run_id':rid})
    print(b.wire({'run':str(run),'prior_calls':len(prev),'clip':cm}).decode())
def send():
    run=current();c=b.identity(run);p=run/'journal/audio_evidence.json'
    if p.exists():raise ValueError('already_sent_no_retry')
    rr=b.old.records(run)
    timeout=min(b.old.reserve(c['prior_records']+rr,300,27,7200),b.old.reserve(rr,300,3,900))
    req=urllib.request.Request(c['endpoint'],data=(run/'request.multipart').read_bytes(),headers={'Content-Type':c['content_type']},method='POST')
    rec={'job_id':'audio_evidence','status':'started','reserved_seconds':timeout,'request_sha256':c['request_sha256']}
    b.create(p,rec);start=time.monotonic()
    try:
        opener=urllib.request.build_opener(_NoRedirect())
        try:response=opener.open(req,timeout=timeout)
        except urllib.error.HTTPError as exc:response=exc
        with response:
            b.create(run/'response.headers.json',{'status':response.code,'headers':dict(response.headers)})
            raw=response.read(4194305);(run/'response.body').write_bytes(raw)
            length=response.headers.get('Content-Length')
            complete=len(raw)<=4194304 and (length is None or len(raw)==int(length))
            rec.update(http_status=response.code,response_sha256=b.sha(run/'response.body'),response_complete=complete)
            if not complete:rec.update(status='unknown',error='incomplete_or_oversize')
            elif response.code!=200:rec.update(status='failed',error='http_error')
            else:
                data=b.prior.loads(raw.decode());audio=data.get('audio',{})
                if audio.get('sample_rate')!=16000 or audio.get('channels')!=1 or audio.get('num_samples')!=512000 or not isinstance(data.get('chunks'),list):raise ValueError('asr_audio_contract')
                b.create(run/'asr_candidate.json',data)
                rec.update(status='success',quality_status='candidate_only_not_verified')
    except Exception as exc:rec.update(status='unknown',error=type(exc).__name__+':'+str(exc))
    rec['http_seconds']=round(time.monotonic()-start,6);b.write(p,rec);print(b.wire(rec).decode())
def frames():
    run=current();b.identity(run)
    if (run/'frames_manifest.json').exists():raise ValueError('frames_already_created')
    rows=[]
    for sec in [619,626,636]:
        p=run/('frame_'+str(sec)+'.png')
        subprocess.run(['ffmpeg','-v','error','-ss',str(sec),'-i',str(VIDEO),'-frames:v','1',str(p)],check=True)
        rows.append({'video_seconds':sec,'path':p.name,'sha256':b.sha(p),'role':'review_only_not_stage2_asset'})
    b.create(run/'frames_manifest.json',rows);print(b.wire(rows).decode())
def selftest():
    import tempfile,unittest
    class Checks(unittest.TestCase):
        def test_clip(self):
            with tempfile.TemporaryDirectory() as td:
                src=Path(td)/'s.wav';dest=Path(td)/'c.wav'
                with wave.open(str(src),'wb') as w:w.setparams((1,2,16000,0,'NONE','none'));w.writeframes(bytes(32000))
                self.assertEqual(clip(src,dest,0,1)['num_samples'],16000)
                with self.assertRaises(ValueError):clip(src,dest,1,0)
        def test_unknown(self):
            with self.assertRaises(ValueError):b.old.reserve([{'status':'unknown','reserved_seconds':300}],300,3,900)
        def test_bounds(self):
            self.assertEqual(b.old.reserve([{'status':'success','http_seconds':899}],300,3,900),1)
            with self.assertRaises(ValueError):b.old.reserve([{'status':'success','http_seconds':1}]*3,300,3,900)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
    if not result.wasSuccessful():raise SystemExit(1)
def closeout():
    run=current();c=b.identity(run);gate=b.read(run/'evidence_review.json');rr=b.old.records(run);allrr=c['prior_records']+rr
    if gate['decision']=='accepted':raise ValueError('accepted_evidence_requires_writer_implementation_not_silent_closure')
    result={'candidate':'E02','decision':gate['decision'],'technical_asr':rr[0]['status'] if rr else 'not_sent','T4_complete':False,'t5_ready':False,
       'new_calls':len(rr),'new_http_seconds':round(sum(x['http_seconds'] for x in rr),6),'family_calls':len(allrr),'family_http_seconds':round(sum(x['http_seconds'] for x in allrr),6),
       'writer_calls':0,'E03_calls':0,'reason':gate['reason']}
    b.create(run/'closeout.json',result);b.create(run/'acceptance.json',result);print(b.wire(result).decode())
def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','send','frames','verify','selftest','closeout']);a=ap.parse_args()
    with (HERE/'campaign.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if a.action=='prepare':prepare()
        elif a.action=='send':send()
        elif a.action=='frames':frames()
        elif a.action=='selftest':selftest()
        elif a.action=='closeout':closeout()
        else:b.identity(current());print('identity_verified_no_inference')
if __name__=='__main__':main()
