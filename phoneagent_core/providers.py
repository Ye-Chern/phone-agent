"""Native Google REST adapter -> existing OpenAI-shaped streaming harness."""
import json
import urllib.request
import uuid

GOOGLE_BASE = 'https://generativelanguage.googleapis.com/v1beta'
REGISTRY = {
    'deepseek-flash': {'label': 'DeepSeek V4.1 Flash', 'provider': 'deepseek', 'modes': ['work', 'chat'], 'tools': True, 'vision': True, 'limits': {}},
    'gemma-4-26b-a4b-it': {'label': 'Gemma 4 26B A4B', 'provider': 'google', 'modes': ['chat'], 'tools': False, 'vision': True, 'limits': {'rpm': 30, 'tpm': 16000, 'rpd': 14400, 'local_rpd': 100, 'reserve': 2}},
    'gemma-4-31b-it': {'label': 'Gemma 4 31B', 'provider': 'google', 'modes': ['chat'], 'tools': False, 'vision': True, 'limits': {'rpm': 30, 'tpm': 16000, 'rpd': 14400, 'local_rpd': 100, 'reserve': 2}},
    'gemini-3.5-flash-lite': {'label': 'Gemini 3.5 Flash-Lite', 'provider': 'google', 'modes': ['work'], 'tools': True, 'vision': True, 'limits': {'rpm': 10, 'tpm': 16000, 'rpd': 500, 'local_rpd': 100, 'reserve': 2}},
    'gemini-3.8-flash': {'label': 'Gemini 3.8 Flash', 'provider': 'google', 'modes': ['work'], 'tools': True, 'vision': True, 'limits': {'rpm': 5, 'tpm': 16000, 'rpd': 20, 'local_rpd': 20, 'reserve': 2}},
    'gemini-embedding-2': {'label': 'Gemini Embedding 2', 'provider': 'google', 'modes': [], 'tools': False, 'vision': False, 'limits': {'rpm': 100, 'tpm': 30000, 'rpd': 1000, 'local_rpd': 100, 'reserve': 5}}
}


def google_payload(payload):
    contents, system = [], []
    call_names = {}
    for msg in payload['messages']:
        if msg['role'] == 'system':
            system.append({'text': msg['content']})
            continue
        role = 'model' if msg['role'] == 'assistant' else 'user'
        parts = []
        if msg['role'] == 'tool':
            value = msg.get('content') or ''
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                pass
            parts = [{'functionResponse': {'name': call_names.get(msg['tool_call_id'], msg['tool_call_id']),
                                           'response': value if isinstance(value, dict) else {'output': value}}}]
        else:
            native_parts = msg.get('google_parts') if role == 'model' else None
            value = '' if native_parts else (msg.get('content') or '')
            if native_parts:
                parts.extend(native_parts)
            if isinstance(value, list):
                for item in value:
                    if item.get('type') == 'text':
                        parts.append({'text': item['text']})
                    elif item.get('type') == 'image_url':
                        url = item['image_url']['url']
                        if url.startswith('data:') and ';base64,' in url:
                            header, data = url.split(';base64,', 1)
                            parts.append({'inlineData': {'mimeType': header[5:], 'data': data}})
            elif value:
                parts.append({'text': str(value)})
            for tc in msg.get('tool_calls', []):
                fn = tc['function']
                call_names[tc['id']] = fn['name']
                part = {'functionCall': {'name': fn['name'], 'args': json.loads(fn['arguments'])}}
                if tc.get('thought_signature'):
                    part['thoughtSignature'] = tc['thought_signature']
                if not native_parts:
                    parts.append(part)
        if parts:
            # Consecutive tool responses belong to one user Content.
            if contents and contents[-1]['role'] == role:
                contents[-1]['parts'].extend(parts)
            else:
                contents.append({'role': role, 'parts': parts})
    body = {'contents': contents, 'generationConfig': {'maxOutputTokens': payload.get('max_tokens', 1024)}}
    if system:
        body['systemInstruction'] = {'parts': system}
    if payload.get('temperature') is not None:
        body['generationConfig']['temperature'] = payload['temperature']
    if payload.get('tools'):
        body['tools'] = [{'functionDeclarations': [t['function'] for t in payload['tools']]}]
    return body


def request(path, key, body=None, timeout=90):
    req = urllib.request.Request(GOOGLE_BASE + path,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        headers={'x-goog-api-key': key, 'Content-Type': 'application/json'})
    return urllib.request.urlopen(req, timeout=timeout)


class GoogleStream:
    def __init__(self, response):
        self.response = response
        self.fp = getattr(response, 'fp', None)
        self.pending = []
        self.finished = False
        self.call_index = 0
        self.has_tools = False

    def readline(self):
        while not self.pending:
            raw = self.response.readline()
            if not raw:
                if self.finished:
                    self.finished = False
                    return b'data: [DONE]\n'
                return b''
            if not raw.startswith(b'data:'):
                continue
            event = json.loads(raw[5:].strip())
            if 'error' in event:
                raise RuntimeError('Google 流返回 API 错误')
            for candidate in event.get('candidates', [])[:1]:
                for part in candidate.get('content', {}).get('parts', []):
                    self.pending.append({'google_part': part})
                    delta = {}
                    if part.get('text'):
                        delta['reasoning_content' if part.get('thought') else 'content'] = part['text']
                    if part.get('functionCall'):
                        fn = part['functionCall']
                        self.has_tools = True
                        tc = {'index': self.call_index, 'id': fn.get('id') or uuid.uuid4().hex,
                              'function': {'name': fn['name'], 'arguments': json.dumps(fn.get('args', {}), ensure_ascii=False)}}
                        if part.get('thoughtSignature'):
                            tc['thought_signature'] = part['thoughtSignature']
                        delta['tool_calls'] = [tc]
                        self.call_index += 1
                    if delta:
                        self.pending.append({'choices': [{'delta': delta, 'finish_reason': None}]})
                finish = candidate.get('finishReason')
                if finish:
                    if finish not in {'STOP', 'MAX_TOKENS'}:
                        raise RuntimeError('Google 生成终止: ' + finish)
                    reason = 'length' if finish == 'MAX_TOKENS' else ('tool_calls' if self.has_tools else 'stop')
                    self.pending.append({'choices': [{'delta': {}, 'finish_reason': reason}]})
                    self.finished = True
            if event.get('usageMetadata'):
                u = event['usageMetadata']
                self.pending.append({'choices': [], 'usage': {'prompt_tokens': u.get('promptTokenCount', 0),
                    'completion_tokens': u.get('candidatesTokenCount', 0) + u.get('thoughtsTokenCount', 0),
                    'total_tokens': u.get('totalTokenCount', 0)}})
        return ('data: ' + json.dumps(self.pending.pop(0), ensure_ascii=False) + '\n').encode()

    def close(self):
        self.response.close()


def stream(payload, key, timeout=300):
    return GoogleStream(request('/models/' + payload['model'] + ':streamGenerateContent?alt=sse',
                                key, google_payload(payload), timeout))


def embed(text, key, purpose='document', dimensions=768):
    # Embedding 2 uses prompt instructions, no taskType. One text -> one vector.
    instruction = ('task: search result | query: ' if purpose == 'query'
                   else 'title: conversation memory | text: ')
    body = {'model': 'models/gemini-embedding-2',
            'content': {'parts': [{'text': instruction + text}]}, 'outputDimensionality': dimensions}
    with request('/models/gemini-embedding-2:embedContent', key, body) as response:
        result = json.load(response)
    vector = result['embedding']['values']
    if len(vector) != dimensions:
        raise ValueError('Embedding 维度与配置不一致')
    return vector
