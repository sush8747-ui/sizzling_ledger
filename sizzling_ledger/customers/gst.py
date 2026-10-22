"""GSTIN helpers: validation, state / PAN details, and an optional lookup of the
registered name and address from a GST data provider.

Two different things happen here and it is worth keeping them apart:

* VALIDATING a GSTIN (format, state code, check character) needs no outside
  service. It always works, offline, and is what stops typos.
* FETCHING the registered name and address needs the GST portal's data. The
  portal itself is behind a captcha, so this is done through a paid/credentialed
  GST API provider that YOU configure (see settings.GST_LOOKUP_URL). Nothing is
  ever invented: if no provider is configured, or it is down, the caller is told
  so and no name/address is made up.
"""
import json
import re

import requests
from django.conf import settings
from django.core.cache import cache

# GST state / UT codes (the first two digits of a GSTIN).
STATE_CODES = {
    '01': 'Jammu and Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab',
    '04': 'Chandigarh', '05': 'Uttarakhand', '06': 'Haryana', '07': 'Delhi',
    '08': 'Rajasthan', '09': 'Uttar Pradesh', '10': 'Bihar', '11': 'Sikkim',
    '12': 'Arunachal Pradesh', '13': 'Nagaland', '14': 'Manipur',
    '15': 'Mizoram', '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam',
    '19': 'West Bengal', '20': 'Jharkhand', '21': 'Odisha', '22': 'Chhattisgarh',
    '23': 'Madhya Pradesh', '24': 'Gujarat',
    '25': 'Daman and Diu',  # merged into 26 in 2020; old registrations remain
    '26': 'Dadra and Nagar Haveli and Daman and Diu', '27': 'Maharashtra',
    '28': 'Andhra Pradesh (old)',  # before the 2014 split; old registrations remain
    '29': 'Karnataka', '30': 'Goa', '31': 'Lakshadweep', '32': 'Kerala',
    '33': 'Tamil Nadu', '34': 'Puducherry', '35': 'Andaman and Nicobar Islands',
    '36': 'Telangana', '37': 'Andhra Pradesh', '38': 'Ladakh',
    '97': 'Other Territory', '99': 'Centre Jurisdiction',
}

