import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock
from pathlib import Path

import agent
import test_agent_backend as baseline


class ProviderHttpTests(unittest.TestCase):
    setUp=baseline.AgentBackendTests.setUp
    tearDown=baseline.AgentBackendTests.tearDown

    def test_configuration_only_returns_flags_and_blocks_busy_or_cross_origin_writes(self):
        server=agent.ThreadingHTTPServer(('127.0.0.1',0),agent.Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        base='http://127.0.0.1:'+str(server.server_port)+'/api/provider-settings'
        def call(body=None,origin=None):
            headers={'Content-Type':'application/json'}
            if origin:headers['Origin']=origin
            request=urllib.request.Request(base,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            try:
                with urllib.request.urlopen(request,timeout=4) as response:return response.status,json.load(response)
            except urllib.error.HTTPError as error:return error.code,json.load(error)
        try:
            with mock.patch.object(agent,'SECRETS_FILE',Path(agent.CONFIG_DIR)/'secrets.json'),mock.patch.dict(agent.os.environ,{'PHONEAGENT_DEEPSEEK_KEY':'','PHONEAGENT_GEMINI_KEY':'','PHONEAGENT_EXA_KEY':''}):
                self.assertFalse(call()[1]['deepseek_key_configured'])
                code,body=call({'deepseek_key':'fake-key-only-for-local-http-test'})
                self.assertEqual(code,200);self.assertTrue(body['deepseek_key_configured'])
                self.assertNotIn('fake-key',json.dumps(body))
                self.assertTrue(call({'deepseek_key':''})[1]['deepseek_key_configured'])
                self.assertEqual(call({'clear_deepseek_key':True},'https://attacker.example')[0],403)
                self.assertTrue(call()[1]['deepseek_key_configured'])
                with agent._active_lock:agent._active_runs[self.cid]=worker
                self.assertEqual(call({'clear_deepseek_key':True})[0],409)
                with agent._active_lock:agent._active_runs.pop(self.cid,None)
                self.assertFalse(call({'clear_deepseek_key':True})[1]['deepseek_key_configured'])
        finally:
            with agent._active_lock:agent._active_runs.pop(self.cid,None)
            server.shutdown();server.server_close();worker.join(3)
