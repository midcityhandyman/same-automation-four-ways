// Normalize Lead (S2) and plan the schedule (S7).
// Mirrors src/lead_followup/normalize.py. tests/test_four_ways.py runs both
// on the same payloads when Node.js is available.
const item = $input.first().json;
const raw = item.body ?? item;
const OFFSET = Number($env.LOCAL_UTC_OFFSET_HOURS ?? -5);
const START = Number($env.SEND_WINDOW_START ?? 8);
const END = Number($env.SEND_WINDOW_END ?? 20);
const HOUR = 3600 * 1000;
const FOLLOW_UP_DAYS = [1, 3, 7];
const CLOSE_AFTER_DAYS = 10;

function normPhone(value) {
  const text = String(value ?? '').trim();
  const digits = text.replace(/[^0-9]/g, '');
  if (digits.length === 10) return '+1' + digits;
  if (digits.length === 11 && digits.startsWith('1')) return '+' + digits;
  if (text.startsWith('+') && digits.length >= 8 && digits.length <= 15) return '+' + digits;
  return null;
}

function normEmail(value) {
  const text = String(value ?? '').trim().toLowerCase();
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(text) ? text : null;
}

// Move a send time into the local send window. Inside the window: unchanged.
function quietAdjust(moment) {
  const local = new Date(moment.getTime() + OFFSET * HOUR);
  const hour = local.getUTCHours();
  if (hour >= START && hour < END) return moment;
  if (hour >= END) local.setUTCDate(local.getUTCDate() + 1);
  local.setUTCHours(START, 0, 0, 0);
  return new Date(local.getTime() - OFFSET * HOUR);
}

const name = String(raw.name ?? '').trim();
const phone = normPhone(raw.phone);
const email = normEmail(raw.email);
const received = raw.received_at ? new Date(raw.received_at) : new Date();

let reason = null;
if (!name) reason = 'missing name';
else if (!phone && !email) reason = 'no usable phone or email';
else if (Number.isNaN(received.getTime())) reason = 'received_at is not an ISO timestamp';

if (reason) {
  return [{ json: { valid: false, reason, raw } }];
}

const due = (days) => quietAdjust(new Date(received.getTime() + days * 24 * HOUR)).toISOString();

return [{
  json: {
    valid: true,
    key: phone ? 'phone:' + phone : 'email:' + email,
    name,
    first_name: name.split(/\s+/)[0],
    email,
    phone,
    service: String(raw.service ?? '').trim() || 'general',
    message: String(raw.message ?? '').trim(),
    source: String(raw.source ?? '').trim() || 'unknown',
    received_at: received.toISOString(),
    primary_channel: phone ? 'sms' : 'email',
    primary_to: phone ?? email,
    follow_up_1_at: due(FOLLOW_UP_DAYS[0]),
    follow_up_2_at: due(FOLLOW_UP_DAYS[1]),
    follow_up_3_at: due(FOLLOW_UP_DAYS[2]),
    close_at: due(CLOSE_AFTER_DAYS),
  },
}];
