"""Project Aegis: bounded, single-process, local Ollama orchestration.

Run: python -m uvicorn gateway:app --host 127.0.0.1 --port 8000 --no-proxy-headers
Dependencies: FastAPI, HTTPX, psutil, uvicorn (Python 3.11+).
Core works as one file; existing diagnostic modules are mounted when present.
OS samples are observations, NOT kernel registers, GPU metrics or psychological
diagnoses. Recovery touches only this gateway, never adapters or shared Ollama.
"""
import asyncio
import hashlib
import importlib
import importlib.util
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import platform
import secrets
import sys
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parent
LAPTOP_MODEL = 'smollm2:135m-instruct-q4_K_M'


@dataclass(frozen=True)
class Settings:
    target: str = field(default_factory=lambda: os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434'))
    max_pending: int = 4
    cache_size: int = 64
    warm_size: int = 128
    cache_bytes: int = 4 * 1024 * 1024
    cache_ttl: float = 600
    timeout: float = 180
    min_available_mb: int = 512
    monitor_interval: float = 5
    latency_threshold_ms: float = 3500
    ram_threshold: float = 90
    recovery_cooldown: float = 30
    rate_window: float = 5
    rate_limit: int = 14
    duplicate_limit: int = 6
    context_window: int = 2048
    min_context: int = 512
    profile: str = 'standard'
    allowed_model: str | None = None
    keep_alive_seconds: int | None = None
    cpu_threads: int | None = None
    batch_size: int | None = None
    memory_wait_seconds: float = 0
    admission_ram_margin_pct: float = 0

    def __post_init__(self):
        p = urlsplit(self.target)
        if p.scheme != 'http' or p.hostname not in ('localhost', '127.0.0.1') or p.username or p.password or p.path not in ('', '/') or p.query or p.fragment:
            raise ValueError('OLLAMA_URL must be a local HTTP origin without credentials.')
        _ = p.port  # Reject malformed ports at startup.
        for name in ('max_pending', 'cache_size', 'warm_size', 'cache_bytes', 'timeout', 'monitor_interval', 'rate_window', 'latency_threshold_ms'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be positive and finite.')
        if not 0 <= self.ram_threshold <= 100 or self.rate_limit < 2 or self.duplicate_limit < 2 or not 512 <= self.min_context <= self.context_window <= 8192:
            raise ValueError('Invalid thresholds or context limits.')
        if any(not math.isfinite(getattr(self, name)) or getattr(self, name) < 0 for name in ('cache_ttl', 'recovery_cooldown', 'min_available_mb')):
            raise ValueError('Invalid negative setting.')
        if self.rate_limit > 64 or self.duplicate_limit > 64:
            raise ValueError('Traffic limits must fit the bounded 64-request history.')
        if self.profile not in ('standard', 'laptop'):
            raise ValueError('Unknown runtime profile.')
        if self.keep_alive_seconds is not None and not 0 <= self.keep_alive_seconds <= 300:
            raise ValueError('Invalid model residency duration.')
        if self.cpu_threads is not None and not 1 <= self.cpu_threads <= 8:
            raise ValueError('Invalid CPU thread limit.')
        if self.batch_size is not None and not 1 <= self.batch_size <= 512:
            raise ValueError('Invalid inference batch size.')
        if not math.isfinite(self.memory_wait_seconds) or not 0 <= self.memory_wait_seconds <= 15:
            raise ValueError('Invalid bounded memory wait.')
        if not 0 <= self.admission_ram_margin_pct <= 5:
            raise ValueError('Invalid admission margin.')


class InferenceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prompt: str = Field(min_length=1, max_length=4000)
    model: str = Field(default_factory=lambda: os.getenv('AEGIS_MODEL', 'qwen2.5:0.5b'), max_length=100, min_length=1, pattern=r'^[a-zA-Z0-9_./:-]+$')
    max_tokens: int = Field(default=80, ge=1, le=256)
    context_window: int = Field(default=2048, ge=512, le=8192)


def sample_system():
    """Portable best-effort counters. Unsupported fields stay null, not zero."""
    started = time.perf_counter_ns()
    ram = psutil.virtual_memory()
    process = psutil.Process()
    memory = process.memory_info()
    cpu = psutil.cpu_stats()
    try:
        disk = psutil.disk_io_counters()
    except (psutil.Error, OSError):
        disk = None
    result = {
        'sample_monotonic_ns': started, 'platform': platform.system(),
        'cpu_utilization_pct': psutil.cpu_percent(interval=None),
        'ram_utilization_pct': ram.percent, 'available_ram_bytes': ram.available,
        'total_ram_bytes': ram.total, 'gateway_rss_bytes': memory.rss,
        'system_context_switches': cpu.ctx_switches,
        # On Windows this is this gateway process's total faults, not system
        # hard faults and not the separate Ollama process's page faults.
        'gateway_page_faults': getattr(memory, 'num_page_faults', None),
    }
    for name in ('read_count', 'write_count', 'read_bytes', 'write_bytes', 'read_time', 'write_time'):
        result['disk_' + name] = getattr(disk, name, None)
    result['sample_duration_us'] = round((time.perf_counter_ns() - started) / 1000, 3)
    return result


def resource_delta(before, after):
    """Microsecond CLOCK interval; underlying OS counters have coarser resolution.
    System deltas include unrelated programs. They do not establish causality.
    """
    result = {'measurement_interval_us': round((after['sample_monotonic_ns'] - before['sample_monotonic_ns']) / 1000, 3)}
    for key in ('system_context_switches', 'gateway_page_faults', 'gateway_rss_bytes', 'available_ram_bytes',
                'disk_read_count', 'disk_write_count', 'disk_read_bytes', 'disk_write_bytes', 'disk_read_time', 'disk_write_time'):
        a, b = before.get(key), after.get(key)
        result[key + '_delta'] = None if a is None or b is None else b - a
    # A measured average is a bottleneck clue, NOT a disk queue-length claim.
    for action in ('read', 'write'):
        count = result.get(f'disk_{action}_count_delta')
        ms = result.get(f'disk_{action}_time_delta')
        result[f'disk_{action}_average_ms'] = round(ms / count, 3) if count and count > 0 and ms is not None and ms >= 0 else None
    return result


@dataclass
class Pool:
    client: httpx.AsyncClient
    leases: int = 0
    retired: bool = False


@dataclass(slots=True)
class Entry:
    answer: str
    expires: float
    size: int


class Engine:
    """All mutable state belongs to ONE asyncio loop/worker; no database needed."""
    def __init__(self, settings, transport=None, telemetry_fn=None, log_path=None):
        self.settings = settings
        self.transport = transport
        self.telemetry_fn = telemetry_fn or sample_system
        self.gate = asyncio.Semaphore(1)  # Bound heavy inference on a small host.
        self.hot, self.warm = OrderedDict(), OrderedDict()
        self.clients = OrderedDict()
        self.salt = secrets.token_bytes(16)
        self.pending = self.cache_bytes = self.epoch = 0
        self.context_window = settings.context_window
        self.snapshot = {}
        self.loop_lag_ms = 0
        self.queue_wait_ms = 0
        self.memory_wait_ms = 0
        self.waiting_for_memory = False
        self.last_latency_ms = None
        self.inflight_started_ns = None
        self.monitor_delta = None
        self.latency_sequence = self.handled_latency_sequence = 0
        self.last_latency_at = 0
        self.last_recovery_at = float('-inf')
        self.ram_latched = False
        self.events = deque(maxlen=64)
        self.cycles = deque(maxlen=16)
        self.metrics = dict(requests=0, cache_hits=0, upstream_calls=0, failures=0,
                            rejected=0, active=0, recoveries=0, behavioral_throttles=0)
        self.pools = []
        self.pool = self.new_pool()
        self.monitor_task = None
        self.logger = logging.getLogger('aegis.' + secrets.token_hex(6))
        self.logger.propagate = False
        self.logger.setLevel(logging.INFO)
        self.handler = None
        if log_path:
            path = Path(log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding='utf-8')
            self.handler.setFormatter(logging.Formatter('%(message)s'))
            self.logger.addHandler(self.handler)

    def event(self, kind, **fields):
        record = dict(timestamp_utc=datetime.now(timezone.utc).isoformat(), event=kind, **fields)
        self.events.append(record)
        # No prompts, answers, API keys or raw client addresses enter these logs.
        # Rate events are coalesced below to keep disk logging bounded in a flood.
        if self.handler:
            self.logger.info(json.dumps(record, allow_nan=False))

    def new_pool(self):
        client = httpx.AsyncClient(base_url=self.settings.target.rstrip('/'), transport=self.transport,
            trust_env=False, timeout=httpx.Timeout(self.settings.timeout, connect=5),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4), follow_redirects=False)
        pool = Pool(client)
        self.pools.append(pool)
        return pool

    @asynccontextmanager
    async def lease(self):
        pool = self.pool
        pool.leases += 1
        try:
            yield pool.client
        finally:
            pool.leases -= 1
            if pool.retired and pool.leases == 0:
                await pool.client.aclose()
                if pool in self.pools:
                    self.pools.remove(pool)

    async def start(self):
        self.snapshot = await asyncio.to_thread(self.telemetry_fn)
        self.monitor_task = asyncio.create_task(self.monitor(), name='aegis-health-monitor')

    async def close(self):
        if self.monitor_task:
            self.monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.monitor_task
        for pool in list(self.pools):
            await pool.client.aclose()
        if self.handler:
            self.handler.close()
            self.logger.removeHandler(self.handler)

    async def monitor(self):
        while True:
            expected = asyncio.get_running_loop().time() + self.settings.monitor_interval
            await asyncio.sleep(self.settings.monitor_interval)
            self.loop_lag_ms = round(max(0, asyncio.get_running_loop().time() - expected) * 1000, 3)
            try:
                sample = await asyncio.to_thread(self.telemetry_fn)
                self.monitor_delta = resource_delta(self.snapshot, sample)
                self.snapshot = sample
                await self.evaluate_health(sample)
            except (psutil.Error, OSError, ValueError) as exc:
                self.event('monitor_error', error_type=type(exc).__name__)

    async def evaluate_health(self, sample):
        """Cooldown + RAM hysteresis prevent periodic recovery thrashing."""
        now = time.monotonic()
        ram = sample.get('ram_utilization_pct')
        if ram is not None and ram < self.settings.ram_threshold - 5:
            self.ram_latched = False
        ram_trigger = ram is not None and ram > self.settings.ram_threshold and not self.ram_latched
        slow = (self.last_latency_ms is not None and self.last_latency_ms > self.settings.latency_threshold_ms
                and self.latency_sequence > self.handled_latency_sequence and now - self.last_latency_at < 30)
        active_ms = (time.perf_counter_ns() - self.inflight_started_ns) / 1e6 if self.inflight_started_ns else None
        active_slow = active_ms is not None and active_ms > self.settings.latency_threshold_ms and self.latency_sequence + 1 > self.handled_latency_sequence
        slow = slow or active_slow
        if not (ram_trigger or slow) or now - self.last_recovery_at < self.settings.recovery_cooldown:
            return False
        self.last_recovery_at = now
        self.handled_latency_sequence = self.latency_sequence + (1 if active_slow else 0)
        if ram_trigger:
            self.ram_latched = True
        self.epoch += 1  # In-flight old generations cannot refill a cleared cache.
        removed = len(self.hot) + len(self.warm)
        self.hot.clear(); self.warm.clear(); self.cache_bytes = 0
        self.context_window = max(self.settings.min_context, self.context_window // 2)
        old = self.pool
        self.pool = self.new_pool()
        old.retired = True
        self.metrics['recoveries'] += 1
        self.event('recovery', reason='ram_pressure' if ram_trigger else 'slow_upstream',
                   ram_utilization_pct=ram, upstream_latency_ms=active_ms if active_slow else self.last_latency_ms,
                   cleared_entries=removed, context_window=self.context_window, epoch=self.epoch,
                   action='clear_owned_cache; retire_owned_HTTP_pool; reduce_future_context')
        # Never close sockets under an active request. Its lease closes the old
        # pool after it drains. No OS sockets/adapters or Ollama process reset.
        if old.leases == 0:
            await old.client.aclose()
            self.pools.remove(old)
        return True

    def behavior(self, peer, fingerprint):
        now = time.monotonic()
        identity = hashlib.sha256(self.salt + peer.encode()).hexdigest()[:16]
        state = self.clients.pop(identity, None) or {'events': deque(maxlen=64), 'last_log': float('-inf'), 'penalty_until': 0}
        events = state['events']
        while events and events[0][0] <= now - self.settings.rate_window:
            events.popleft()
        events.append((now, fingerprint))
        duplicates = sum(key == fingerprint for _, key in events)
        rapid = len(events)
        score = round(100 * min(1, max((rapid - 1) / (self.settings.rate_limit - 1),
                                      (duplicates - 1) / (self.settings.duplicate_limit - 1))), 1)
        if rapid > self.settings.rate_limit or duplicates > self.settings.duplicate_limit:
            state['penalty_until'] = now + self.settings.rate_window
        lane = 'CACHE_ONLY' if now < state['penalty_until'] else 'NORMAL'
        if lane == 'CACHE_ONLY':
            score = 100
        self.clients[identity] = state
        while len(self.clients) > 128:
            self.clients.popitem(last=False)
        if lane == 'CACHE_ONLY':
            self.metrics['behavioral_throttles'] += 1
            if now - state['last_log'] >= 1:
                self.event('behavioral_throttle', client=identity, developer_stress_index=score, lane=lane)
                state['last_log'] = now
        return dict(routing_lane=lane, developer_stress_index=score,
                    index_meaning='traffic-pattern proxy, not a human psychological assessment')

    def cache_key(self, body, context):
        return hashlib.sha256(json.dumps([body.model, body.prompt, body.max_tokens, context]).encode()).hexdigest()

    def cached(self, key):
        now = time.monotonic()
        for name, tier in (('hot', self.hot), ('warm', self.warm)):
            entry = tier.get(key)
            if entry:
                if entry.expires <= now:
                    self.cache_bytes -= tier.pop(key).size
                    continue
                tier.move_to_end(key)
                return entry.answer, name
        return None

    def remember(self, key, answer):
        # Python Unicode storage can exceed UTF-8 size; charge the actual str
        # payload plus key and a metadata allowance. This is not an RSS cap.
        size = sys.getsizeof(answer) + sys.getsizeof(key) + 256
        if size > self.settings.cache_bytes:
            return
        for tier in (self.hot, self.warm):
            if key in tier:
                self.cache_bytes -= tier.pop(key).size
        self.hot[key] = Entry(answer, time.monotonic() + self.settings.cache_ttl, size)
        self.cache_bytes += size
        while len(self.hot) > self.settings.cache_size:
            old_key, entry = self.hot.popitem(last=False)
            self.warm[old_key] = entry
        while len(self.warm) > self.settings.warm_size or self.cache_bytes > self.settings.cache_bytes:
            tier = self.warm if self.warm else self.hot
            self.cache_bytes -= tier.popitem(last=False)[1].size

    async def memory_slot(self):
        """Wait briefly for real headroom instead of racing a noisy threshold.
        Cached work never waits here. The critical 90% monitor stays unchanged.
        """
        started = time.monotonic()
        deadline = started + self.settings.memory_wait_seconds
        limit = self.settings.ram_threshold - self.settings.admission_ram_margin_pct
        try:
            while True:
                sample = await asyncio.to_thread(self.telemetry_fn)
                self.snapshot = sample
                enough = (sample['available_ram_bytes'] >= self.settings.min_available_mb * 1024**2
                          and sample.get('ram_utilization_pct', 0) <= limit)
                if enough:
                    return sample
                if time.monotonic() >= deadline:
                    self.metrics['rejected'] += 1
                    raise HTTPException(503, 'RAM pressure: no safe memory slot before the bounded wait expired. Cached answers remain available.')
                self.waiting_for_memory = True
                await asyncio.sleep(min(.25, max(0, deadline - time.monotonic())))
        finally:
            self.memory_wait_ms = round((time.monotonic() - started)*1000, 3)
            self.waiting_for_memory = False

    async def generate(self, body, behavior):
        started = time.perf_counter_ns()
        self.metrics['requests'] += 1
        context = min(body.context_window, self.context_window)
        key = self.cache_key(body, context)
        def result(answer, strategy, tier=None, delta=None):
            return dict(response=answer, model=body.model, execution_strategy=strategy,
                        latency_ms=round((time.perf_counter_ns() - started) / 1e6, 3),
                        context_window=context, cache_tier=tier, resource_delta=delta, **behavior)
        def from_cache():
            hit = self.cached(key)
            if hit:
                self.metrics['cache_hits'] += 1
                return result(hit[0], 'CACHE_HIT', hit[1])
        hit = from_cache()
        if hit:
            return hit
        if behavior['routing_lane'] == 'CACHE_ONLY':
            self.metrics['rejected'] += 1
            raise HTTPException(429, 'Traffic limit: exact answer is not cached. Retry after the rate window.', headers={'Retry-After': str(math.ceil(self.settings.rate_window))})
        if self.pending >= self.settings.max_pending:
            self.metrics['rejected'] += 1
            raise HTTPException(429, 'Inference queue full.', headers={'Retry-After': '5'})
        self.pending += 1
        queued = time.perf_counter_ns()
        try:
            async with asyncio.timeout(self.settings.timeout):
                async with self.gate:
                    self.queue_wait_ms = round((time.perf_counter_ns() - queued) / 1e6, 3)
                    # A queued request must honor any recovery that happened
                    # while it waited; context is part of the exact cache key.
                    context = min(body.context_window, self.context_window)
                    key = self.cache_key(body, context)
                    hit = from_cache()
                    if hit:
                        return hit
                    before = await self.memory_slot()
                    context = min(body.context_window, self.context_window)
                    key = self.cache_key(body, context)
                    epoch = self.epoch
                    self.metrics['active'] += 1
                    self.metrics['upstream_calls'] += 1
                    upstream_started = time.perf_counter_ns()
                    self.inflight_started_ns = upstream_started
                    try:
                        async with self.lease() as client:
                            options = {'num_predict': body.max_tokens, 'temperature': 0, 'num_ctx': context}
                            if self.settings.cpu_threads is not None:
                                options.update(num_thread=self.settings.cpu_threads, num_gpu=0)
                            if self.settings.batch_size is not None:
                                options['num_batch'] = self.settings.batch_size
                            payload = {
                                'model': body.model, 'prompt': body.prompt, 'stream': False,
                                'options': options}
                            if self.settings.keep_alive_seconds is not None:
                                # The laptop launcher uses an isolated model server;
                                # no shared Ollama model residency is changed.
                                payload['keep_alive'] = self.settings.keep_alive_seconds
                            async with client.stream('POST', '/api/generate', json=payload) as response:
                                if response.status_code == 404:
                                    raise HTTPException(404, 'Model not installed in Ollama.')
                                response.raise_for_status()
                                raw = bytearray()
                                async for chunk in response.aiter_bytes():
                                    if len(raw) + len(chunk) > 256 * 1024:
                                        raise ValueError('Upstream response exceeds the bound')
                                    raw.extend(chunk)
                                data = json.loads(raw)
                                answer = data.get('response')
                                if not isinstance(answer, str) or not answer.strip() or data.get('done') is not True:
                                    raise ValueError('Incomplete generation')
                    finally:
                        self.metrics['active'] -= 1
                        self.last_latency_ms = round((time.perf_counter_ns() - upstream_started) / 1e6, 3)
                        self.last_latency_at = time.monotonic()
                        self.latency_sequence += 1
                        self.inflight_started_ns = None
                        after = await asyncio.to_thread(self.telemetry_fn)
                        delta = resource_delta(before, after)
                        cycle = dict(upstream_latency_ms=self.last_latency_ms, context_window=context, **delta)
                        self.cycles.append(cycle)
                        self.event('inference_resource_delta', **cycle)
                    if epoch == self.epoch:
                        self.remember(key, answer)
                    response = result(answer, 'LIVE_INFERENCE_ROUTING', delta=delta)
                    response['generation'] = dict(output_tokens=data.get('eval_count'),
                        input_tokens=data.get('prompt_eval_count'), done_reason=data.get('done_reason'),
                        model_load_ms=round(data['load_duration']/1e6,3) if isinstance(data.get('load_duration'), (int,float)) else None)
                    return response
        except (TimeoutError, httpx.TimeoutException):
            self.metrics['failures'] += 1
            raise HTTPException(504, 'Inference/queue deadline exceeded.')
        except httpx.RequestError:
            self.metrics['failures'] += 1
            raise HTTPException(503, 'Ollama connection unavailable.')
        except (httpx.HTTPStatusError, ValueError, AttributeError):
            self.metrics['failures'] += 1
            raise HTTPException(502, 'Ollama did not return a bounded, complete response.')
        except HTTPException:
            self.metrics['failures'] += 1
            raise
        finally:
            self.pending -= 1


class Guard:
    """Bounded ASGI body reader + adaptive inference admission middleware."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode('latin1') for k, v in scope['headers']}
        host = headers.get('host', '')
        origin = headers.get('origin')
        if host.split(':')[0] not in ('127.0.0.1', 'localhost', 'testserver') or (origin and origin != scope.get('scheme', 'http') + '://' + host):
            return await JSONResponse({'detail': 'Local, same-origin access only.'}, status_code=403)(scope, receive, send)
        if scope['path'] == '/v1/orchestrate' and scope['method'] == 'POST':
            if 'x-forwarded-for' in headers or 'forwarded' in headers:
                return await JSONResponse({'detail': 'Forwarded identities are not supported.'}, status_code=400)(scope, receive, send)
            raw = bytearray()
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                if len(raw) + len(message.get('body', b'')) > 65536:
                    return await JSONResponse({'detail': 'Inference body limit is 64 KiB.'}, status_code=413)(scope, receive, send)
                raw.extend(message.get('body', b''))
                if not message.get('more_body', False):
                    break
            try:
                parsed = json.loads(raw)
                fingerprint = hashlib.sha256(json.dumps(parsed, sort_keys=True).encode()).hexdigest()
            except (ValueError, RecursionError):
                fingerprint = 'invalid-json'
            engine = scope['app'].state.engine
            scope['aegis_behavior'] = engine.behavior((scope.get('client') or ('unknown', 0))[0], fingerprint)
            behavior = scope['aegis_behavior']
            async def annotated_send(message):
                if message['type'] == 'http.response.start':
                    message = {**message, 'headers': list(message.get('headers', [])) + [
                        (b'x-aegis-routing-lane', behavior['routing_lane'].encode()),
                        (b'x-developer-stress-index', str(behavior['developer_stress_index']).encode())]}
                await send(message)
            delivered = False
            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {'type': 'http.request', 'body': bytes(raw), 'more_body': False}
                return await receive()
            return await self.app(scope, replay, annotated_send)
        return await self.app(scope, receive, send)


def create_app(*, transport=None, telemetry_fn=None, log_path=None, **options):
    """Dependency injection is Python-only; no public fault/recovery controls."""
    profile = options.pop('profile', os.getenv('AEGIS_PROFILE', 'standard'))
    defaults = {}
    if profile == 'laptop':
        defaults = dict(target=os.getenv('OLLAMA_URL', 'http://127.0.0.1:11435'),
                        max_pending=2, context_window=512, cache_bytes=1024*1024,
                        allowed_model=os.getenv('AEGIS_MODEL', LAPTOP_MODEL),
                        keep_alive_seconds=0, cpu_threads=2, batch_size=32,
                        memory_wait_seconds=8, admission_ram_margin_pct=1)
    settings = Settings(**{**defaults, **options, 'profile': profile})
    @asynccontextmanager
    async def lifespan(app):
        engine = Engine(settings, transport, telemetry_fn, log_path)
        app.state.engine = engine
        try:
            await engine.start()
            yield
        finally:
            await engine.close()
    app = FastAPI(title='Project Aegis — local orchestration', lifespan=lifespan)
    app.add_middleware(Guard)

    @app.post('/v1/orchestrate')
    async def infer(body: InferenceRequest, request: Request):
        if not body.prompt.strip():
            raise HTTPException(422, 'Prompt cannot be blank.')
        if settings.allowed_model:
            if 'model' not in body.model_fields_set:
                body = body.model_copy(update={'model': settings.allowed_model})
            elif body.model != settings.allowed_model:
                raise HTTPException(422, 'This laptop profile only permits its configured small model.')
        return await app.state.engine.generate(body, request.scope['aegis_behavior'])

    @app.get('/health')
    async def health():
        try:
            async with app.state.engine.lease() as client:
                response = await client.get('/api/tags', timeout=5)
                response.raise_for_status()
                installed = sorted(response.json()['models'], key=lambda m: m.get('size', 0))
                if settings.allowed_model:
                    installed = [m for m in installed if m['name'] == settings.allowed_model]
                models = [m['name'] for m in installed]
                return {'status': 'ready' if models else 'no_models', 'models': models,
                        'profile': settings.profile, 'configured_model': settings.allowed_model}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(503, 'Ollama is unavailable.')

    @app.get('/cluster/telemetry')
    async def telemetry():
        e = app.state.engine
        sample = e.snapshot
        return {**sample, **e.metrics, 'available_ram_gb': round(sample.get('available_ram_bytes', 0) / 1024**3, 2),
                'total_ram_gb': round(sample.get('total_ram_bytes', 0) / 1024**3, 2),
                'pending_requests': e.pending, 'max_pending': settings.max_pending,
                'cache_entries': len(e.hot) + len(e.warm), 'hot_entries': len(e.hot), 'warm_entries': len(e.warm),
                'cache_limit': settings.cache_size + settings.warm_size, 'cache_bytes_charged': e.cache_bytes,
                'cache_byte_limit': settings.cache_bytes, 'context_window': e.context_window,
                'event_loop_lag_ms': e.loop_lag_ms, 'last_inference_queue_wait_ms': e.queue_wait_ms,
                'last_memory_wait_ms': e.memory_wait_ms, 'waiting_for_memory': e.waiting_for_memory,
                'last_upstream_latency_ms': e.last_latency_ms, 'recent_inference_deltas': list(e.cycles),
                'runtime_profile': dict(name=settings.profile, model=settings.allowed_model,
                    configured_context=settings.context_window, keep_alive_seconds=settings.keep_alive_seconds,
                    cpu_threads=settings.cpu_threads, batch_size=settings.batch_size,
                    memory_wait_seconds=settings.memory_wait_seconds,
                    admission_ram_limit_pct=settings.ram_threshold-settings.admission_ram_margin_pct,
                    upstream=settings.target),
                'monitor_resource_delta': e.monitor_delta,
                'sample_age_ms': round(max(0, time.perf_counter_ns() - sample.get('sample_monotonic_ns', time.perf_counter_ns())) / 1e6, 3),
                'unsupported_metrics': ['Windows OS run-queue length', 'system hard page faults', 'GPU/VRAM'],
                'scope': 'OS-wide disk/context-switch counters; gateway-only RSS/page faults. Microsecond clock timestamps do not imply microsecond counter resolution or inference causality.'}

    @app.get('/cluster/recovery')
    async def recovery():
        e = app.state.engine
        return dict(recoveries=e.metrics['recoveries'], epoch=e.epoch, context_window=e.context_window,
                    ram_latched=e.ram_latched, events=list(e.events), monitor_interval_seconds=settings.monitor_interval,
                    latency_threshold_ms=settings.latency_threshold_ms, ram_threshold_pct=settings.ram_threshold,
                    recovery_cooldown_seconds=settings.recovery_cooldown)

    # Optional adapters preserve this workspace's previous applications. The
    # Aegis core above needs none of these modules or static files to start.
    for module, factory in (('network_clinic', 'create_router'), ('service_checks', 'create_service_router'),
                            ('developer_routes', 'create_developer_router'), ('dev_lab', 'create_fixture_router')):
        if importlib.util.find_spec(module):
            app.include_router(getattr(importlib.import_module(module), factory)())

    pages = {'/': 'clinic.html', '/ai-lab': 'index.html', '/developer': 'developer.html',
             '/service-ui.js': 'service-ui.js', '/developer-ui.js': 'developer-ui.js',
             '/aegis': 'aegis.html', '/aegis-ui.js': 'aegis-ui.js'}
    def page_handler(filename):
        async def serve():
            path = ROOT / 'static' / filename
            if path.exists():
                return FileResponse(path)
            return JSONResponse({'service': 'Project Aegis', 'telemetry': '/cluster/telemetry', 'docs': '/docs'})
        return serve
    for route, filename in pages.items():
        app.add_api_route(route, page_handler(filename), methods=['GET'], include_in_schema=False)

    @app.get('/experiment')
    async def experiment():
        path = ROOT / 'results' / 'comparison.json'
        if not path.exists():
            raise HTTPException(404, 'Run compare.py to generate a comparison first.')
        return FileResponse(path, media_type='application/json', headers={'Cache-Control': 'no-store'})

    @app.get('/aegis/evidence')
    async def aegis_evidence():
        # Read only fixed, locally generated report paths. Never label a
        # simulation as a live inference result or silently hide failed runs.
        result = {}
        for label, filename in (('live', 'aegis-live-laptop.json'), ('simulation', 'aegis-simulated.json')):
            path = ROOT / 'results' / filename
            if not path.exists():
                result[label] = None
                continue
            try:
                result[label] = json.loads(path.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                result[label] = None
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    @app.get('/aegis/residency')
    async def aegis_residency():
        try:
            async with app.state.engine.lease() as client:
                response = await client.get('/api/ps', timeout=5)
                response.raise_for_status()
                models = response.json()['models']
                return dict(source='Actual configured Ollama /api/ps',
                    models=[dict(name=m['name'],size_bytes=m.get('size'),vram_bytes=m.get('size_vram'),
                                 context_window=m.get('context_length')) for m in models],
                    keep_alive_seconds=settings.keep_alive_seconds)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(503, 'Cannot verify configured model residency.')

    @app.get('/aegis/review', include_in_schema=False)
    async def recorded_review():
        path = ROOT / 'results' / 'aegis-review.html'
        if not path.exists():
            raise HTTPException(404, 'Run export_aegis_review.py to prepare a recorded review.')
        return FileResponse(path, media_type='text/html', headers={
            'Cache-Control': 'no-store',
            'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
        })
    return app


app = create_app(log_path=os.getenv('AEGIS_LOG_PATH', str(ROOT / 'results' / 'aegis.jsonl')))
