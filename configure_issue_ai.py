"""Provision the Windows account that runs planning's existing daily task."""
import argparse
import json
import os
from pathlib import Path

from issue_ai import (ROOT, ENDPOINT, C4CReader, IssueAIError, load_taxonomy, private_dir,
                      read_env, validate_config)
from sync_issue_subcategories import probe_model


def collection_credentials(path):
    payload=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    matches=[]
    def visit(node, inherited=None):
        if not isinstance(node,dict):
            return
        auth=node.get('auth') or inherited
        request=node.get('request')
        if isinstance(request,dict):
            url=request.get('url',{})
            raw=url if isinstance(url,str) else url.get('raw','')
            if raw.split('?')[0].replace('/DC4C/','/PC4C/') == ENDPOINT:
                candidate=request.get('auth') or auth or {}
                if candidate.get('type')!='basic' or request.get('method')!='GET':
                    raise IssueAIError('The supplied list request must use GET and Basic Auth')
                values={v['key']:v.get('value','') for v in candidate.get('basic',[])}
                if not values.get('username') or not values.get('password'):
                    raise IssueAIError('C4C list request credentials are missing')
                matches.append((values['username'],values['password']))
        for child in node.get('item',[]):
            visit(child,auth)
    visit(payload)
    if not matches or len(set(matches))!=1:
        raise IssueAIError('Could not identify one credential pair for the authorized C4C list endpoint')
    return matches[0]


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--openai-env',required=True)
    ap.add_argument('--c4c-collection',required=True)
    ap.add_argument('--model',default='gpt-5.5')
    ap.add_argument('--overwrite',action='store_true')
    args=ap.parse_args()
    try:
        config=read_env(args.openai_env)
        user,password=collection_credentials(args.c4c_collection)
        config={k:v for k,v in config.items() if k in {'OPENAI_API_KEY','OPENAI_BASE_URL','OPENAI_RESPONSES_URL'}}
        config.setdefault('OPENAI_BASE_URL','https://api.openai.com/v1')
        config.update(ISSUE_AI_MODEL=args.model,C4C_ISSUE_USERNAME=user,C4C_ISSUE_PASSWORD=password,
                      C4C_ISSUE_AI_ENABLED='1',ISSUE_AI_MAX_PER_RUN='250',ISSUE_AI_BATCH_SIZE='10')
        validate_config(config)
        target=(private_dir()/'settings.env').resolve()
        if target.is_relative_to(ROOT.resolve()):
            raise IssueAIError('Private settings must be outside the website/project directory')
        if target.exists() and not args.overwrite:
            raise IssueAIError('Private settings already exist; use --overwrite only to replace them deliberately')
        if any('\n' in v or '\r' in v or '\x00' in v for v in config.values()):
            raise IssueAIError('A configuration value contains an unsupported newline/control character')
        # Verify the selected model before enabling the scheduled hook.
        C4CReader(config).page('Z006',0,top=1)
        probe_model(config,load_taxonomy())
        target.parent.mkdir(parents=True,exist_ok=True)
        text='# Private settings for this Windows scheduled-task account. Never commit or serve.\n'
        text+='\n'.join(k+'="'+v+'"' for k,v in config.items())+'\n'
        temp=target.with_suffix('.env.tmp')
        temp.write_text(text,encoding='utf-8')
        try:
            os.chmod(temp,0o600)
        except OSError:
            pass  # Windows uses the account's inherited LOCALAPPDATA ACL.
        temp.replace(target)
        print('Issue AI enabled for this Windows account. Model: '+args.model)
        print('Private config: '+str(target))
        print('Run the existing daily BAT under this SAME Windows account. No new schedule is created.')
        return 0
    except Exception as error:
        print(str(error) if isinstance(error,IssueAIError) else 'Configuration failed: '+type(error).__name__)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
