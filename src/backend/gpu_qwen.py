"""Client for the on-demand Qwen service running in the school GPU Slurm pool.

The AWS API never stores or transmits a human GPU password.  It can send only
a short JSON request through a pinned, purpose-limited SSH key to the remote
gateway.  The gateway starts a Slurm allocation when needed and stops it after
its own idle timeout.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from project_service import fail


class GpuPreparing(RuntimeError):
    """The remote Slurm job has been requested but is not ready yet."""


def _value(name: str) -> str:
    return os.getenv(name, '').strip()


def config() -> dict:
    host = _value('ALO_QWEN_GPU_HOST')
    user = _value('ALO_QWEN_GPU_USER')
    key_path = _value('ALO_QWEN_GPU_KEY_PATH')
    known_hosts = _value('ALO_QWEN_GPU_KNOWN_HOSTS')
    command = _value('ALO_QWEN_GPU_REMOTE_COMMAND') or 'aloha-qwen-gateway'
    port_raw = _value('ALO_QWEN_GPU_PORT') or '10000'
    if not all((host, user, key_path, known_hosts)):
        fail(503, '학교 GPU 도우미 연결 설정이 아직 완료되지 않았습니다.')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', host):
        fail(503, '학교 GPU 서버 주소 설정을 확인하세요.')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}', user):
        fail(503, '학교 GPU 서버 계정 설정을 확인하세요.')
    if command != 'aloha-qwen-gateway':
        fail(503, '학교 GPU 원격 명령 설정을 확인하세요.')
    try:
        port = int(port_raw)
    except ValueError:
        fail(503, '학교 GPU SSH 포트 설정을 확인하세요.')
    if not 1 <= port <= 65535 or not Path(key_path).is_file() or not Path(known_hosts).is_file():
        fail(503, '학교 GPU 인증 파일 설정을 확인하세요.')
    return {
        'provider': 'gpu', 'host': host, 'user': user, 'port': port,
        'key_path': key_path, 'known_hosts': known_hosts, 'command': command,
    }


def _gateway(settings: dict, payload: dict, *, timeout: int = 100) -> dict:
    """Call the forced-command GPU gateway without a shell or user command."""
    data = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(data) > 96_000:
        fail(413, 'GPU 도우미에 전달할 질문 내용이 너무 깁니다.')
    command = [
        'ssh', '-i', settings['key_path'], '-p', str(settings['port']),
        '-o', 'UserKnownHostsFile=' + settings['known_hosts'],
        '-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes',
        '-o', 'IdentitiesOnly=yes', '-o', 'PasswordAuthentication=no',
        '-o', 'KbdInteractiveAuthentication=no', '-o', 'RequestTTY=no',
        '-o', 'ConnectTimeout=12', '-o', 'ServerAliveInterval=15',
        '-o', 'ServerAliveCountMax=2', '-o', 'LogLevel=ERROR',
        settings['user'] + '@' + settings['host'], settings['command'],
    ]
    try:
        result = subprocess.run(command, input=data, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        fail(502, '학교 GPU 도우미 연결을 확인하지 못했습니다. 잠시 후 다시 시도하세요.')
    if result.returncode:
        fail(502, '학교 GPU 도우미 연결을 확인하지 못했습니다. 잠시 후 다시 시도하세요.')
    try:
        reply = json.loads(result.stdout.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        fail(502, '학교 GPU 도우미 응답 형식을 확인하지 못했습니다.')
    if not isinstance(reply, dict):
        fail(502, '학교 GPU 도우미 응답 형식을 확인하지 못했습니다.')
    return reply


def status(settings: dict) -> dict:
    reply = _gateway(settings, {'action': 'status'}, timeout=20)
    return {
        'provider': 'gpu',
        'state': str(reply.get('state') or 'unknown'),
        'idle_seconds': reply.get('idle_seconds'),
        'message': str(reply.get('message') or ''),
    }


def ask(settings: dict, messages: list[dict], tools: list[dict]) -> dict:
    reply = _gateway(settings, {'action': 'ask', 'messages': messages, 'tools': tools}, timeout=105)
    state = str(reply.get('state') or '')
    if state in {'queued', 'starting'}:
        raise GpuPreparing('GPU 모델을 준비 중입니다. GPU 할당과 모델 로딩이 끝나면 자동으로 다시 시도합니다.')
    if state != 'ready':
        fail(503, str(reply.get('message') or '학교 GPU 도우미를 사용할 수 없습니다.'))
    message = reply.get('message')
    if not isinstance(message, dict):
        fail(502, '학교 GPU 모델의 답변 형식을 확인하지 못했습니다.')
    return message
