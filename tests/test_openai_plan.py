import io
import json
import unittest
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import agent
import test_agent_backend as baseline
from phoneagent_core import openai_plan, storage


def wire(events):
    return io.BytesIO(b''.join(('data: ' + json.dumps(event) + '\n\n').encode() for event in events))


def consume(stream):
    result=[]
    while raw:=stream.readline():
        result.append(agent.parse_sse_line(raw.decode().strip()))
    return result


class ResponsesTests(unittest.TestCase):
    def test_preview_payload_namespace_native_replay_and_images(self):
        native=[{'id':'reason','type':'reasoning','encrypted_content':'opaque'},
                {'id':'fc','type':'function_call','call_id':'call','namespace':'phoneagent','name':'read_file','arguments':'{"path":"x"}'}]
        source={'model':'chatgpt/server-model','temperature':0.7,'max_tokens':300,'reasoning_effort':'high',
                'messages':[{'role':'system','content':'current rules'},
                    {'role':'user','content':[{'type':'text','text':'hello'},{'type':'image_url','image_url':{'url':'data:image/png;base64,YQ=='}}]},
                    {'role':'assistant','content':'do not duplicate','openai_items':native},
                    {'role':'tool','tool_call_id':'call','content':'{"ok":true}'}],
                'tools':[{'type':'function','function':{'name':'read_file','description':'read','parameters':{'type':'object'}}}]}
        result=openai_plan.payload(source)
        self.assertFalse(result['store']);self.assertTrue(result['stream'])
        self.assertEqual(result['model'],'server-model')
        self.assertEqual(result['instructions'],'current rules')
        self.assertEqual(result['reasoning'],{'effort':'high'})
        self.assertNotIn('temperature',result);self.assertNotIn('max_output_tokens',result)
        self.assertNotIn('previous_response_id',result)
        self.assertEqual(result['input'][0]['content'][1]['type'],'input_image')
        self.assertEqual(result['input'][1:3],native)
        self.assertEqual(result['input'][-1]['namespace'],'phoneagent')
        self.assertEqual(result['tools'][0]['type'],'namespace')

    def test_completed_stream_usage_and_tool_call_emitted_once(self):
        call={'id':'fc','type':'function_call','call_id':'one','namespace':'phoneagent','name':'list_files','arguments':'{"path":"."}'}
        output=[{'id':'r','type':'reasoning','encrypted_content':'encrypted'},call]
        result=consume(openai_plan.ResponsesStream(wire([
            {'type':'response.output_item.done','item':call},
            {'type':'response.completed','response':{'status':'completed','output':output,'usage':{'input_tokens':30,'output_tokens':8,'total_tokens':38}}}])))
        calls=[e for e in result if e and e.get('choices') and e['choices'][0]['delta'].get('tool_calls')]
        self.assertEqual(len(calls),1)
        self.assertEqual([e['openai_item'] for e in result if e and e.get('openai_item')],output)
        self.assertEqual(next(e['usage'] for e in result if e and 'usage' in e)['total_tokens'],38)
        self.assertTrue(result[-1]['__done__'])

    def test_usage_limit_after_text_and_eof_never_completed(self):
        stream=openai_plan.ResponsesStream(wire([
            {'type':'response.output_text.delta','delta':'partial'},
            {'type':'response.failed','response':{'error':{'code':'subscription_sharing_usage_limit_exceeded','message':'private upstream detail'}}}]))
        self.assertIn('partial',stream.readline().decode())
        with self.assertRaisesRegex(RuntimeError,'套餐使用受限'):stream.readline()
        eof=consume(openai_plan.ResponsesStream(wire([{'type':'response.output_text.delta','delta':'partial'}])))
        self.assertFalse(any(e and e.get('__done__') for e in eof))
        with self.assertRaises(RuntimeError):consume(openai_plan.ResponsesStream(wire([{'type':'response.incomplete'}])))


