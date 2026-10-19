import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';

export const options = {
  scenarios: {
    guest_viewers: {
      executor: 'constant-vus', vus: 500, duration: '15s', exec: 'viewStream',
    },
    chat_posters: {
      executor: 'constant-vus', vus: 50, duration: '15s', exec: 'postChat',
    },
  },
  thresholds: {
    http_req_failed: ['rate==0.00'],
    http_req_duration: ['p(95)<800'],
  },
};

export function viewStream() {
  const response = http.get(`${BASE_URL}/audit/v1/qr/match-m1?inspect=true`);
  check(response, {
    'guest QR status is 200': (r) => r.status === 200,
    'QR has target_route': (r) => r.json('target_route') !== undefined,
  });
  // Keep the full viewer population active without tight-polling each session.
  sleep(0.5);
}

export function postChat() {
  const payload = JSON.stringify({
    chat_room_id: 'chat-cs-session-101',
    incoming_messages: [{
      message_id: `k6-${__VU}-${__ITER}`,
      sender: `user-${__VU}`,
      text: 'Live match reaction',
    }],
    check_dlq: false,
  });
  const response = http.post(`${BASE_URL}/audit/v1/moderation/test`, payload, {
    headers: { 'Content-Type': 'application/json' },
  });
  check(response, { 'moderation probe status is 200': (r) => r.status === 200 });
  // Model a short human posting interval while all 50 poster VUs remain active.
  sleep(0.5);
}
