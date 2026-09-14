"""Apply the reviewed postcode plan; change only each ticket's postcode leaf."""
import json
import re
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import firebase_admin
from firebase_admin import credentials, db

OUT=Path(__file__).resolve().parent
ROOT=OUT.parent.parent
before=json.loads((OUT/'firebase_before.json').read_text(encoding='utf-8'))
results=json.loads((OUT/'match_results.json').read_text(encoding='utf-8'))
assert len(results)==len(before['tickets'])==795
assert {r['ticket_id'] for r in results}==set(before['tickets'])
matched=[r for r in results if r['status']=='matched']
updates={r['ticket_id']+'/postcode':r['postcode'] for r in matched}
assert len(updates)==len(matched)
assert all(re.fullmatch(r'\d{4}',p) for p in updates.values())
assert all(before['tickets'][r['ticket_id']]['ticketType']=='Z011' for r in matched)
(OUT/'planned_updates.json').write_text(json.dumps(updates,indent=2),encoding='utf-8')

firebase_admin.initialize_app(credentials.Certificate(str(ROOT/'firebase-service-account.json')),{'databaseURL':'https://snowy-hr-report-default-rtdb.asia-southeast1.firebasedatabase.app','httpTimeout':30})
ref=db.reference('recallClaim/tickets')
current=ref.get()
assert current==before['tickets'], 'Tickets changed since the snapshot; regenerate the plan before applying.'
ref.update(updates)
after=db.reference('recallClaim').get()
(OUT/'firebase_after.json').write_text(json.dumps(after,ensure_ascii=False,indent=2),encoding='utf-8')
verified=[]
write_failures=[]
for r in matched:
    (verified if after['tickets'].get(r['ticket_id'],{}).get('postcode')==r['postcode'] else write_failures).append(r['ticket_id'])
expected=json.loads(json.dumps(before))
for r in matched: expected['tickets'][r['ticket_id']]['postcode']=r['postcode']
unchanged_other_fields=expected==after
failed=[r for r in results if r['status']!='matched']
reason_zh={'no_match':'三个表中未找到可匹配记录','unconfirmed_contact_identity':'联系方式有候选，但客户身份无法确认或仅为经销商通用邮箱','missing_postcode':'已匹配记录的邮编为空或格式无效','conflicting_postcodes':'候选来源邮编冲突，未自动选择'}
summary=dict(completed_at=datetime.now(timezone.utc).isoformat(),total=len(results),written_and_verified=len(verified),unmatched=len(failed),write_failures=write_failures,other_fields_unchanged=unchanged_other_fields,failure_reasons=dict(Counter(r['status'] for r in failed)),failed_ticket_ids=[r['ticket_id'] for r in failed]+write_failures)
(OUT/'result_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# Recall ticket 邮编写入结果','',f"总计 {len(results)}；成功写入并回读验证 {len(verified)}；未匹配 {len(failed)}；写入失败 {len(write_failures)}。",'', '写入路径：`recallClaim/tickets/{ticketId}/postcode`。邮编使用四位字符串，保留前导零。','', '## 未成功的 ticket','', '| Ticket ID | 原因 | 候选邮编 |','| --- | --- | --- |']
for r in failed:
    lines.append(f"| {r['ticket_id']} | {reason_zh[r['status']]} | {', '.join(r['candidate_postcodes'])} |")
for tid in write_failures: lines.append(f'| {tid} | 写入后未通过回读核验 | |')
lines+=['','## 匹配方式','', '优先使用 VIN、Serial ID／车架号、客户编号。无直接匹配时，使用精确邮箱／电话并以客户姓名佐证。对已确认的 VIN 重复或其他车主记录，使用 Serial ID 与客户邮箱共同复核。无法消除的邮编冲突保留为空。','', '## 原始来源','', '- VCUST.XLSX，Sheet1：客户编号 A 列，姓名 G/J 列，邮编 M 列，邮箱 AJ 列。','- Product_Registrations_Till_17072026.xlsx，Sheet38：车架号 D 列、VIN R 列、邮编 N 列。','- SAPAnalyticsReport(Z578DB6F34E1CDB92C9D1CE).xlsx，第 1 个工作表：表头第 7 行，Serial ID C 列、客户编号 I 列、邮编 T 列。','', '本次保留了写入前后本地备份及每条匹配证据。现有同步脚本会整节点覆盖 recallClaim，后续同步可能移除本次新增字段；本次未修改同步脚本。']
(OUT/'failed_tickets.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
(OUT/'failed_ticket_ids.txt').write_text('\n'.join(summary['failed_ticket_ids'])+'\n',encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
assert not write_failures, 'Some postcode writes did not verify.'
assert unchanged_other_fields, 'Other fields changed during the operation; inspect the snapshots.'
