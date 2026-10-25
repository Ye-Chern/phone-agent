import json
import threading
import unittest
import urllib.request
from unittest import mock

import agent
import test_agent_backend as backend_tests
from test_agent_backend import FakeResponse, text_response, tool_response


class IntegrationTests(unittest.TestCase):
    setUp = backend_tests.AgentBackendTests.setUp
    tearDown = backend_tests.AgentBackendTests.tearDown

    def test_http_failure_is_recoverable_after_reopen_without_resending_user(self):
        server = agent.ThreadingHTTPServer(('127.0.0.1', 0), agent.Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)

        def request(path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(base + path, data=data, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=4) as response:
                return response.read().decode()

        broken = FakeResponse([{'choices': [{'delta': {'content': 'partial'}, 'finish_reason': None}]}])
        try:
            with mock.patch.object(agent, 'api_request', side_effect=[broken, text_response('recovered')]), \
                    mock.patch.object(agent, 'load_secrets', return_value={'deepseek_key': 'fixture'}):
                first = request('/api/chat', {'conv_id': self.cid, 'message': 'original'})
                self.assertIn('"retryable": true', first)
                reopened = json.loads(request('/api/convs/' + self.cid + '/messages'))
                self.assertTrue(reopened['run']['retryable'])
                self.assertEqual(reopened['run']['status'], 'failed')
                retried = request('/api/chat', {'conv_id': self.cid, 'retry': True})
                self.assertIn('recovered', retried)
                self.assertIn('"status": "completed"', retried)
                final = json.loads(request('/api/convs/' + self.cid + '/messages'))
                self.assertFalse(final['run']['retryable'])
                self.assertEqual([m['content'] for m in final['messages'] if m['role'] == 'user'], ['original'])
                self.assertEqual(json.loads(request('/api/status'))['provider'], 'DeepSeek')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_unfinished_old_turn_is_not_executed_when_a_new_turn_is_sent(self):
        old_user = agent.msg_add(self.cid, 'user', 'abandoned task')
        old_run = agent.run_create(self.cid, old_user, agent.DEFAULT_MODEL)
        agent.run_update(old_run, 'interrupted', 'stopped')
        call = {'id': 'abandoned', 'type': 'function', 'function': {'name': 'notify', 'arguments': '{}'}}
        agent.msg_add(self.cid, 'assistant', '', {'tool_calls': [call], 'tool_results': {},
                      'tool_statuses': {'abandoned': 'pending'}, 'batch_status': 'pending', 'run_id': old_run})
        effect = mock.Mock(return_value={'ok': True, 'output': 'sent'})
        with mock.patch.dict(agent.TOOL_IMPL, {'notify': effect}), \
                mock.patch.object(agent, 'api_request', return_value=text_response()):
            agent.run_agent(self.cid, 'a different task', agent.DEFAULT_MODEL, lambda e: None)
        effect.assert_not_called()

    def test_crash_during_side_effect_does_not_repeat_the_uncertain_call(self):
        user_id = agent.msg_add(self.cid, 'user', 'notify')
        run_id = agent.run_create(self.cid, user_id, agent.DEFAULT_MODEL)
        call = {'id': 'uncertain', 'type': 'function', 'function': {'name': 'notify', 'arguments': '{}'}}
        agent.msg_add(self.cid, 'assistant', '', {'tool_calls': [call], 'tool_results': {},
                      'tool_statuses': {'uncertain': 'started'}, 'batch_status': 'pending', 'run_id': run_id})
        agent.recover_stale_runs()
        effect = mock.Mock(return_value={'ok': True, 'output': 'sent'})
        with mock.patch.dict(agent.TOOL_IMPL, {'notify': effect}), \
                mock.patch.object(agent, 'api_request', return_value=text_response()) as upstream:
            agent.run_agent(self.cid, '', agent.DEFAULT_MODEL, lambda e: None, retry=True)
        effect.assert_not_called()
        result = next(m for m in upstream.call_args.args[0]['messages'] if m.get('tool_call_id') == 'uncertain')
        self.assertFalse(json.loads(result['content'])['ok'])

    def test_missing_legacy_result_remains_valid_tool_protocol_and_honest(self):
        agent.msg_add(self.cid, 'user', 'old task')
        call = {'id': 'legacy', 'type': 'function', 'function': {'name': 'notify', 'arguments': '{}'}}
        agent.msg_add(self.cid, 'assistant', '', {'tool_calls': [call], 'tool_results': {}})
        tool = agent.history_for_agent(self.cid)[-1]
        self.assertEqual(tool['role'], 'tool')
        self.assertIsInstance(tool['content'], str)
        self.assertFalse(json.loads(tool['content'])['ok'])

    def test_view_image_cannot_split_a_multi_tool_result_batch(self):
        picture = self.root / 'fixture.png'
        picture.write_bytes(b'fixture image bytes')
        responses = [tool_response([('image', 'view_image', {'path': str(picture)}),
                                    ('list', 'list_files', {'path': str(self.root)})]), text_response()]
        with mock.patch.object(agent, 'api_request', side_effect=responses) as upstream:
            agent.run_agent(self.cid, 'inspect image then list', agent.DEFAULT_MODEL, lambda e: None)
        messages = upstream.call_args.args[0]['messages']
        index = next(i for i, m in enumerate(messages) if m.get('tool_calls'))
        self.assertEqual([m['role'] for m in messages[index + 1:index + 4]], ['tool', 'tool', 'user'])
        self.assertEqual([m['tool_call_id'] for m in messages[index + 1:index + 3]], ['image', 'list'])

    def test_http_400_is_not_automatically_retried(self):
        import io
        import urllib.error
        failure = urllib.error.HTTPError('https://api.deepseek.com', 400, 'bad request', {}, io.BytesIO(b'bad'))
        with mock.patch.object(agent, 'get_api_key_for', return_value='fixture'), \
                mock.patch('urllib.request.urlopen', side_effect=failure) as upstream:
            with self.assertRaises(urllib.error.HTTPError):
                agent.api_request({'model': agent.DEFAULT_MODEL, 'messages': []})
        self.assertEqual(upstream.call_count, 1)

    def test_image_injection_cannot_trim_away_its_real_user_instruction(self):
        agent.msg_add(self.cid, 'user', 'keep this real instruction')
        agent.msg_add(self.cid, 'assistant', 'some progress ' * 20)
        agent.msg_add(self.cid, 'user', 'image injected', {'injected': True})
        agent.msg_add(self.cid, 'assistant', 'answer')
        with mock.patch.object(agent, 'MAX_CONTEXT_CHARS', 32):
            messages = agent.history_for_agent(self.cid)
        self.assertEqual(messages[0]['content'], 'keep this real instruction')

    def test_second_server_cannot_mark_an_active_servers_runs_interrupted(self):
        with mock.patch('sys.argv', ['agent.py']), \
                mock.patch.object(agent, 'get_api_key', return_value='fixture'), \
                mock.patch.object(agent, 'ThreadingHTTPServer', side_effect=OSError('port busy')), \
                mock.patch.object(agent, 'recover_stale_runs') as recovery, \
                mock.patch('builtins.print'):
            with self.assertRaises(SystemExit):
                agent.main()
        recovery.assert_not_called()
