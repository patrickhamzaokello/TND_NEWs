# Real-Time Article Streaming — Client Integration Guide

The backend pushes every newly scraped article to connected clients over a
WebSocket the moment it lands — no polling required. Built with Django
Channels, backed by the same Redis instance used for Celery/cache.

## Endpoint

```
wss://newsapi.mwonya.com/ws/articles/stream/?token=<JWT_ACCESS_TOKEN>
```

Local dev:

```
ws://localhost:6200/ws/articles/stream/?token=<JWT_ACCESS_TOKEN>
```

**Auth:** WebSocket handshakes can't carry custom headers on most mobile
platforms, so the same JWT access token you already use for REST calls
(`Authorization: Bearer <token>`) is passed as a query parameter instead.
Use the standard access token from `/auth/login/` or `/auth/refresh/`.

- Missing/invalid/expired token → server closes the connection with code
  `4401`. Refresh your access token via the existing refresh-token flow and
  reconnect.
- Access tokens are short-lived (10 minutes, see `SIMPLE_JWT` settings), so
  **your reconnect logic must refresh the token before reconnecting**, not
  just retry the same URL.

## Connection lifecycle

1. Client opens the WebSocket with `?token=...`.
2. Server validates the token, auto-subscribes the client to the firehose
   (all articles), and sends:
   ```json
   { "type": "connection_established", "message": "...", "groups": ["articles.stream"] }
   ```
3. Server pushes events as they happen (see Message Types below).
4. Client should send `{"action": "ping"}` periodically (e.g. every 30s) to
   keep the connection alive through load balancers/proxies; server replies
   `{"type": "pong"}`.

## Message types (server → client)

### `article.new`
Sent the instant a newly scraped article finishes processing and has full
content.

```json
{
  "type": "article.new",
  "article": {
    "id": 4821,
    "title": "Bank of Uganda holds key lending rate at 9.5%",
    "slug": "bank-of-uganda-holds-key-lending-rate",
    "excerpt": "...",
    "featured_image_url": "https://...",
    "url": "https://dailymonitor.co.ug/...",
    "source": { "id": 3, "name": "Daily Monitor", "favicon_url": "https://..." },
    "category": { "id": 5, "name": "Business", "slug": "business" },
    "published_at": "2026-06-30T09:12:00Z",
    "scraped_at": "2026-06-30T09:14:31Z",
    "read_time_minutes": 3,
    "has_full_content": true
  }
}
```

### `breaking_news`
Sent when the backend's breaking-news detector flags an article (title
prefix like "BREAKING:", breaking category, etc). Same `article` shape as
above, plus a `priority`.

```json
{
  "type": "breaking_news",
  "priority": "high",
  "article": { "...": "same shape as article.new" }
}
```

### `connection_established` / `subscribed` / `unsubscribed`
Acknowledgements for connection and subscription changes, includes the
current list of joined groups.

### `pong`
Reply to a client `ping`.

### `error`
```json
{ "type": "error", "message": "Unknown action: 'foo'" }
```

## Subscribing to specific categories or sources (optional)

By default every client receives **all** articles (the firehose). To scope
the stream:

**At connect time**, via query params:
```
wss://.../ws/articles/stream/?token=...&categories=politics,sports&sources=3,7
```

**After connecting**, via messages (client → server):
```json
{ "action": "subscribe", "categories": ["politics"], "sources": [3] }
```
```json
{ "action": "unsubscribe", "categories": ["politics"] }
```

Category values are `Category.slug` (see `GET /news/categories/`). Source
values are `NewsSource.id` (see `GET /news/sources/`). Subscribing to a
category/source does **not** remove you from the firehose — you'll still get
everything, plus acknowledgements scoped to the new groups. If you only want
the scoped stream, do not rely on the firehose join (it currently always
happens on connect; the consumer can be adjusted to make the firehose
opt-in if a "filtered only" mode is needed later).

## Reconnection strategy (required)

WebSockets drop — cellular networks, backgrounding, server restarts. Clients
**must** implement reconnect-with-backoff:

1. On `onclose`/`onerror`, wait `min(2^attempt, 30)` seconds before retrying.
2. Before reconnecting, check if the access token has expired; if so, call
   your existing token-refresh endpoint first.
3. Reset the backoff counter on a successful `connection_established`.
4. On reconnect, re-send any `subscribe` messages for non-default
   categories/sources — subscriptions are per-connection and do not persist
   server-side.
5. Consider fetching `GET /news/articles/?ordering=-scraped_at` once after
   reconnecting to backfill anything missed while disconnected (the stream
   has no replay/offset mechanism — it is fire-and-forget, at-most-once
   delivery).

## Example clients

### JavaScript / React Native

