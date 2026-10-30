#!/usr/bin/env python3
"""Prepare spec 14.4 B source review inventory without marking it reviewed."""

import argparse
from pathlib import Path
from PIL import Image, ImageDraw
from extractube.store import read, write, sha, ref

def window(values,size):
    if not values:return []
    at=max(0,(len(values)-size)//2)
    return values[:size]+values[at:at+size]+values[-size:]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('video_id');parser.add_argument('run_id');parser.add_argument('--data-root',type=Path,default=Path('data'));args=parser.parse_args()
    work=(args.data_root/args.video_id).resolve();directory=work/f'vision/runs/{args.run_id}';dest=directory/'source_review';dest.mkdir(exist_ok=True)
    h=read(directory/'stage3_input.json');fd=read(directory/'t4/frames.json')['payload'];t5=read(directory/'t5/visual_assets.json')['payload'];fm={f['frame_id']:f for f in fd['frames']};am={a['association_id']:a for a in fd['associations']}
    order=lambda d:(fm[d['frame_id']]['pts'],d['association_id'])
    use=sorted([d for d in t5['decisions'] if d['decision']=='use'],key=order);omit=sorted([d for d in t5['decisions'] if d['decision']=='omit'],key=order);uncertain_failed=sorted([d for d in t5['decisions'] if d['decision'] in ('uncertain','failed')],key=order)
    selected=(use if len(use)<=15 else window(use,5))+uncertain_failed+window(omit,3)
    selected=list({d['association_id']:d for d in selected}.values());selected.sort(key=order)
    inventory=[]
    for i,d in enumerate(selected):
        f=fm[d['frame_id']];reference=f['full'];p=work/reference['path']
        if sha(p)!=reference['sha256']:raise ValueError('source review hash mismatch')
        inventory.append({'review_index':i,'association_id':d['association_id'],'candidate_id':am[d['association_id']]['candidate_id'],'frame_id':d['frame_id'],'actual_video_seconds':f['video_seconds'],'image':reference,'automatic_decision':d['decision'],'caption':d['caption'],'reason':d['reason'],'source_review_performed':False})
    per_page=12
    for page in range((len(inventory)+per_page-1)//per_page):
        canvas=Image.new('RGB',(2400,1200),'#ddd');draw=ImageDraw.Draw(canvas)
        for slot,r in enumerate(inventory[page*per_page:page*per_page+per_page]):
            with Image.open(work/r['image']['path']) as im:
                im.load();im.thumbnail((800,350));x=(slot%3)*800;y=(slot//3)*300
                # Four rows of 300 pixels; content is letterboxed, never altered at source.
                im.thumbnail((800,270));canvas.paste(im,(x,y+26))
                draw.text((x+5,y+5),f'{r["review_index"]} {r["automatic_decision"]} {r["actual_video_seconds"]}s {r["candidate_id"]}',fill='black')
        p=dest/f'contact_{page:03d}.jpg';canvas.save(p,quality=90)
        for r in inventory[page*per_page:page*per_page+per_page]:r['contact_sheet']=ref(work,p)
    report={'scope':'spec_14_4_B_source_review','video_id':args.video_id,'run_id':args.run_id,'automatic_counts':h['coverage'],'use_total':len(use),'omit_total':len(omit),'uncertain_failed_total':len(uncertain_failed),'review_inventory_count':len(inventory),'quality_review_performed':False,'rows':inventory}
    write(dest/'manifest.json',report,immutable=True)
    print('REVIEW_MANIFEST='+str(dest/'manifest.json'))
if __name__=='__main__':main()