class WorkContextIntegrationTests(unittest.TestCase):
    setUp=baseline.AgentBackendTests.setUp
    tearDown=baseline.AgentBackendTests.tearDown

    def test_account_catalog_controls_image_input_without_disabling_luna(self):
        catalog = [
            {'slug': 'gpt-6-luna', 'input_modalities': ['text', 'image']},
            {'slug': 'text-only', 'input_modalities': ['text']},
            {'slug': 'unspecified'},
        ]
        with mock.patch.object(agent.chatgpt_auth, 'models', return_value=catalog):
            registry = agent.model_registry()
        self.assertTrue(registry['chatgpt/gpt-6-luna']['vision'])
        self.assertFalse(registry['chatgpt/text-only']['vision'])
        self.assertFalse(registry['chatgpt/unspecified']['vision'])

    def test_session_reasoning_is_in_actual_payload_and_other_modes_are_independent(self):
        with agent.db() as conn:
            storage.update_session(conn,self.cid,{'reasoning_effort':'high'},agent.CONFIG_DIR)
        with mock.patch.object(agent,'api_request',return_value=baseline.text_response()) as request:
            events=[];agent.run_agent(self.cid,'synthetic request','deepseek-flash',events.append)
        self.assertEqual(request.call_args.args[0]['reasoning_effort'],'high')
        chat=agent.conv_create('chat')
        self.assertEqual(agent.session_reasoning_effort(agent.session_info(chat),'deepseek-flash'),'low')
        self.assertEqual(agent.session_info(self.cid)['settings']['reasoning_effort'],'high')

    def test_account_mutations_and_callback_are_blocked_during_work(self):
        server=agent.ThreadingHTTPServer(('127.0.0.1',0),agent.Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def call(path,body=None):
            request=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(request,timeout=3) as response:return response.status,json.load(response)
            except urllib.error.HTTPError as error:return error.code,json.load(error)
        try:
            with agent._active_lock:agent._active_runs[self.cid]=worker
            with mock.patch.object(agent.chatgpt_auth,'finish_login') as finish:
                self.assertEqual(call('/auth/callback?state=synthetic&code=synthetic')[0],409)
                finish.assert_not_called()
            for action in ('login','select','logout'):
                self.assertEqual(call('/api/chatgpt/'+action,{})[0],409)
            self.assertEqual(call('/api/convs/'+self.cid+'/context',{'enabled':False})[0],409)
            with agent._active_lock:agent._active_runs.pop(self.cid,None)
            status,data=call('/api/chatgpt/accounts')
            self.assertEqual(status,200);self.assertTrue(data['available']);self.assertFalse(data['plan_ready'])
            status,data=call('/api/convs/'+self.cid+'/context',{'enabled':False,'budget':64000})
            self.assertEqual(status,200);self.assertFalse(data['enabled'])
        finally:
            with agent._active_lock:agent._active_runs.pop(self.cid,None)
            server.shutdown();server.server_close();worker.join(3)

    def test_work_compaction_persists_and_includes_actual_summary_usage(self):
        with agent.db() as conn:
            storage.update_session(conn,self.cid,{'work_context_budget':10000},agent.CONFIG_DIR)
        for i in range(15):
            agent.msg_add(self.cid,'user','synthetic task '+str(i)+' '+'old fact '*70)
            agent.msg_add(self.cid,'assistant','completed '+'synthetic ack '*70)
        with mock.patch.object(agent,'api_request',side_effect=[baseline.text_response('任务：合成任务；已完成：旧阶段；下一步：最新请求'),baseline.text_response('done')]) as request:
            events=[];agent.run_agent(self.cid,'latest safe request','deepseek-flash',events.append)
        self.assertEqual(request.call_count,2)
        self.assertEqual(events[-1]['status'],'completed')
        context=next(e for e in events if e['type']=='context')
        self.assertEqual(context['compaction']['status'],'compacted')
        self.assertLessEqual(context['estimated_input_tokens'],context['budget'])
        self.assertEqual(len(events[-1]['usage_requests']),2)
        with agent.db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM work_compaction_checkpoints WHERE conv_id=?',(self.cid,)).fetchone()[0],1)
        self.assertEqual(sum(r['role']=='user' for r in agent.conv_messages(self.cid)),16)

    def test_delete_session_removes_checkpoints_without_unknown_table(self):
        with agent.db() as conn:
            conn.execute('INSERT INTO work_compaction_checkpoints(conv_id,start_id,through_id,source_hash,model,summary,created_ts) VALUES(?,?,?,?,?,?,?)',
                         (self.cid,1,2,'hash','model','summary',1));conn.commit()
        agent.conv_delete(self.cid)
        with agent.db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM work_compaction_checkpoints').fetchone()[0],0)
