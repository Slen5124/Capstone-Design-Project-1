#!/usr/bin/env python3
"""Restricted SSH gateway for an on-demand Qwen Slurm chat server.

This program is installed in the GPU account and invoked only through a forced
SSH command.  It accepts a small JSON protocol; it never evaluates commands
from AWS or exposes a shell.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path.home() / '.aloha-qwen-copilot'
CONFIG = ROOT / 'config.json'
STATE = ROOT / 'state.json'
LOCK = ROOT / 'gateway.lock'
ALLOWED_ROLES = {'system', 'user', 'assistant', 'tool'}


def send(value: dict, code: int = 0) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(',', ':')))
    raise SystemExit(code)


def load_config() -> dict:
    try:
        config = json.loads(CONFIG.read_text(encoding='utf-8'))
        required = {'model_path', 'llama_server', 'api_key_file', 'activity_file', 'idle_seconds'}
        if not required <= set(config):
            raise ValueError()
        if not all(isinstance(config[key], str) and config[key].startswith('/') for key in ('model_path', 'llama_server', 'api_key_file', 'activity_file')):
            raise ValueError()
        if not isinstance(config['idle_seconds'], int) or not 300 <= config['idle_seconds'] <= 7200:
            raise ValueError()
        return config
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        send({'state': 'error', 'message': 'GPU 도우미 설정을 확인하지 못했습니다.'}, 1)


def read_state() -> dict:
    try:
        value = json.loads(STATE.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def write_state(value: dict) -> None:
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = STATE.with_name(STATE.name + '.new')
    temp.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    os.chmod(temp, 0o600)
    os.replace(temp, STATE)


def running(job_id: str) -> bool:
    if not re.fullmatch(r'\d{1,16}', str(job_id)):
        return False
    result = subprocess.run(['squeue', '-h', '-j', str(job_id), '-o', '%T'], capture_output=True, text=True, timeout=10)
    return result.returncode == 0 and bool(result.stdout.strip())


def current(config: dict) -> dict:
    state = read_state()
    job_id = str(state.get('job_id') or '')
    if not job_id or not running(job_id):
        return {'state': 'stopped', 'message': 'GPU 모델이 대기 중입니다.', 'idle_seconds': config['idle_seconds']}
    phase = state.get('state') if state.get('state') in {'queued', 'starting', 'ready'} else 'starting'
    return {**state, 'state': phase, 'idle_seconds': config['idle_seconds']}


def start(config: dict) -> dict:
    state = current(config)
    if state['state'] in {'queued', 'starting', 'ready'}:
        return state
    result = subprocess.run(['sbatch', '--parsable', str(ROOT / 'aloha_qwen_server.sbatch')], capture_output=True, text=True, timeout=20)
    job_id = result.stdout.strip().split(';', 1)[0]
    if result.returncode or not re.fullmatch(r'\d{1,16}', job_id):
        return {'state': 'error', 'message': 'GPU 작업을 요청하지 못했습니다. 현재 GPU 대기열을 확인하세요.'}
    state = {'state': 'queued', 'job_id': job_id, 'submitted_at': int(time.time())}
    write_state(state)
    return {**state, 'idle_seconds': config['idle_seconds'], 'message': 'GPU 작업을 요청했습니다. 모델을 준비하고 있습니다.'}


def valid_messages(value: object) -> list[dict] | None:
    if not isinstance(value, list) or not 1 <= len(value) <= 24:
        return None
    checked, total = [], 0
    for item in value:
        if not isinstance(item, dict) or item.get('role') not in ALLOWED_ROLES or not isinstance(item.get('content'), str):
            return None
        total += len(item['content'])
        if len(item['content']) > 12_000 or total > 50_000:
            return None
        clean = {'role': item['role'], 'content': item['content']}
        if item.get('tool_calls') is not None and item['role'] == 'assistant':
            clean['tool_calls'] = item['tool_calls']
        if item.get('tool_call_id') is not None and item['role'] == 'tool':
            clean['tool_call_id'] = item['tool_call_id']
        checked.append(clean)
    return checked


def valid_tools(value: object) -> list[dict] | None:
    if not isinstance(value, list) or len(value) > 5:
        return None
    return value if all(isinstance(item, dict) and item.get('type') == 'function' and isinstance(item.get('function'), dict) for item in value) else None


def ask(config: dict, request: dict) -> dict:
    state = start(config)
    if state['state'] != 'ready':
        return state
    messages, tools = valid_messages(request.get('messages')), valid_tools(request.get('tools'))
    if messages is None or tools is None:
        return {'state': 'error', 'message': 'GPU 도우미 질문 형식을 확인하지 못했습니다.'}
    node, port = str(state.get('node') or ''), state.get('port')
    if not re.fullmatch(r'[A-Za-z0-9.-]{1,253}', node) or not isinstance(port, int) or not 1 <= port <= 65535:
        return {'state': 'starting', 'message': 'GPU 모델 연결을 준비하고 있습니다.'}
    try:
        api_key = Path(config['api_key_file']).read_text(encoding='utf-8').strip()
        if not api_key:
            raise ValueError()
        body = json.dumps({'model': 'qwen-local', 'messages': messages, 'tools': tools, 'tool_choice': 'auto', 'temperature': 0.2, 'max_tokens': 900}, ensure_ascii=False).encode('utf-8')
        call = urllib.request.Request('http://' + node + ':' + str(port) + '/v1/chat/completions', data=body, method='POST', headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + api_key})
        Path(config['activity_file']).touch()
        with urllib.request.urlopen(call, timeout=85) as response:
            result = json.loads(response.read().decode('utf-8'))
        message = result['choices'][0]['message']
        if not isinstance(message, dict):
            raise ValueError()
        return {'state': 'ready', 'message': message}
    except (OSError, ValueError, KeyError, IndexError, TypeError, urllib.error.URLError, urllib.error.HTTPError):
        return {'state': 'starting', 'message': 'GPU 모델 응답을 준비하고 있습니다.'}


def stop(config: dict) -> dict:
    state = current(config)
    job_id = str(state.get('job_id') or '')
    if re.fullmatch(r'\d{1,16}', job_id):
        subprocess.run(['scancel', job_id], capture_output=True, timeout=15)
    write_state({'state': 'stopped', 'stopped_at': int(time.time())})
    return {'state': 'stopped', 'message': 'GPU 모델을 종료했습니다.', 'idle_seconds': config['idle_seconds']}


def main() -> None:
    if os.environ.get('SSH_ORIGINAL_COMMAND', '').strip() != 'aloha-qwen-gateway':
        send({'state': 'error', 'message': '허용되지 않은 GPU 도우미 요청입니다.'}, 1)
    try:
        raw = sys.stdin.buffer.read(96_000)
        request = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        send({'state': 'error', 'message': 'GPU 도우미 요청 형식을 확인하지 못했습니다.'}, 1)
    if not isinstance(request, dict) or request.get('action') not in {'status', 'ask', 'stop'}:
        send({'state': 'error', 'message': '허용되지 않은 GPU 도우미 요청입니다.'}, 1)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with LOCK.open('a+') as lock:
        os.chmod(LOCK, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        config = load_config()
        if request['action'] == 'status':
            send(current(config))
        if request['action'] == 'stop':
            send(stop(config))
        send(ask(config, request))


if __name__ == '__main__':
    main()
