# TNDNEWS Real-Time WebSocket Stream

Live article delivery over WebSocket — built on **Django Channels 4** + **Redis Channel Layer**.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Connecting](#connecting)
3. [Authentication](#authentication)
4. [Client-to-Server Messages](#client-to-server-messages)
5. [Server-to-Client Events](#server-to-client-events)
6. [Groups (Channels)](#groups-channels)
7. [Article Payload Shape](#article-payload-shape)
8. [How Articles Enter the Stream](#how-articles-enter-the-stream)
9. [Example Client Code](#example-client-code)
10. [Limitations](#limitations)
11. [Scaling](#scaling)

---

## Architecture Overview

```
Scrapers (Celery workers)
    │ save Article to PostgreSQL
    │
    ▼
Django post_save signal  (signals.py)
    │ broadcast_new_article  →  Redis Channel Layer  →  WebSocket clients
    │ detect_breaking_news   →  Redis Channel Layer  →  WebSocket clients
    │
    ▼
ASGI server (Daphne / Uvicorn)
    │  ProtocolTypeRouter
    ├─ http  →  Django REST Framework (normal API)
    └─ websocket  →  JWTAuthMiddleware  →  URLRouter  →  ArticleStreamConsumer
```

**Key components:**

| File | Role |
|------|------|
| `TNDNEWS/asgi.py` | ASGI entrypoint — routes HTTP vs WebSocket |
| `tnd_apps/news_scrapping/ws_auth.py` | JWT middleware: authenticates the WS handshake |
| `tnd_apps/news_scrapping/routing.py` | Maps URL pattern to consumer |
| `tnd_apps/news_scrapping/consumers.py` | `ArticleStreamConsumer` — all WS logic |
| `tnd_apps/news_scrapping/signals.py` | Triggers broadcasts on article save |
| `TNDNEWS/settings.py` `CHANNEL_LAYERS` | Redis config for channel routing |

---

## Connecting

```
wss://newsapi.mwonya.com/ws/articles/stream/?token=<JWT access token>
```

Optional query parameters at connect time:

| Param | Example | Effect |
|-------|---------|--------|
| `token` | `token=eyJ...` | **Required** — JWT access token |
| `categories` | `categories=politics,sports` | Auto-subscribe to those category groups |
| `sources` | `sources=12,14` | Auto-subscribe to those source groups |

On successful connection the server sends:

```json
{
  "type": "connection_established",
  "message": "Connected to TNDNEWS real-time article stream.",
  "groups": ["articles.stream", "articles.category.politics"]
}
```

---

## Authentication

WebSocket handshakes cannot carry `Authorization` headers (browser limitation), so the JWT access token is passed as a query string parameter (`?token=...`).

**Flow:**

1. Client requests a JWT token pair from `POST /auth/login/` (same tokens used by the REST API).
2. Client opens the WebSocket with `?token=<access_token>`.
3. `JWTAuthMiddleware` (`ws_auth.py`) validates the token using `djangorestframework_simplejwt.tokens.AccessToken`, then fetches the user from the database asynchronously.
4. If invalid or missing: `scope['user']` is set to `AnonymousUser`.
5. `ArticleStreamConsumer.connect()` checks `user.is_authenticated`. If false it closes with **code 4401** (custom unauthorized code).

**Token lifetime** follows your JWT settings. Clients should re-connect with a fresh token when it expires — there is currently no in-band token refresh over WebSocket.

---

## Client-to-Server Messages

All messages are JSON objects with an `action` field.

### `ping`

Keep-alive. The server replies immediately with `pong`.

```json
{ "action": "ping" }
```

Response:
```json
{ "type": "pong" }
```

### `subscribe`

Join additional category or source groups after the initial connection.

```json
{
  "action": "subscribe",
  "categories": ["business", "technology"],
  "sources": [5, 23]
}
```

Response:
```json
{
  "type": "subscribed",
  "groups": ["articles.stream", "articles.category.business", "articles.source.5"]
}
```

### `unsubscribe`

Leave specific groups. The client stays connected and still receives firehose events.

```json
{
  "action": "unsubscribe",
  "categories": ["sports"],
  "sources": [23]
}
```

Response:
```json
{
  "type": "unsubscribed",
  "groups": ["articles.stream", "articles.category.business"]
}
```

### Unknown action

```json
{ "type": "error", "message": "Unknown action: 'foo'" }
```

---

## Server-to-Client Events

### `article.new`

Fires when a new article is scraped and has full content (`has_full_content=True`).
A Redis dedup key (`ws_broadcast:article:<id>`, TTL 24 h) ensures each article is broadcast exactly once even under concurrent workers.

```json
{
  "type": "article.new",
  "article": { ...article payload... }
}
```

### `article.updated`

Fires when an existing article is updated (e.g. content enriched after initial scrape).

```json
{
  "type": "article.updated",
  "article": { ...article payload... }
}
```

### `breaking_news`

Fires when a newly saved article is detected as breaking (keyword/category match in `detect_breaking_news` signal).

```json
{
  "type": "breaking_news",
  "priority": "high",
  "article": { ...article payload... }
}
```

`priority` values: `"high"` | `"medium"` | `"low"`

---

## Groups (Channels)

Every connected client is always in the **firehose** group and receives all events. Clients can additionally join category and source groups to filter their feed.

| Group name | Receives |
|------------|----------|
| `articles.stream` | All new articles (firehose — every client) |
| `articles.category.<slug>` | Articles in that category (e.g. `articles.category.politics`) |
| `articles.source.<id>` | Articles from that source (e.g. `articles.source.12`) |

Clients can be in multiple groups simultaneously. An article published in "Politics" from source 12 will reach:
- All firehose subscribers (everyone)
- `articles.category.politics` subscribers
- `articles.source.12` subscribers

There is **no deduplication** across groups — a client subscribed to both the firehose and `articles.category.politics` will receive the same article twice for a politics article. Clients should deduplicate by `article.id`.

---

## Article Payload Shape

All three event types include the same article shape:

```json
{
  "id": 4821,
  "title": "Parliament approves new finance bill",
  "slug": "parliament-approves-new-finance-bill",
  "excerpt": "MPs voted 214–89 in favour...",
  "featured_image_url": "https://cdn.example.com/images/finance-bill.jpg",
  "url": "https://monitor.co.ug/parliament-finance-bill",
  "source": {
    "id": 3,
    "name": "Daily Monitor",
    "favicon_url": "https://cdn.example.com/favicons/monitor.png"
  },
  "category": {
    "id": 7,
    "name": "Politics",
    "slug": "politics"
  },
  "published_at": "2026-07-04T08:15:00+03:00",
  "scraped_at": "2026-07-04T08:17:42.301Z",
  "read_time_minutes": 4,
  "has_full_content": true
}
```

`category` is `null` if the article has no category assigned.

---

## How Articles Enter the Stream

1. A Celery scraper worker saves an `Article` row to PostgreSQL with `has_full_content=True`.
2. Django's `post_save` signal fires `broadcast_new_article` in `signals.py`.
3. The signal checks `_should_broadcast()` — only newly completed articles qualify.
4. A Redis SETNX key (`ws_broadcast:article:<id>`) is claimed. If it already exists (another worker already broadcast this article), the function returns immediately.
5. The signal calls `channel_layer.group_send(FIREHOSE_GROUP, {...})` synchronously via `async_to_sync`.
6. If the article has a category, it also sends to `articles.category.<slug>`.
7. If the article has a source, it also sends to `articles.source.<id>`.
8. The Redis Channel Layer routes the message to all connected consumers in those groups.
9. Each consumer's `article_new` handler calls `self.send_json(...)` to push the event to the client.

The `detect_breaking_news` signal runs the same path but sends `{"type": "breaking_news", ...}`.

---

## Example Client Code

### JavaScript (browser / React Native)

```js
const token = await getAccessToken(); // from your auth store
const ws = new WebSocket(
  `wss://newsapi.mwonya.com/ws/articles/stream/?token=${token}&categories=politics,business`
);

const seen = new Set(); // dedup across group overlap

ws.onopen = () => {
  // optional: keep-alive every 30 s
  setInterval(() => ws.send(JSON.stringify({ action: 'ping' })), 30_000);
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);

  if (msg.type === 'article.new' || msg.type === 'article.updated') {
    if (seen.has(msg.article.id)) return; // duplicate from overlapping groups
    seen.add(msg.article.id);
    addArticleToFeed(msg.article);
  }

  if (msg.type === 'breaking_news') {
    showBreakingNewsBanner(msg.article, msg.priority);
  }
};

// Subscribe to more groups after connect
ws.send(JSON.stringify({
  action: 'subscribe',
  categories: ['technology'],
  sources: [12],
}));

// Leave a group
ws.send(JSON.stringify({
  action: 'unsubscribe',
  categories: ['politics'],
}));
```

### Python (testing / backend-to-backend)

```python
import asyncio, json
import websockets

async def listen():
    token = "eyJ..."
    uri = f"wss://newsapi.mwonya.com/ws/articles/stream/?token={token}"
    async with websockets.connect(uri) as ws:
        async for raw in ws:
            msg = json.loads(raw)
            print(msg['type'], msg.get('article', {}).get('title', ''))

asyncio.run(listen())
```

---

## Limitations

### Authentication
- **No token refresh over WebSocket.** When the JWT access token expires, the connection does not automatically renew. Clients must reconnect with a new token.
- Token is in the URL query string — visible in server access logs. Use HTTPS/WSS and short-lived access tokens to mitigate.

### Delivery guarantees
- **Fire-and-forget.** There is no acknowledgement mechanism. If a client is temporarily disconnected and reconnects, it will miss articles published during the outage.
- **No message replay or history.** Clients that miss events must call the REST API (`/api/articles/latest/`) to back-fill.
- **No ordering guarantee across groups.** A client in both firehose and a category group will receive two copies of the same article; the order of those two deliveries is undefined.

### Scale
- **Single Redis instance is a bottleneck.** All channel layer traffic goes through one Redis. On high-throughput days (breaking news storms) this can become a hotspot.
- **`async_to_sync` in signals.** The `broadcast_new_article` signal runs `async_to_sync(channel_layer.group_send)(...)` from synchronous Django signal code. This works correctly but blocks the Celery worker thread while the Redis round-trip completes (~1–5 ms typically).
- **No per-user filtering on the server.** Filtering is group-based (category/source). Fine-grained filters (e.g. "only articles from sources I follow") must be implemented client-side.
- **No backpressure.** A slow client that cannot consume messages fast enough will accumulate messages in the Channels layer buffer until the server-side WebSocket times out.

### Infrastructure
- Requires an ASGI server (Daphne or Uvicorn) — the standard Gunicorn/WSGI setup does not support WebSockets.
- Django ORM calls inside `connect()` (user fetch in `ws_auth.py`) use `database_sync_to_async`, which runs them in a thread pool. The thread pool size is bounded — very high connection rates could exhaust it.

---

## Scaling

### Horizontal scaling (multiple ASGI workers)

The Redis Channel Layer (`channels_redis`) already supports multiple ASGI worker processes on the same machine or across multiple machines. All workers share the same Redis pub/sub, so a `group_send` from a Celery worker reaches every client regardless of which ASGI process holds their connection.

```
                ┌─ ASGI worker 1 ──► WS clients A, B
Celery worker ──┤ Redis Channel Layer
                └─ ASGI worker 2 ──► WS clients C, D
```

Run multiple Daphne/Uvicorn instances behind a load balancer (nginx, AWS ALB). Enable sticky sessions OR use a stateless design — since all state is in Redis, sticky sessions are not required.

### Redis Channel Layer tuning

```python
# settings.py
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [REDIS_URL],
            'capacity': 1500,         # max messages buffered per group (default 100)
            'expiry': 10,             # seconds a message lives if undelivered (default 60)
            'group_expiry': 86400,    # seconds a group membership lives (default 86400)
        },
    },
}
```

Increase `capacity` if you see `ChannelFull` errors during breaking-news bursts. Lower `expiry` aggressively drops stale messages for slow clients.

### Redis Sentinel / Cluster

For high availability, point `hosts` at a Redis Sentinel or Redis Cluster:

```python
# Sentinel example
'hosts': [{'sentinels': [('sentinel-1', 26379), ('sentinel-2', 26379)], 'master_name': 'mymaster'}]
```

Separate the channel layer Redis from the cache/Celery Redis so a cache flush does not disrupt WebSocket delivery.

### Connection limits

| Layer | Default limit | How to raise |
|-------|--------------|--------------|
| ASGI worker file descriptors | OS default (~1024) | `ulimit -n 65536` in your systemd unit |
| Daphne worker threads | 20 | `--thread-count` flag |
| Uvicorn workers | 1 per process | Add `--workers N` (each handles its own async loop) |
| Redis max connections | 10 000 | `maxclients` in `redis.conf` |

A single Uvicorn worker can hold **thousands of concurrent WebSocket connections** (they are coroutines, not threads). For most news applications one ASGI process handles 5 000–20 000 concurrent readers comfortably.

### Adding message history / replay

The current implementation has no replay. To add it:

1. In `broadcast_new_article`, write each event to a Redis Stream (`XADD articles:stream * ...`) with a 24-hour TTL.
2. On WebSocket connect, accept an optional `?last_event_id=<stream_id>` query param.
3. Read missed events from the stream (`XRANGE articles:stream <last_event_id> +`) and send them to the client before joining live groups.

### Rate-limiting connections

Add a middleware layer (e.g. `channels_ratelimit` or a custom `BaseMiddleware`) that checks a Redis counter keyed by user ID or IP, rejecting connections above a threshold. This prevents a single user from opening hundreds of connections.

### Monitoring

Key metrics to watch:

| Metric | Tool | Alert when |
|--------|------|-----------|
| Redis pub/sub message rate | Redis `INFO stats` → `total_commands_processed` | Sudden spike = broadcast storm |
| Channel layer queue depth | Custom: `LLEN` on group keys | > `capacity` setting |
| ASGI worker memory | Prometheus / DataDog | > 500 MB per worker |
| WebSocket error rate (4401) | ASGI access logs | Spike = token expiry issue |
| `ChannelFull` exceptions | Django logs | Any = raise `capacity` |
