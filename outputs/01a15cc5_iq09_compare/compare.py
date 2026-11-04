import json,datetime
from pathlib import Path
from collections import Counter
import openpyxl
out=Path(__file__).parent
source=Path('C:/Users/Leo.Li/Downloads/IQ09 20261022.XLSX')
hana=out.parent/'01a15cc5_iq09_3111/IQ09_3111_Z_D_20261022.xlsx'
def read(path):
 w=openpyxl.load_workbook(path,data_only=True,read_only=True)
 s=w.worksheets[0]
 it=s.iter_rows(values_only=True); h=next(it)
 rows=[{'_row':i,**dict(zip(h,r))} for i,r in enumerate(it,2) if any(v is not None and v!='' for v in r)]
 w.close();return list(h),rows
ah,a=read(source);bh,b=read(hana)
fields=[('Material','Material'),('Material Description','Material Description'),('Serial Number','Serial Number'),('Plant','Plant'),('Storage location','Storage Location'),('Sales Order','Sales Order'),('Equipment','Equipment'),('System status','System Status'),('Stock Type (Primary Posting)','Stock Type'),('Special Stock','Special Stock'),('Sales order item','Sales Order Item'),('Created on','Created On'),('Changed On','Changed On')]
def text(v):return '' if v is None else str(v).strip()
def norm(v,field):
 v=text(v)
 if field in ('Equipment','Plant','Storage Location','Sales Order','Sales Order Item','Stock Type'):
  if v.endswith('.0'):v=v[:-2]
  return v.lstrip('0') or ('0' if v else '')
 if field in ('Created On','Changed On'):return v[:10] if v and not v.startswith('0000') else ''
 if field=='System Status':return ' '.join(sorted(v.split()))
 return v
def key(r):return norm(r['Equipment'],'Equipment')
ac=Counter(key(r) for r in a);bc=Counter(key(r) for r in b)
assert all(ac.values()) and all(bc.values())
duplicates=[['User export',k,n] for k,n in ac.items() if n>1]+[['HANA export',k,n] for k,n in bc.items() if n>1]
assert not duplicates,duplicates
am={key(r):r for r in a};bm={key(r):r for r in b}
only_a=[am[k] for k in sorted(am.keys()-bm.keys())]
only_b=[bm[k] for k in sorted(bm.keys()-am.keys())]
diff=[];matched=0;changed=set()
for k in sorted(am.keys()&bm.keys()):
 x,y=am[k],bm[k]
 for af,bf in fields:
  if norm(x.get(af),bf)!=norm(y.get(bf),bf):
   diff.append([k,text(x['Material']),text(x['Serial Number']),bf,text(x.get(af)),text(y.get(bf)),x['_row'],y['_row']]);changed.add(k)
 if k not in changed:matched+=1
summary={'userRows':len(a),'hanaRows':len(b),'sharedEquipment':len(am.keys()&bm.keys()),'identicalEquipment':matched,'changedEquipment':len(changed),'fieldDifferences':len(diff),'onlyUser':len(only_a),'onlyHana':len(only_b),'comparedFields':len(fields),'userPrefixes':dict(Counter(text(r['Material'])[:1] for r in a)),'duplicateEquipment':duplicates}
payload={'summary':summary,'onlyUser':only_a,'onlyHana':only_b,'differences':diff,'fieldMap':fields,'sourcePaths':[str(source),str(hana)]}
(out/'comparison.json').write_text(json.dumps(payload,ensure_ascii=False,default=str),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))
print('Only user:',json.dumps([{k:r.get(k) for k in ['Equipment','Material','Serial Number','Plant','Storage location','Sales Order','System status','Created on','Changed On','_row']} for r in only_a],ensure_ascii=False,default=str))
print('Only HANA:',json.dumps(only_b,ensure_ascii=False,default=str))
print('Differences:',json.dumps(diff[:25],ensure_ascii=False))
