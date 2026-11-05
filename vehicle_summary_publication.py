"""Publish vehicle lookup maps without illegal RTDB keys or partial replacement."""
import copy
import logging
import re
import time

from firebase_admin.exceptions import InvalidArgumentError

logger = logging.getLogger(__name__)
LOOKUP_MAPS = (
    'pgiByChassis', 'goodReceiveByChassis', 'seriesByChassis',
    'pgiBySalesOrder', 'goodReceiveBySalesOrder', 'seriesBySalesOrder',
)


def valid_key(key):
    return (isinstance(key, str) and bool(key) and len(key.encode('utf-8')) <= 768
            and not re.search(r'[.#$\[\]/\x00-\x1f\x7f]', key))


def prepare_vehicle_summary(payload):
    result = copy.deepcopy(payload)
    removed = 0
    for name in LOOKUP_MAPS:
        mapping = result.get(name, {})
        for key, value in list(mapping.items()):
            if valid_key(key):
                continue
            alias = re.sub(r'[^A-Z0-9]', '', key.upper())
            if not valid_key(alias):
                raise ValueError(f'{name}: cannot form a safe vehicle lookup alias')
            if alias in mapping and mapping[alias] != value:
                raise ValueError(f'{name}: conflicting normalized vehicle lookup alias {alias}')
            mapping[alias] = value
            del mapping[key]
            removed += 1

    def validate(value, path='analysisVehicleBaseSummary'):
        if isinstance(value, dict):
            for key, item in value.items():
                if not valid_key(key):
                    raise ValueError(f'Invalid Firebase key at {path}/{key!r}')
                validate(item, f'{path}/{key}')
        elif isinstance(value, list):
            for item in value:
                validate(item, path)
    validate(result)
    if removed:
        logger.info('Replaced %s illegal vehicle aliases with safe lookup aliases; vehicle totals unchanged', removed)
    return result


def firebase_shape(value):
    """RTDB does not store nulls/empty containers; normalize for read-back checks."""
    if isinstance(value, dict):
        return {k: v for k, item in value.items() if (v := firebase_shape(item)) is not None} or None
    if isinstance(value, list):
        # RTDB may return dense integer-keyed objects as lists.
        return {str(i): v for i, item in enumerate(value) if (v := firebase_shape(item)) is not None} or None
    return value


def is_size_error(exc):
    message = str(exc).lower()
    return any(text in message for text in (
        'exceeds the maximum size', 'payload too large', 'request entity too large',
        'request payload size exceeds',
    ))


def summary_changes(previous, current):
    """An atomic multi-path patch avoids rewriting unchanged large lookup maps."""
    patch = {}
    def visit(path, before, after):
        if before == after:
            return
        if isinstance(before, dict) and isinstance(after, dict):
            for key in before.keys() | after.keys():
                visit(f'{path}/{key}' if path else key, before.get(key), after.get(key))
        else:
            if not path:
                raise ValueError('Vehicle summary root must be an object')
            patch[path] = after
    visit('', firebase_shape(previous) or {}, firebase_shape(current) or {})
    return patch


def publish_vehicle_summary(ref, payload, tries=3):
    safe = prepare_vehicle_summary(payload)
    for attempt in range(tries):
        try:
            previous = ref.get()
            if previous is None:
                ref.set(safe)
            else:
                changes = summary_changes(previous, safe)
                if changes:
                    # Single atomic PATCH, including deletions. Do not split the
                    # live summary into partially visible generations.
                    ref.update(changes)
            if firebase_shape(ref.get()) != firebase_shape(safe):
                raise RuntimeError('Vehicle base summary read-back does not match uploaded data')
            logger.info('PASS vehicle base summary uploaded and read-back verified')
            return
        except InvalidArgumentError:
            raise  # Retrying cannot fix an invalid key or an oversized payload.
        except Exception:
            if attempt + 1 >= tries:
                raise
            logger.warning('Vehicle base summary upload/verification retry %s/%s', attempt + 1, tries)
            time.sleep(2 ** attempt)
