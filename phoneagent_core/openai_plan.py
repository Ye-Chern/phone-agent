"""Public Responses API adapter for SIWC-authorized ChatGPT plan tokens.

No ChatGPT backend endpoints, API billing fallback, or server-side history.
"""
import json
import re
import urllib.request
from collections import deque

BASE = 'https://api.openai.com/v1'
NAMESPACE = 'phoneagent'


def payload(source):
    items, instructions, namespaces = [], [], {}
    for message in source['messages']:
        role = message['role']
        if role in {'system', 'developer'}:
            instructions.append(str(message.get('content') or ''))
            continue
        native = message.get('openai_items') if role == 'assistant' else None
        if native:
            items.extend(native)
            for item in native:
                if item.get('type') == 'function_call':
                    namespaces[item['call_id']] = item.get('namespace')
            continue
        if role == 'tool':
            item = {'type': 'function_call_output', 'call_id': message['tool_call_id'],
                    'output': str(message.get('content') or '')}
            if namespaces.get(message['tool_call_id']):
                item['namespace'] = namespaces[message['tool_call_id']]
            items.append(item)
            continue
        content = message.get('content') or ''
        if isinstance(content, list):
            translated = []
            for part in content:
                if part.get('type') == 'text':
                    translated.append({'type': 'input_text' if role == 'user' else 'output_text', 'text': part['text']})
                elif part.get('type') == 'image_url' and role == 'user':
                    translated.append({'type': 'input_image', 'image_url': part['image_url']['url']})
            content = translated
        if content:
            items.append({'type': 'message', 'role': role, 'content': content})
        for call in message.get('tool_calls', []):
            function = call['function']
            items.append({'type': 'function_call', 'call_id': call['id'],
                          'name': function['name'], 'namespace': NAMESPACE,
                          'arguments': function['arguments']})
            namespaces[call['id']] = NAMESPACE
    body = {'model': source['model'].removeprefix('chatgpt/'), 'input': items,
            'instructions': '\n'.join(instructions), 'store': False, 'stream': True,
            'include': ['reasoning.encrypted_content']}
    if source.get('reasoning_effort'):
        body['reasoning'] = {'effort': source['reasoning_effort']}
    if source.get('tools'):
        body['tools'] = [{'type': 'namespace', 'name': NAMESPACE,
                          'description': 'PhoneAgent local tools; host enforces permissions.',
                          'tools': [{'type': 'function', 'name': t['function']['name'],
                                     'description': t['function'].get('description', ''),
                                     'parameters': t['function']['parameters'], 'strict': False}
                                    for t in source['tools']]}]
    # SIWC preview rejects max_output_tokens, temperature, previous_response_id,
    # system items and other ordinary API options; deliberately omit them.
    return body


class ResponsesStream:
    def __init__(self, response):
        self.response = response
        self.fp = getattr(response, 'fp', None)
        self.pending, self.calls = deque(), {}
        self.terminal = False
        self.text_seen = False

    def push(self, value):
        self.pending.append(('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n').encode())

    def call(self, item):
        call_id = item.get('call_id')
        if not call_id or call_id in self.calls:
            return
        name = item.get('name', '')
        if item.get('namespace') not in (None, NAMESPACE):
            name = '__unsupported_namespace__'
        if name.startswith(NAMESPACE + '.'):
            name = name[len(NAMESPACE) + 1:]
        self.calls[call_id] = len(self.calls)
        self.push({'choices': [{'delta': {'tool_calls': [{'index': self.calls[call_id],
                   'id': call_id, 'function': {'name': name, 'arguments': item.get('arguments', '{}')}}]},
                   'finish_reason': None}]})

    def readline(self):
        while not self.pending:
            if self.terminal:
                return b''
            raw = self.response.readline()
            if not raw:
                return b''  # EOF is not successful completion.
            if not raw.startswith(b'data:'):
                continue
            value = raw[5:].strip()
            if value == b'[DONE]':
                continue  # Require the Responses terminal event, not this sentinel.
            event = json.loads(value)
            kind = event.get('type', '')
            if kind == 'response.output_text.delta':
                self.text_seen = True
                self.push({'choices': [{'delta': {'content': event.get('delta', '')}, 'finish_reason': None}]})
            elif kind == 'response.output_item.done':
                item = event.get('item', {})
                if item.get('type') == 'function_call':
                    self.call(item)
            elif kind == 'response.completed':
                response = event['response']
                if response.get('status', 'completed') != 'completed':
                    raise RuntimeError('ChatGPT 套餐响应未完成，请重试或检查使用额度')
                for item in response.get('output', []):
                    self.push({'openai_item': item})
                    if item.get('type') == 'function_call':
                        self.call(item)
                    elif not self.text_seen and item.get('type') == 'message':
                        for part in item.get('content', []):
                            if part.get('type') == 'output_text':
                                self.push({'choices': [{'delta': {'content': part.get('text', '')}, 'finish_reason': None}]})
                usage = response.get('usage') or {}
                self.push({'usage': {'prompt_tokens': usage.get('input_tokens', 0),
                                    'completion_tokens': usage.get('output_tokens', 0),
                                    'total_tokens': usage.get('total_tokens', 0)}})
                self.push({'choices': [{'delta': {}, 'finish_reason': 'tool_calls' if self.calls else 'stop'}]})
                self.pending.append(b'data: [DONE]\n\n')
                self.terminal = True
            elif kind in {'response.failed', 'response.incomplete', 'error'}:
                error = event.get('error') or event.get('response', {}).get('error') or {}
                code = str(error.get('code') or kind)
                code = code if re.fullmatch(r'[\w.-]{1,100}', code) else 'response_error'
                if code in {'subscription_sharing_usage_limit_exceeded', 'subscription_sharing_usage_unavailable'}:
                    raise RuntimeError('ChatGPT 套餐使用受限，请在 ChatGPT 设置→Usage 检查额度或授权')
                raise RuntimeError('ChatGPT 套餐响应失败：' + code)
        return self.pending.popleft()

    def close(self):
        self.response.close()


def stream(source, token, timeout=180):
    request = urllib.request.Request(BASE + '/responses', data=json.dumps(payload(source), ensure_ascii=False).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    return ResponsesStream(urllib.request.urlopen(request, timeout=timeout))
