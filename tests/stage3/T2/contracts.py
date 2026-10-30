import json
def loads(text):
    def pairs(items):
        out={}
        for k,v in items:
            if k in out: raise ValueError("duplicate_json_key")
            out[k]=v
        return out
    return json.loads(text,object_pairs_hook=pairs,parse_constant=lambda x:(_ for _ in ()).throw(ValueError("nonfinite_json")))
def exact(obj,keys):
    if not isinstance(obj,dict) or set(obj)!=set(keys): raise ValueError("object_keys")

def parse_structure(obj,target):
    exact(obj,['sections'])
    sections=obj['sections']
    if not isinstance(sections,list) or not 1<=len(sections)<=12: raise ValueError('section_count')
    children_count=0
    def partition(nodes,a,z,root=False):
        nonlocal children_count
        cursor=a
        for n in nodes:
            exact(n,['sentence_start','sentence_end','title','children'] if root else ['sentence_start','sentence_end','title'])
            x,y=n['sentence_start'],n['sentence_end']
            if type(x) is not int or type(y) is not int or x!=cursor or not x<y<=z: raise ValueError('partition')
            if not isinstance(n['title'],str) or not n['title'].strip() or not 1<=len(n['title'])<=60: raise ValueError('title')
            if root:
                ch=n['children']
                if not isinstance(ch,list): raise ValueError('children_array')
                children_count+=len(ch)
                if children_count>24: raise ValueError('children_count')
                if ch: partition(ch,x,y)
            cursor=y
        if cursor!=z: raise ValueError('partition_end')
    partition(sections,*target,root=True)
    return obj

def parse_response(raw,status,job):
    if status!=200: raise ValueError('http_error')
    outer=loads(raw.decode('utf-8'))
    if not isinstance(outer,dict): raise ValueError('outer_object')
    choices=outer.get('choices')
    if not isinstance(choices,list) or len(choices)!=1: raise ValueError('choices')
    choice=choices[0]
    if not isinstance(choice,dict) or choice.get('finish_reason')!='stop': raise ValueError('finish_reason')
    message=choice.get('message')
    if not isinstance(message,dict): raise ValueError('message')
    content=message.get('content')
    if not isinstance(content,str) or not content.strip() or content.lstrip().startswith(chr(96)*3): raise ValueError('content')
    return parse_structure(loads(content),job['target']),{'usage':outer.get('usage'),'finish_reason':'stop'}

def nodes(obj):
    for i,n in enumerate(obj['sections']):
        yield 's'+str(i),n
        for j,ch in enumerate(n['children']): yield 's'+str(i)+'.'+str(j),ch