# 2 digits state, 10 char PAN, 1 entity number, 'Z', 1 check character.
_GSTIN_RE = re.compile(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$')
_CHARS = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'


def normalize_gstin(value):
    """Upper-case and strip spaces/dashes; None becomes ''."""
    return re.sub(r'[\s-]+', '', str(value or '')).upper()


def checksum_char(first14):
    """The 15th character of a GSTIN, worked out from the first 14."""
    total = 0
    for i, ch in enumerate(first14):
        val = _CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += val // 36 + val % 36
    return _CHARS[(36 - total % 36) % 36]


def gstin_problem(gstin):
    """None when `gstin` (already normalized) is a valid GSTIN, otherwise a
    short message saying what is wrong with it."""
    if not gstin:
        return 'Enter a GST number.'
    if len(gstin) != 15:
        return f'A GST number has 15 characters ({len(gstin)} entered).'
    if not _GSTIN_RE.match(gstin):
        return ('This is not in GST number format: 2 digits, 5 letters, 4 digits, '
                '1 letter, 1 letter or digit, the letter Z, then 1 check character.')
    if gstin[:2] not in STATE_CODES:
        return f'Unknown GST state code {gstin[:2]}.'
    if checksum_char(gstin[:14]) != gstin[14]:
        return ('This GST number is not valid: its check character does not match. '
                'Please re-check it.')
    return None


def state_name(gstin):
    return STATE_CODES.get(gstin[:2], '')


def pan_from_gstin(gstin):
    return gstin[2:12]


# ---------------------------------------------------------------------------
# Reading a provider's answer. Providers differ in wrapping and key names but
# nearly all follow the GSTN "taxpayer" shape: lgnm (legal name), tradeNam
# (trade name), sts (status) and pradr (principal address).
# ---------------------------------------------------------------------------
_WRAPPER_KEYS = ('data', 'taxpayerInfo', 'taxpayer_info', 'taxpayer', 'result')
_ADDRESS_PARTS = ('bno', 'flno', 'bnm', 'st', 'loc', 'locality', 'dst', 'city', 'stcd', 'pncd')


def _unwrap(payload):
    """Dig the taxpayer object out of wrappers such as {"data": ...} or a JSON
    string inside a field."""
    for _ in range(5):
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return {}
            continue
        if not isinstance(payload, dict):
            return {}
        for key in _WRAPPER_KEYS:
            if isinstance(payload.get(key), (dict, str)):
                payload = payload[key]
                break
        else:
            return payload
    return payload if isinstance(payload, dict) else {}


def _first(source, *keys):
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def _compose_address(parts):
    seen, out = set(), []
    for part in parts:
        text = str(part or '').strip().strip(',')
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return ', '.join(out)


def parse_taxpayer(payload):
    """Turn a provider's response into
    {legal_name, trade_name, status, address, location, pincode, state}.
    Any value the provider did not give is ''."""
    info = _unwrap(payload)
    if not isinstance(info, dict):
        info = {}

    pradr = info.get('pradr')
    addr = {}
    address = ''
    if isinstance(pradr, dict):
        addr = pradr.get('addr') if isinstance(pradr.get('addr'), dict) else pradr
        address = _first(pradr, 'adr')
    elif isinstance(pradr, str):
        address = pradr.strip()
    if not address:
        address = _first(info, 'address', 'principal_address', 'principalAddress')
    if not address and addr:
        address = _compose_address(addr.get(k) for k in _ADDRESS_PARTS)

    state = _first(addr, 'stcd', 'state') if addr else ''
    if state.isdigit():  # a numeric code rather than a name
        state = STATE_CODES.get(state.zfill(2), '')

    # Official GST data has no contact details, but some providers add them.
    # Read them only if present; nothing is ever invented.
    contact = info.get('contact') if isinstance(info.get('contact'), dict) else {}
    email = _first(info, 'email', 'emailId', 'email_id', 'em', 'contactEmail') or \
        _first(contact, 'email', 'emailId', 'em')
    phone = _first(info, 'mobile', 'phone', 'mobileNo', 'mobile_no', 'mb', 'contactNumber', 'contact_no') or \
        _first(contact, 'mobile', 'phone', 'mb', 'mobNum')
    phone = re.sub(r'[^0-9+]', '', phone)
    if phone.startswith('+91'):
        phone = phone[3:]
    elif len(phone) == 12 and phone.startswith('91'):
        phone = phone[2:]

    return {
        'email': email,
        'phone': phone,
        'legal_name': _first(info, 'lgnm', 'legalName', 'legal_name', 'name'),
        'trade_name': _first(info, 'tradeNam', 'tradeName', 'tradename', 'trade_name'),
        'status': _first(info, 'sts', 'status', 'gstinStatus'),
        'address': address,
        # City-level value for the "Location" field: district first, then city/locality.
        'location': _first(addr, 'dst', 'city', 'loc', 'locality') if addr else '',
        'pincode': _first(addr, 'pncd', 'pincode') if addr else '',
        'state': state,
    }


# ---------------------------------------------------------------------------
# The optional remote lookup
# ---------------------------------------------------------------------------
class GstLookupError(Exception):
    pass


class GstNotFound(GstLookupError):
    """The provider has no taxpayer for this GSTIN."""


class GstUnavailable(GstLookupError):
    """The provider could not be reached, refused us, or is not configured."""


def lookup_configured():
    return bool(getattr(settings, 'GST_LOOKUP_URL', ''))


def _fill(text, gstin, api_key):
    return str(text).replace('{gstin}', gstin).replace('{api_key}', api_key)


def fetch_taxpayer(gstin):
    """Ask the configured provider about `gstin` and return parse_taxpayer()'s
    result. Raises GstNotFound / GstUnavailable."""
    template = getattr(settings, 'GST_LOOKUP_URL', '')
    if not template:
        raise GstUnavailable('no GST data provider is configured')
    api_key = getattr(settings, 'GST_LOOKUP_API_KEY', '') or ''
    headers = {
        str(k): _fill(v, gstin, api_key)
        for k, v in (getattr(settings, 'GST_LOOKUP_HEADERS', None) or {}).items()
    }
    try:
        response = requests.get(
            _fill(template, gstin, api_key),
            headers=headers,
            timeout=getattr(settings, 'GST_LOOKUP_TIMEOUT', 10),
        )
    except requests.RequestException as exc:
        raise GstUnavailable('the GST data service could not be reached') from exc

    code = response.status_code
    if code in (400, 404, 422):
        raise GstNotFound()
    if code in (401, 403):
        raise GstUnavailable('the GST data service rejected the server credentials')
    if code == 429:
        raise GstUnavailable('the GST data service limit was reached')
    if code >= 400:
        raise GstUnavailable(f'the GST data service returned an error ({code})')
    try:
        payload = response.json()
    except ValueError as exc:
        raise GstUnavailable('the GST data service sent an unreadable answer') from exc

    info = parse_taxpayer(payload)
    if not (info['legal_name'] or info['trade_name']):
        # e.g. 200 with {"error": "Invalid GSTIN"} — no taxpayer behind it.
        raise GstNotFound()
    return info


def lookup_taxpayer(gstin):
    """fetch_taxpayer() with a 24-hour cache, so re-opening a form (or two people
    looking up the same supplier) does not use up the provider's paid quota."""
    key = f'gst-taxpayer:{gstin}'
    cached = cache.get(key)
    if cached:
        return cached
    info = fetch_taxpayer(gstin)
    cache.set(key, info, 24 * 60 * 60)
    return info
