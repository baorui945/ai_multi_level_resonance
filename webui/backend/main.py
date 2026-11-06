# -*- coding: utf-8 -*-
"""MLR Web UI 后端（FastAPI）。

启动：
    python -m uvicorn webui.backend.main:app --host 127.0.0.1 --port 8200
开发模式前端：cd webui/frontend && npm run dev（:3200，/api 代理到 :8200）
生产模式：npm run build 后，本服务直接托管 webui/frontend/dist。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from webui.backend import data_store as DS          # noqa: E402

app = FastAPI(title='MLR Web UI', version='1.0.0')
app.add_middleware(
    CORSMiddleware, allow_origins=['*'], allow_credentials=True,
    allow_methods=['*'], allow_headers=['*'],
)

OUTPUT = DS.OUTPUT
DIST = _ROOT / 'webui' / 'frontend' / 'dist'


@app.get('/api/health')
def health():
    return {'ok': True, 'service': 'mlr-webui', 'root': str(_ROOT)}


@app.get('/api/meta')
def meta():
    return DS.meta()


@app.get('/api/scan')
def scan(universe: str = 'hs300'):
    return DS.load_scan(universe)


# ── 手动执行扫描（后台线程 + 轮询状态）────────────────────────────────
_JOBS: dict[str, dict] = {}
_JOB_LOCK = threading.Lock()


class ScanRequest(BaseModel):
    universe: str = 'hs300'
    date: Optional[str] = None


def _now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _scan_worker(job_id: str, universe: str, date: Optional[str]) -> None:
    cmd = [sys.executable, str(_ROOT / 'webui' / 'export_scan.py'),
           '--universe', universe]
    if date:
        cmd += ['--date', date]
    t0 = time.time()
    status, message = 'completed', ''
    try:
        proc = subprocess.run(cmd, cwd=str(_ROOT), capture_output=True,
                              text=True, encoding='utf-8', errors='replace',
                              timeout=1800)
        out = ((proc.stdout or '') + '\n' + (proc.stderr or '')).strip()
        message = out[-1500:]
        if proc.returncode != 0:
            status = 'failed'
    except Exception as exc:                                 # noqa: BLE001
        status, message = 'failed', str(exc)
    with _JOB_LOCK:
        _JOBS[job_id].update(status=status, message=message,
                             finished=_now(),
                             duration=round(time.time() - t0, 1))


@app.post('/api/scan/run')
def scan_run(req: ScanRequest):
    with _JOB_LOCK:
        running = [j for j in _JOBS.values() if j['status'] == 'running']
        if running:
            raise HTTPException(
                status_code=409,
                detail=f'已有扫描在运行（{running[0]["universe"]}），请等待完成')
        job_id = uuid.uuid4().hex[:8]
        _JOBS[job_id] = {
            'id': job_id, 'status': 'running', 'universe': req.universe,
            'date': req.date, 'started': _now(), 'finished': None,
            'duration': None, 'message': '扫描已启动',
        }
        job = dict(_JOBS[job_id])
    threading.Thread(target=_scan_worker,
                     args=(job_id, req.universe, req.date),
                     daemon=True).start()
    return job


@app.get('/api/scan/job/{job_id}')
def scan_job(job_id: str):
    with _JOB_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail='job not found')
        return dict(job)


@app.get('/api/backtest')
def backtest():
    return DS.load_backtest()


@app.get('/api/stock/{code}')
def stock(code: str):
    code = ''.join(ch for ch in code if ch.isdigit()).zfill(6)
    data = DS.stock_detail(code)
    if not data.get('available'):
        raise HTTPException(status_code=404, detail=data.get('error', 'not found'))
    return data


@app.get('/api/runs')
def runs():
    return {'rows': DS.list_runs()}


@app.get('/api/download/{rel:path}')
def download(rel: str):
    """只允许下载 output/ 目录内的文件。"""
    target = (OUTPUT / rel).resolve()
    try:
        target.relative_to(OUTPUT.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail='forbidden')
    if not target.is_file():
        raise HTTPException(status_code=404, detail='file not found')
    return FileResponse(target, filename=target.name)


@app.post('/api/refresh')
def refresh(rebuild: bool = False):
    """清空进程内缓存并重新读取产物（默认不重跑扫描）。

    扫描请用 `POST /api/scan/run`（后台任务）；这里保留 rebuild=True 的
    兼容入口，仅重建 hs300 快照。
    """
    message = ''
    if rebuild:
        script = _ROOT / 'webui' / 'export_scan.py'
        try:
            proc = subprocess.run(
                [sys.executable, str(script)], cwd=str(_ROOT),
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=600)
            message = (proc.stdout or proc.stderr or '').strip()[-400:]
        except Exception as exc:                             # noqa: BLE001
            message = f'扫描快照重建失败：{exc}'
    DS._STOCK_CACHE.clear()
    return {'ok': True, 'message': message, **DS.meta()}


# ── 生产模式：托管前端构建产物 ────────────────────────────────────────
if DIST.is_dir():
    app.mount('/assets', StaticFiles(directory=DIST / 'assets'), name='assets')

    @app.get('/{full_path:path}')
    def spa(full_path: str):
        candidate = (DIST / full_path).resolve()
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / 'index.html')