```javascript
class ArticleStream {
  constructor({ getAccessToken, baseWsUrl = 'wss://newsapi.mwonya.com' }) {
    this.getAccessToken = getAccessToken; // async () => string
    this.baseWsUrl = baseWsUrl;
    this.attempt = 0;
    this.handlers = {};
    this.pingTimer = null;
  }

  on(type, handler) {
    this.handlers[type] = handler;
  }

  async connect() {
    const token = await this.getAccessToken();
    this.ws = new WebSocket(`${this.baseWsUrl}/ws/articles/stream/?token=${token}`);

    this.ws.onopen = () => {
      this.attempt = 0;
      this.pingTimer = setInterval(() => this._send({ action: 'ping' }), 30000);
    };

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      const handler = this.handlers[data.type];
      if (handler) handler(data);
    };

    this.ws.onclose = (event) => {
      clearInterval(this.pingTimer);
      if (event.code === 4401) {
        // token invalid/expired — caller should refresh before reconnect
      }
      this._scheduleReconnect();
    };

    this.ws.onerror = () => this.ws.close();
  }

  subscribe({ categories = [], sources = [] }) {
    this._send({ action: 'subscribe', categories, sources });
  }

  unsubscribe({ categories = [], sources = [] }) {
    this._send({ action: 'unsubscribe', categories, sources });
  }

  _send(payload) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }

  _scheduleReconnect() {
    const delay = Math.min(2 ** this.attempt, 30) * 1000;
    this.attempt += 1;
    setTimeout(() => this.connect(), delay);
  }

  close() {
    clearInterval(this.pingTimer);
    this.ws?.close();
  }
}

// Usage:
const stream = new ArticleStream({ getAccessToken: async () => myAccessToken });
stream.on('article.new', (msg) => addArticleToFeed(msg.article));
stream.on('breaking_news', (msg) => showBreakingBanner(msg.article, msg.priority));
stream.connect();
```

### Flutter / Dart

```dart
import 'dart:async';
import 'dart:convert';
import 'package:web_socket_channel/web_socket_channel.dart';

class ArticleStream {
  final Future<String> Function() getAccessToken;
  final String baseWsUrl;
  WebSocketChannel? _channel;
  Timer? _pingTimer;
  int _attempt = 0;

  ArticleStream({
    required this.getAccessToken,
    this.baseWsUrl = 'wss://newsapi.mwonya.com',
  });

  final _controller = StreamController<Map<String, dynamic>>.broadcast();
  Stream<Map<String, dynamic>> get messages => _controller.stream;

  Future<void> connect() async {
    final token = await getAccessToken();
    final uri = Uri.parse('$baseWsUrl/ws/articles/stream/?token=$token');
    _channel = WebSocketChannel.connect(uri);

    _pingTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      _channel?.sink.add(jsonEncode({'action': 'ping'}));
    });

    _channel!.stream.listen(
      (raw) {
        _attempt = 0;
        final data = jsonDecode(raw as String) as Map<String, dynamic>;
        _controller.add(data);
      },
      onDone: _scheduleReconnect,
      onError: (_) => _scheduleReconnect(),
    );
  }

  void subscribe({List<String> categories = const [], List<int> sources = const []}) {
    _channel?.sink.add(jsonEncode({
      'action': 'subscribe',
      'categories': categories,
      'sources': sources,
    }));
  }

  void _scheduleReconnect() {
    _pingTimer?.cancel();
    final delaySeconds = [1, 2, 4, 8, 16, 30][_attempt.clamp(0, 5)];
    _attempt++;
    Future.delayed(Duration(seconds: delaySeconds), connect);
  }

  void close() {
    _pingTimer?.cancel();
    _channel?.sink.close();
  }
}

// Usage:
final stream = ArticleStream(getAccessToken: () async => myAccessToken);
stream.messages.listen((msg) {
  switch (msg['type']) {
    case 'article.new':
      addArticleToFeed(msg['article']);
      break;
    case 'breaking_news':
      showBreakingBanner(msg['article'], msg['priority']);
      break;
  }
});
await stream.connect();
```

### Quick manual test (websocat / wscat)

```bash
# Get a token first
curl -X POST https://newsapi.mwonya.com/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "..."}'

# Then connect (websocat: brew install websocat)
websocat "wss://newsapi.mwonya.com/ws/articles/stream/?token=<ACCESS_TOKEN>"
```

## What's "real-time" about this

- The WebSocket push happens from a Django `post_save` signal on `Article`
  the instant a scraper (Celery task or management command) saves an
  article with `has_full_content=True` — no polling delay, no batch window.
- Delivery is via Redis pub/sub (Channels' `channels_redis` layer), so it
  works across multiple backend worker processes/containers, not just a
  single process.
- Breaking news articles get an additional, distinct `breaking_news` event
  on top of the regular `article.new` event so clients can show an
  interrupt-style UI for those specifically.

## Operational notes (backend team)

- The app server now runs via `gunicorn -k uvicorn.workers.UvicornWorker
  TNDNEWS.asgi:application` (see `django.sh`) instead of the WSGI worker —
  this single process serves both normal HTTP and WebSocket traffic.
- `CHANNEL_LAYERS` reuses `REDIS_DATABASE_SERVER_HOST` (the same Redis
  instance as Celery/cache) — no new infrastructure required.
- Streaming failures are caught and logged but never raise — a broken
  Channels/Redis connection cannot break article scraping or enrichment.
- There is currently no message persistence/replay: a client that is
  offline misses events that occurred while disconnected. If you need
  guaranteed delivery, pair this stream with the existing
  `GET /news/articles/?ordering=-scraped_at` REST endpoint as a periodic
  backfill, or use the existing push-notification system
  (`PushToken` / `/news/api/push-tokens/`) for guaranteed delivery of
  breaking news.
