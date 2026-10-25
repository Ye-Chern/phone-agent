import io
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import agent
from phoneagent_core import memory, policy, providers, quota, skills, storage
import test_agent_backend as baseline
from test_agent_backend import text_response, tool_response


class UpgradeTests(unittest.TestCase):
    setUp = baseline.AgentBackendTests.setUp
    tearDown = baseline.AgentBackendTests.tearDown
    # Only new tests; original suite still runs independently.
    def test_outside_access_without_selected_project_uses_home_base(self):
        cid=agent.conv_create('work')
        file=self.root/'outside-notes.txt';file.write_text('synthetic')
        with agent.db() as conn:
            info=storage.session(conn,cid)
            with self.assertRaises(ValueError):policy.prepare(conn,info,'read_file',{'path':str(file)},agent.CONFIG_DIR)
            args,waiting=policy.prepare(conn,info,'read_file',{'path':str(file)},agent.CONFIG_DIR,allow_outside=True)
            self.assertEqual(args['path'],str(file.resolve()));self.assertIsNone(waiting)
            args,waiting=policy.prepare(conn,info,'run_command',{'command':'echo synthetic'},agent.CONFIG_DIR,allow_outside=True,auto_approve=True)
            self.assertEqual(args['workdir'],str(Path.home().resolve()));self.assertIsNone(waiting)

    def test_work_budget_defaults_by_provider_and_preserves_explicit_choice(self):
        info=agent.session_info(self.cid)
        self.assertEqual(agent.work_context_settings(info,'chatgpt/gpt-6-luna')['settings']['work_context_budget'],24000)
        self.assertEqual(agent.work_context_settings(info,'deepseek-flash')['settings']['work_context_budget'],24000)
        self.assertEqual(agent.work_context_settings(info,'gemini-3.8-flash')['settings']['work_context_budget'],12000)
        info['settings']['work_context_budget']=10000
        self.assertEqual(agent.work_context_settings(info,'chatgpt/gpt-6-luna')['settings']['work_context_budget'],10000)

    def test_outside_workspace_permission_is_independent_and_revocable(self):
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as config, mock.patch.object(agent,'CONFIG_DIR',Path(config)):
            file=Path(outside)/'notes.txt';file.write_text('external synthetic notes')
            with agent.db() as conn:
                info=storage.session(conn,self.cid)
                with self.assertRaises(ValueError):policy.prepare(conn,info,'read_file',{'path':str(file)},agent.CONFIG_DIR)
            agent.set_approval_policy({'auto_approve_work':True})
            agent.set_approval_policy({'allow_outside_workspace':True})
            self.assertTrue(agent.approval_policy()['auto_approve_work'])
            result,_,_=agent._execute_tool({'function':{'name':'read_file','arguments':json.dumps({'path':str(file)})}},lambda e:None,self.cid)
            self.assertTrue(result['ok']);self.assertIn('external synthetic notes',result['output'])
            secret=Path(config)/'secret.txt';secret.write_text('not for model')
            with agent.db() as conn:
                with self.assertRaises(ValueError):policy.prepare(conn,info,'read_file',{'path':str(secret)},agent.CONFIG_DIR,allow_outside=True)
            agent.set_approval_policy({'allow_outside_workspace':False})
            self.assertTrue(agent.approval_policy()['auto_approve_work'])
            result,_,_=agent._execute_tool({'function':{'name':'read_file','arguments':json.dumps({'path':str(file)})}},lambda e:None,self.cid)
            self.assertFalse(result['ok'])
    def test_work_auto_approval_runs_without_prompt_and_can_be_revoked(self):
        with tempfile.TemporaryDirectory() as config, mock.patch.object(agent, 'CONFIG_DIR', Path(config)):
            self.assertFalse(agent.approval_policy()['auto_approve_work'])
            agent.set_approval_policy({'auto_approve_work': True})
            effect=mock.Mock(return_value={'ok':True,'output':'AUTO_OK'})
            with mock.patch.object(agent,'api_request',side_effect=[tool_response([('auto1','run_command',{'command':'echo AUTO_OK'})]),text_response()]) as request, mock.patch.dict(agent.TOOL_IMPL,{'run_command':effect}):
                events=[];agent.run_agent(self.cid,'自动批准测试','deepseek-flash',events.append)
            self.assertEqual(effect.call_count,1)
            self.assertEqual(events[-1]['status'],'completed')
            self.assertFalse(any(e['type']=='approval' for e in events))
            self.assertIn('用户已开启 Work 自动批准',request.call_args_list[0].args[0]['messages'][0]['content'])
            with agent.db() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM approvals WHERE state='auto-used'").fetchone()[0],1)
            agent.set_approval_policy({'auto_approve_work':False})
            effect.reset_mock()
            with mock.patch.object(agent,'api_request',return_value=tool_response([('manual1','run_command',{'command':'echo AUTO_OK'})])), mock.patch.dict(agent.TOOL_IMPL,{'run_command':effect}):
                events=[];agent.run_agent(self.cid,'关闭后的测试','deepseek-flash',events.append)
            effect.assert_not_called()
            self.assertTrue(any(e['type']=='approval' for e in events))

    def test_auto_approval_keeps_path_chat_and_explicit_rejection_guards(self):
        f=self.root/'existing.txt';f.write_text('original')
        chat=agent.conv_create('chat')
        with agent.db() as conn:
            info=storage.session(conn,self.cid)
            for name in ('write_file','delete_file','open_in_cxxdroid'):
                args,wait=policy.prepare(conn,info,name,{'path':str(f)},agent.CONFIG_DIR,auto_approve=True)
                self.assertIsNone(wait)
                self.assertEqual(args['path'],str(f.resolve()))
            with self.assertRaises(ValueError):policy.prepare(conn,info,'write_file',{'path':'../outside'},agent.CONFIG_DIR,auto_approve=True)
            with self.assertRaises(ValueError):policy.prepare(conn,info,'read_file',{'path':'.env'},agent.CONFIG_DIR,auto_approve=True)
            with self.assertRaises(ValueError):policy.prepare(conn,info,'delete_file',{'path':'.'},agent.CONFIG_DIR,auto_approve=True)
            with self.assertRaises(ValueError):policy.prepare(conn,storage.session(conn,chat),'run_command',{'command':'echo no'},agent.CONFIG_DIR,auto_approve=True)
            _,wait=policy.prepare(conn,info,'run_command',{'command':'echo rejected'},agent.CONFIG_DIR)
            conn.execute("UPDATE approvals SET state='rejected' WHERE id=?",(wait['approval_id'],));conn.commit()
            with self.assertRaises(ValueError):policy.prepare(conn,info,'run_command',{'command':'echo rejected'},agent.CONFIG_DIR,auto_approve=True)

    def test_approval_policy_config_preserves_values_and_rejects_bad_input(self):
        with tempfile.TemporaryDirectory() as config, mock.patch.object(agent,'CONFIG_DIR',Path(config)):
            target=Path(config)/'runtime.json'
            target.write_text(json.dumps({'quotas':{'deepseek-flash':{'local_rpd':10}},'allowed_hosts':['test.local']}))
            agent.set_approval_policy({'auto_approve_work':True})
            self.assertEqual(json.loads(target.read_text())['quotas']['deepseek-flash']['local_rpd'],10)
            for value in ('true',1,None):
                with self.assertRaises(ValueError):agent.set_approval_policy({'auto_approve_work':value})
            target.write_text('{broken')
            with self.assertRaises(ValueError):agent.set_approval_policy({'auto_approve_work':False})
            self.assertEqual(target.read_text(),'{broken')
    def test_cumulative_stream_usage_is_counted_once(self):
        response = baseline.FakeResponse([
            {'choices':[{'delta':{'content':'done'}}], 'usage':{'prompt_tokens':20,'completion_tokens':2,'total_tokens':22}},
            {'choices':[{'delta':{},'finish_reason':'stop'}], 'usage':{'prompt_tokens':20,'completion_tokens':5,'total_tokens':25}},
            {'usage':{'prompt_tokens':20,'completion_tokens':5,'total_tokens':25}},
            {'__done__':True}])
        with mock.patch.object(agent, 'api_request', return_value=response):
            events=[]
            agent.run_agent(self.cid,'hello','deepseek-flash',events.append)
        self.assertEqual(events[-1]['usage']['total_tokens'],25)
        self.assertEqual(len(events[-1]['usage_requests']),1)

    def test_import_preserves_sources_revisions_and_rolls_back_on_error(self):
        body={'format':'phoneagent-v2','session':{'mode':'chat','settings':{'workdir':'/','semantic_enabled':True}},
              'messages':[{'id':10,'role':'user','content':'更正偏好'}],
              'memories':[{'id':'old','kind':'preference','content':'旧偏好','source_id':10,'validity':'superseded'},
                          {'id':'new','kind':'preference','content':'新偏好','source_id':10,'revises':'old','validity':'active'}]}
        cid=agent.import_chat(body)
        self.assertNotIn('workdir',agent.session_info(cid)['settings'])
        self.assertFalse(agent.session_info(cid)['settings']['semantic_enabled'])
        with agent.db() as conn:
            imported=[dict(r) for r in conn.execute('SELECT * FROM memories WHERE conv_id=?',(cid,))]
        new=next(m for m in imported if m['content']=='新偏好')
        old=next(m for m in imported if m['content']=='旧偏好')
        self.assertEqual(new['revises'],old['id'])
        self.assertEqual(new['source_id'],agent.conv_messages(cid)[0]['id'])
        before=len(agent.conv_list())
        body['memories'][1]['source_id']=999
        with self.assertRaises(ValueError):agent.import_chat(body)
        self.assertEqual(len(agent.conv_list()),before)

    def test_http_mode_security_busy_and_data_roundtrip(self):
        server=agent.ThreadingHTTPServer(('127.0.0.1',0),agent.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)+'/api/'
        def call(route,body=None,headers=None):
            req=urllib.request.Request(base+route,data=json.dumps(body).encode() if body is not None else None,
                headers={'Content-Type':'application/json',**(headers or {})})
            try:
                with urllib.request.urlopen(req,timeout=4) as r:return r.status,json.load(r)
            except urllib.error.HTTPError as e:return e.code,json.load(e)
        try:
            self.assertEqual(call('status',headers={'Host':'attacker.example'})[0],403)
            self.assertEqual(call('convs',{'mode':'chat'},headers={'Origin':'https://attacker.example'})[0],403)
            self.assertEqual(call('approval-policy',{'auto_approve_work':True},headers={'Origin':'https://attacker.example'})[0],403)
            self.assertEqual(call('approval-policy',{'auto_approve_work':'yes'})[0],400)
            with tempfile.TemporaryDirectory() as config, mock.patch.object(agent,'CONFIG_DIR',Path(config)):
                self.assertEqual(call('approval-policy',{'auto_approve_work':True})[1],{'auto_approve_work':True,'allow_outside_workspace':False})
                self.assertTrue(call('approval-policy')[1]['auto_approve_work'])
                self.assertEqual(call('approval-policy',{'auto_approve_work':False})[0],200)
            status,data=call('convs',{'mode':'chat'});self.assertEqual(status,201);cid=data['id']
            self.assertEqual(call('convs/'+cid+'/settings',{'workdir':str(self.root)})[0],400)
            with agent._active_lock:agent._active_runs[cid]=thread
            self.assertEqual(call('convs/'+cid+'/settings',{'identity':'new'})[0],409)
            with self.assertRaises(ValueError):agent.conv_delete(cid)
            with self.assertRaises(ValueError):agent.conv_delete_all()
            with agent._active_lock:agent._active_runs.pop(cid,None)
            call('convs/'+cid+'/memories',{'content':'人工确认的合成记忆','epistemic':'confirmed'})
            exported=call('convs/'+cid+'/export')[1]
            status,imported=call('import',exported);self.assertEqual(status,201)
            self.assertEqual(call('convs/'+imported['id']+'/memories')[1]['memories'][0]['content'],'人工确认的合成记忆')
        finally:
            server.shutdown();server.server_close();thread.join(3)

    def test_pacific_reset_uses_summer_and_winter_offsets(self):
        from datetime import datetime, timezone
        self.assertEqual(quota.pacific_day(datetime(2026,7,2,6,59,tzinfo=timezone.utc).timestamp()),'2026-07-01')
        self.assertEqual(quota.pacific_day(datetime(2026,7,2,7,0,tzinfo=timezone.utc).timestamp()),'2026-07-02')
        self.assertEqual(quota.pacific_day(datetime(2026,1,2,7,59,tzinfo=timezone.utc).timestamp()),'2026-01-01')
        self.assertEqual(quota.pacific_day(datetime(2026,1,2,8,0,tzinfo=timezone.utc).timestamp()),'2026-01-02')

    def test_native_google_parts_preserve_signature_on_text_and_empty_parts(self):
        parts=[{'text':'thinking','thought':True,'thoughtSignature':'sig-text'},
               {'thoughtSignature':'sig-empty'}, {'functionCall':{'name':'list_files','args':{'path':'.'}},'thoughtSignature':'sig-call'}]
        body=providers.google_payload({'model':'gemini-3.8-flash','messages':[
            {'role':'assistant','content':'ignored duplicate','google_parts':parts,
             'tool_calls':[{'id':'one','function':{'name':'list_files','arguments':'{"path":"."}'}}]},
            {'role':'tool','tool_call_id':'one','content':'{"ok":true}'}]})
        self.assertEqual(body['contents'][0]['parts'],parts)
        self.assertEqual(body['contents'][1]['parts'][0]['functionResponse']['name'],'list_files')

    def test_modes_migration_and_chat_tool_denial(self):
        chat = agent.conv_create('chat')
        self.assertEqual(agent.session_info(self.cid)['mode'], 'work')
        self.assertEqual(agent.session_info(chat)['mode'], 'chat')
        with agent.db() as c:
            with self.assertRaises(ValueError):
                storage.update_session(c, self.cid, {'mode':'chat'}, agent.CONFIG_DIR)
            with self.assertRaises(ValueError):
                policy.prepare(c, storage.session(c, chat), 'notify', {}, agent.CONFIG_DIR)
        effect=mock.Mock()
        with mock.patch.object(agent, 'api_request', return_value=tool_response([('evil','notify',{})])), mock.patch.dict(agent.TOOL_IMPL, {'notify':effect}):
            events=[]
            agent.run_agent(chat,'hello','deepseek-flash',events.append)
        effect.assert_not_called()
        self.assertIn('Chat',events[-1]['error'])

    def test_chat_archive_memory_correction_and_source(self):
        chat=agent.conv_create('chat')
        source=agent.msg_add(chat,'user','记住我喜欢茉莉茶')
        hallucination=agent.msg_add(chat,'assistant','你已经付款')
        with agent.db() as c:
            mid=memory.add(c,chat,{'kind':'preference','content':'喜欢茉莉茶','source_id':source})
            with self.assertRaises(ValueError):
                memory.add(c,chat,{'content':'付款成功','source_id':hallucination})
            with self.assertRaises(ValueError):
                memory.add(c,chat,{'content':'跨会话','source_id':agent.msg_add(self.cid,'user','work')})
            revised=memory.add(c,chat,{'content':'喜欢龙井茶','kind':'preference','epistemic':'confirmed','revises':mid,'source_id':source},human=True)
            rows=memory.retrieve(c,chat,'喜欢什么茶')
            self.assertNotIn(mid,[r['id'] for r in rows])
            self.assertIn(revised,[r['id'] for r in rows])
        self.assertEqual(len(agent.conv_messages(chat)),2)

    def test_memory_candidates_idempotent_and_secret_filter(self):
        chat=agent.conv_create('chat')
        source=agent.msg_add(chat,'user','记住我的名字是阿澄')
        with agent.db() as c:
            self.assertEqual(len(memory.candidates(c,chat,agent.conv_messages(chat))),1)
            self.assertEqual(memory.candidates(c,chat,agent.conv_messages(chat)),[])
            secret=memory.add(c,chat,{'content':'阿澄的秘密门钥匙','audience':'阿澄','source_id':source},human=True)
            self.assertNotIn(secret,[r['id'] for r in memory.retrieve(c,chat,'秘密门钥匙',perspective='旅人')])
            self.assertIn(secret,[r['id'] for r in memory.retrieve(c,chat,'秘密门钥匙',perspective='阿澄')])

    def test_private_memory_source_is_also_excluded_from_recent_turns(self):
        chat=agent.conv_create('chat')
        source=agent.msg_add(chat,'user','Mira秘密暗号是bluebird')
        agent.msg_add(chat,'assistant','我知道暗号bluebird')
        with agent.db() as c:
            memory.add(c,chat,{'content':'秘密暗号bluebird','source_id':source,'audience':'Mira'},human=True)
            info=storage.update_session(c,chat,{'perspective':'Noah'},agent.CONFIG_DIR)
        agent.msg_add(chat,'user','Noah现在知道什么？')
        with agent.db() as c:
            messages,meta=memory.context(c,info,agent.conv_messages(chat),'Noah现在知道什么？')
        self.assertNotIn('bluebird',json.dumps(messages))
        self.assertEqual(meta['hidden_source_turns'],1)
        self.assertEqual(len(agent.conv_messages(chat)),3)

    def test_context_bounded_no_work_content_and_memory_off(self):
        chat=agent.conv_create('chat')
        for n in range(100):
            agent.msg_add(chat,'user','最近消息'+str(n)+' x'*60)
            agent.msg_add(chat,'assistant','回复'+str(n)+' y'*60)
        with agent.db() as c:
            info=storage.update_session(c,chat,{'context_budget':1000,'memory_enabled':False},agent.CONFIG_DIR)
            messages,meta=memory.context(c,info,agent.conv_messages(chat),'最近消息99')
        self.assertLessEqual(meta['estimated_input_tokens'],1000)
        self.assertEqual(meta['memory_ids'],[])
        self.assertIn('99',messages[-1]['content'])
        self.assertGreater(len(agent.conv_messages(chat)),len(messages))

    def test_images_are_parts_and_estimate_not_base64_size(self):
        chat=agent.conv_create('chat')
        image='data:image/png;base64,'+'a'*1000000
        agent.msg_add(chat,'user',json.dumps(agent.build_user_content('图中是什么',[image])))
        with agent.db() as c:
            messages,meta=memory.context(c,storage.session(c,chat),agent.conv_messages(chat),'图中是什么')
        self.assertIsInstance(messages[-1]['content'],list)
        self.assertLess(meta['estimated_input_tokens'],5000)
        self.assertIn('inlineData', providers.google_payload({'model':'gemma-4-31b-it','messages':messages})['contents'][-1]['parts'][-1])

    def test_paths_symlink_and_permission_expiry(self):
        out=self.root.parent/'outside.txt'
        with self.assertRaises(ValueError): policy.resolve_path(self.root,'../outside.txt',agent.CONFIG_DIR)
        with self.assertRaises(ValueError): policy.resolve_path(self.root,str(out),agent.CONFIG_DIR)
        with self.assertRaises(ValueError): policy.resolve_path(self.root,'secrets.json',agent.CONFIG_DIR)
        with self.assertRaises(ValueError): policy.validate_root('content://tree/x',agent.CONFIG_DIR)
        try:
            (self.root/'link').symlink_to(self.root.parent,target_is_directory=True)
        except OSError:
            pass
        else:
            with self.assertRaises(ValueError): policy.resolve_path(self.root,'link/outside.txt',agent.CONFIG_DIR)
        with self.assertRaises(OSError): policy.resolve_path(self.root/'missing','file',agent.CONFIG_DIR)

    def test_approval_bound_to_arguments_consumed_once_and_rejected(self):
        with agent.db() as c:
            info=storage.session(c,self.cid)
            args={'command':'echo hello'}
            _,waiting=policy.prepare(c,info,'run_command',args,agent.CONFIG_DIR)
            aid=waiting['approval_id']
            c.execute("UPDATE approvals SET state='approved' WHERE id=?",(aid,));c.commit()
            normalized,waiting=policy.prepare(c,info,'run_command',args,agent.CONFIG_DIR)
            self.assertIsNone(waiting);self.assertEqual(normalized['workdir'],str(self.root))
            _,waiting=policy.prepare(c,info,'run_command',args,agent.CONFIG_DIR)
            self.assertNotEqual(waiting['approval_id'],aid)
            c.execute("UPDATE approvals SET state='rejected' WHERE id=?",(waiting['approval_id'],));c.commit()
            with self.assertRaises(ValueError): policy.prepare(c,info,'run_command',args,agent.CONFIG_DIR)

    def test_approval_retry_executes_exact_tool_once(self):
        effect=mock.Mock(return_value={'ok':True,'output':'done'})
        with mock.patch.object(agent,'api_request',return_value=tool_response([('cmd','run_command',{'command':'echo safe'})])),mock.patch.dict(agent.TOOL_IMPL,{'run_command':effect}):
            events=[];agent.run_agent(self.cid,'run echo',agent.DEFAULT_MODEL,events.append)
        effect.assert_not_called()
        self.assertEqual(events[-1]['status'],'awaiting_approval')
        aid=next(e['approval_id'] for e in events if e['type']=='approval')
        with agent.db() as c:c.execute("UPDATE approvals SET state='approved' WHERE id=?",(aid,));c.commit()
        with mock.patch.object(agent,'api_request',return_value=text_response()),mock.patch.dict(agent.TOOL_IMPL,{'run_command':effect}):
            events=[];agent.run_agent(self.cid,'',agent.DEFAULT_MODEL,events.append,retry=True)
        self.assertEqual(effect.call_count,1)
        self.assertEqual(events[-1]['status'],'completed')
        self.assertEqual(sum(r['role']=='user' for r in agent.conv_messages(self.cid)),1)

    def test_quota_persisted_actual_usage_cooldown_and_concurrency(self):
        model='gemma-4-31b-it'
        limits={'rpm':2,'rpd':3,'tpm':100,'reserve':1}
        with agent.db() as c:
            first=quota.reserve(c,model,20,limits,now=10000)
            quota.complete(c,first,{'prompt_tokens':15,'completion_tokens':5})
        with agent.db() as c:
            second=quota.reserve(c,model,30,limits,now=10001)
            with self.assertRaises(quota.QuotaExceeded): quota.reserve(c,model,1,limits,now=10002)
            quota.complete(c,second,status='http-429',cooldown=12000)
            with self.assertRaises(quota.QuotaExceeded): quota.reserve(c,model,1,{},now=11000)
        results=[]
        def reserve():
            try:
                with agent.db() as c:quota.reserve(c,'concurrent',1,{'local_rpd':1})
                results.append('ok')
            except quota.QuotaExceeded:results.append('blocked')
        threads=[threading.Thread(target=reserve) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sorted(results),['blocked','ok'])

    def test_google_stream_tools_usage_signature_roundtrip(self):
        native=[{'candidates':[{'content':{'parts':[{'functionCall':{'name':'list_files','args':{'path':'.'}},'thoughtSignature':'opaque'}]},'finishReason':'STOP'}],
                 'usageMetadata':{'promptTokenCount':12,'candidatesTokenCount':4,'thoughtsTokenCount':2,'totalTokenCount':18}}]
        raw=io.BytesIO(b''.join(('data: '+json.dumps(e)+'\n\n').encode() for e in native))
        response=providers.GoogleStream(raw)
        events=[]
        while line:=response.readline(): events.append(agent.parse_sse_line(line.decode()))
        call=next(e['choices'][0]['delta']['tool_calls'][0] for e in events if e.get('choices') and e['choices'][0]['delta'].get('tool_calls'))
        self.assertEqual(call['thought_signature'],'opaque')
        self.assertEqual(events[-2]['usage']['completion_tokens'],6)
        self.assertTrue(events[-1]['__done__'])
        body=providers.google_payload({'model':'gemini-3.8-flash','messages':[{'role':'assistant','tool_calls':[call]}, {'role':'tool','tool_call_id':call['id'],'content':'{"ok":true}'}]})
        self.assertEqual(body['contents'][0]['parts'][0]['thoughtSignature'],'opaque')
        self.assertEqual(body['contents'][1]['parts'][0]['functionResponse']['name'],'list_files')

    def test_skills_discovery_lazy_references_dependencies_and_escape(self):
        root=self.root/'skills';p=root/'demo';p.mkdir(parents=True)
        (p/'SKILL.md').write_text('---\nname: demo\ndescription: do something\nversion: 1\n---\nDetailed instructions',encoding='utf-8')
        (p/'extra.md').write_text('reference')
        items=skills.discover(root)
        self.assertEqual(items[0]['description'],'do something')
        self.assertNotIn('Detailed',json.dumps(items))
        self.assertIn('reference',skills.load(items,'demo','extra.md'))
        with self.assertRaises(ValueError):skills.load(items,'demo','../../outside')
        with self.assertRaises(ValueError):skills.load(skills.discover(root,disabled=['demo']),'demo')

    def test_corrupt_database_is_not_replaced(self):
        target=self.root/'corrupt.db';target.write_bytes(b'not a database')
        with mock.patch.object(agent,'DB_FILE',target):
            with self.assertRaises(sqlite3.DatabaseError):agent.db()
        self.assertEqual(target.read_bytes(),b'not a database')

    def test_google_429_no_auto_retry_and_persisted_block(self):
        model='gemma-4-31b-it'
        exc=urllib.error.HTTPError('https://generativelanguage.googleapis.com',429,'rate',{'Retry-After':'2'},io.BytesIO(b'private error'))
        with mock.patch.object(agent,'get_api_key_for',return_value='fixture'),mock.patch.object(providers,'stream',side_effect=exc) as req:
            with self.assertRaises(urllib.error.HTTPError):agent.api_request({'model':model,'messages':[]})
            with self.assertRaises(quota.QuotaExceeded):agent.api_request({'model':model,'messages':[]})
        self.assertEqual(req.call_count,1)


if __name__=='__main__':unittest.main()
