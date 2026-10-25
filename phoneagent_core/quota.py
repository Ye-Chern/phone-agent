import math
import time
from datetime import datetime, timedelta, timezone


class QuotaExceeded(RuntimeError):
    pass


def pacific_day(ts):
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(ts, ZoneInfo('America/Los_Angeles')).date().isoformat()
    except (ImportError, KeyError):
        # Modern US DST rules, for Termux/Windows without IANA tzdata.
        utc = datetime.fromtimestamp(ts, timezone.utc)
        y = utc.year
        march = datetime(y, 3, 8, 10, tzinfo=timezone.utc)
        march += timedelta(days=(6 - march.weekday()) % 7)
        nov = datetime(y, 11, 1, 9, tzinfo=timezone.utc)
        nov += timedelta(days=(6 - nov.weekday()) % 7)
        offset = -7 if march <= utc < nov else -8
        return (utc + timedelta(hours=offset)).date().isoformat()


def estimate_tokens(value):
    # Conservative planning heuristic from UTF-8 bytes, not a tokenizer bound.
    # Work history can contain serialized multimodal content and provider-private
    # replay fields. Those are protocol payloads, not prose: counting their
    # base64/opaque bytes as text makes one JPEG look like tens of thousands of
    # tokens. Keep visible text and reserve bounded space for each real image.
    import json
    import re

    visible_images = 0
    native_images = 0
    private_fields = {'reasoning_content', 'google_parts', 'openai_items'}
    image_data_url = re.compile(r'data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+')

    def image_count(obj):
        if isinstance(obj, list):
            return sum(image_count(item) for item in obj)
        if isinstance(obj, str):
            return len(image_data_url.findall(obj))
        if not isinstance(obj, dict):
            return 0
        kind = str(obj.get('type', '')).lower()
        if kind in {'image_url', 'input_image'}:
            return 1
        # Native provider image parts often use inline_data / image / image_url
        # without an OpenAI-style type discriminator.
        if kind in {'image', 'inline_data', 'inline-data'}:
            return 1
        return sum(image_count(item) for key, item in obj.items()
                   if key not in {'data', 'b64_json', 'base64'})

    def strip_images(obj, *, native=False):
        nonlocal visible_images, native_images
        if isinstance(obj, list):
            return [strip_images(x, native=native) for x in obj]
        if isinstance(obj, dict):
            kind = str(obj.get('type', '')).lower()
            if kind in {'image_url', 'input_image', 'image', 'inline_data', 'inline-data'}:
                if native:
                    native_images += 1
                else:
                    visible_images += 1
                return {'type': 'image', 'content': '[image]'}
            clean = {}
            for key, item in obj.items():
                if key in private_fields:
                    native_images += image_count(item)
                    continue
                clean[key] = strip_images(item, native=native)
            return clean
        if isinstance(obj, str):
            # Some persisted user content is JSON text containing image_url
            # objects. Parse only JSON containers, leaving ordinary text intact.
            candidate = obj.strip()
            if candidate.startswith(('[', '{')):
                try:
                    parsed = json.loads(candidate)
                except (TypeError, ValueError):
                    pass
                else:
                    return strip_images(parsed, native=native)
            if image_data_url.search(obj):
                image_matches = len(image_data_url.findall(obj))
                if native:
                    native_images += image_matches
                else:
                    visible_images += image_matches
                return image_data_url.sub('[image]', obj)
        return obj

    clean = strip_images(value)
    text = clean if isinstance(clean, str) else json.dumps(clean, ensure_ascii=False)
    # A native replay may mirror an image already present in visible content.
    # Count the larger representation rather than charging for both copies.
    images = max(visible_images, native_images)
    # 2048 per image is intentionally bounded planning headroom; upstream usage
    # replaces it after a completed request.
    return max(1, math.ceil(len(text.encode('utf-8')) / 2)) + images * 2048


def limits_for(model, registry, overrides):
    limits = dict(registry[model].get('limits', {}))
    limits.update(overrides.get(model, {}))
    return limits


def reserve(conn, model, estimate, limits, now=None):
    now = time.time() if now is None else now
    day = pacific_day(now)
    conn.execute('BEGIN IMMEDIATE')
    try:
        daily = conn.execute('SELECT COUNT(*) FROM requests WHERE model=? AND day=?', (model, day)).fetchone()[0]
        minute = conn.execute('SELECT COUNT(*),COALESCE(SUM(COALESCE(input_tokens,estimated)),0),COALESCE(MAX(cooldown),0) FROM requests WHERE model=? AND ts>?', (model, now - 60)).fetchone()
        cooldown = conn.execute('SELECT COALESCE(MAX(cooldown),0) FROM requests WHERE model=?', (model,)).fetchone()[0]
        if cooldown > now:
            raise QuotaExceeded('上游限流冷却中，请稍后重试')
        caps = [int(limits[k]) for k in ('rpd', 'local_rpd') if limits.get(k)]
        if caps and daily >= max(0, min(caps) - int(limits.get('reserve', 0))):
            raise QuotaExceeded('达到本地每日请求上限或保留额度')
        if limits.get('rpm') and minute[0] >= int(limits['rpm']):
            raise QuotaExceeded('达到本地每分钟请求上限')
        if limits.get('tpm') and minute[1] + estimate > int(limits['tpm']):
            raise QuotaExceeded('预计输入超过本地每分钟 token 上限')
        cur = conn.execute('INSERT INTO requests(model,ts,day,estimated,status) VALUES(?,?,?,?,?)',
                           (model, now, day, estimate, 'reserved'))
        conn.commit()
        return cur.lastrowid
    except BaseException:
        conn.rollback()
        raise


def complete(conn, rid, usage=None, status='complete', cooldown=0):
    usage = usage or {}
    conn.execute('UPDATE requests SET input_tokens=?,output_tokens=?,status=?,cooldown=? WHERE id=?',
                 (usage.get('prompt_tokens'), usage.get('completion_tokens'), status, cooldown, rid))
    conn.commit()


def report(conn, registry, overrides):
    now = time.time()
    result = []
    for model in registry:
        limits = limits_for(model, registry, overrides)
        row = conn.execute('SELECT COUNT(*),COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),SUM(input_tokens IS NULL) FROM requests WHERE model=? AND day=?', (model, pacific_day(now))).fetchone()
        cap = min([int(limits[k]) for k in ('rpd', 'local_rpd') if limits.get(k)] or [0])
        result.append({'model': model, 'requests_today': row[0], 'input_tokens': row[1], 'output_tokens': row[2],
                       'unknown_usage_requests': row[3] or 0, 'limits': limits,
                       'estimated_remaining': max(0, cap - int(limits.get('reserve', 0)) - row[0]) if cap else None,
                       'source': 'local-estimate', 'reset_timezone': 'America/Los_Angeles'})
    return result
