#!/usr/bin/env bash
#SBATCH --gres=gpu:1
#SBATCH --time=02:00:00
#SBATCH --job-name=aloha-qwen-chat

set -euo pipefail
ROOT="${HOME}/.aloha-qwen-copilot"
CONFIG="${ROOT}/config.json"
STATE="${ROOT}/state.json"
ACTIVITY="${ROOT}/last_activity"

readarray -t SETTINGS < <(python3 - "$CONFIG" <<'PY'
import json,sys
c=json.load(open(sys.argv[1]))
for key in ('model_path','llama_server','api_key_file','idle_seconds','activity_file'):
 print(c[key])
PY
)
MODEL="${SETTINGS[0]}"
LLAMA_SERVER="${SETTINGS[1]}"
API_KEY_FILE="${SETTINGS[2]}"
IDLE_SECONDS="${SETTINGS[3]}"
ACTIVITY_FILE="${SETTINGS[4]}"

# Load the cluster CUDA module here if required.
PORT="$(python3 - <<'PY'
import socket
s=socket.socket();s.bind(('0.0.0.0',0));print(s.getsockname()[1]);s.close()
PY
)"
touch "$ACTIVITY_FILE"
python3 - "$STATE" "$SLURM_JOB_ID" "$HOSTNAME" "$PORT" <<'PY'
import json,os,sys,time
p,j,n,port=sys.argv[1:]
t=p+'.new';open(t,'w').write(json.dumps({'state':'starting','job_id':j,'node':n,'port':int(port),'started_at':int(time.time())}))
os.chmod(t,0o600);os.replace(t,p)
PY

"$LLAMA_SERVER" -m "$MODEL" --device CUDA0 --split-mode none -ngl all --fit off -c 16384 -np 2 --host 0.0.0.0 --port "$PORT" --jinja --reasoning off --no-warmup --api-key-file "$API_KEY_FILE" >"${ROOT}/server-${SLURM_JOB_ID}.log" 2>&1 &
SERVER_PID=$!
cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
  python3 - "$STATE" "$SLURM_JOB_ID" <<'PY'
import json,os,sys,time
p,j=sys.argv[1:]
try:v=json.load(open(p))
except Exception:v={}
if str(v.get('job_id'))==j:
 t=p+'.new';open(t,'w').write(json.dumps({'state':'stopped','job_id':j,'stopped_at':int(time.time())}));os.chmod(t,0o600);os.replace(t,p)
PY
}
trap cleanup EXIT TERM INT

READY=0
for _ in $(seq 1 90); do
  if python3 - "$PORT" <<'PY'
import sys,urllib.request
try:
 urllib.request.urlopen('http://127.0.0.1:'+sys.argv[1]+'/health',timeout=1);raise SystemExit(0)
except Exception:raise SystemExit(1)
PY
  then READY=1; break; fi
  kill -0 "$SERVER_PID" 2>/dev/null || exit 1
  sleep 2
done
if [ "$READY" != "1" ]; then exit 1; fi
python3 - "$STATE" "$SLURM_JOB_ID" "$HOSTNAME" "$PORT" <<'PY'
import json,os,sys,time
p,j,n,port=sys.argv[1:]
t=p+'.new';open(t,'w').write(json.dumps({'state':'ready','job_id':j,'node':n,'port':int(port),'ready_at':int(time.time())}));os.chmod(t,0o600);os.replace(t,p)
PY

while kill -0 "$SERVER_PID" 2>/dev/null; do
  last="$(stat -c %Y "$ACTIVITY_FILE" 2>/dev/null || date +%s)"
  now="$(date +%s)"
  if [ $((now-last)) -ge "$IDLE_SECONDS" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    break
  fi
  sleep 10
done
wait "$SERVER_PID" || true
