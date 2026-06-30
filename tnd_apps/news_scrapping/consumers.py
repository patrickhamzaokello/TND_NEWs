"""
Real-time article stream over WebSocket.

Clients connect to:
    wss://<host>/ws/articles/stream/?token=<JWT access token>

On connect they are auto-joined to the firehose group ("articles.stream") and
receive every newly scraped article as it lands. They can additionally scope
the stream to specific categories/sources either via query params at connect
time or by sending {"action": "subscribe", ...} messages afterwards.

See docs in tnd_apps/news_scrapping/REALTIME_STREAMING.md for the full
client-facing protocol and example client code.
"""

import logging
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)

FIREHOSE_GROUP = 'articles.stream'


def category_group(slug: str) -> str:
    return f'articles.category.{slug}'


def source_group(source_id) -> str:
    return f'articles.source.{source_id}'


class ArticleStreamConsumer(AsyncJsonWebsocketConsumer):

    async def connect(self):
        self.user = self.scope.get('user')
        if self.user is None or not getattr(self.user, 'is_authenticated', False):
            await self.close(code=4401)  # custom code: unauthorized
            return

        self.joined_groups = set()
        await self._join(FIREHOSE_GROUP)

        # Optional initial scoping via query params:
        # ?categories=politics,sports&sources=12,14
        query_params = parse_qs(self.scope.get('query_string', b'').decode())
        for slug in query_params.get('categories', [''])[0].split(','):
            slug = slug.strip()
            if slug:
                await self._join(category_group(slug))
        for source_id in query_params.get('sources', [''])[0].split(','):
            source_id = source_id.strip()
            if source_id:
                await self._join(source_group(source_id))

        await self.accept()
        await self.send_json({
            'type': 'connection_established',
            'message': 'Connected to TNDNEWS real-time article stream.',
            'groups': sorted(self.joined_groups),
        })

    async def disconnect(self, close_code):
        for group in list(getattr(self, 'joined_groups', [])):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        action = content.get('action')

        if action == 'ping':
            await self.send_json({'type': 'pong'})
            return

        if action == 'subscribe':
            for slug in content.get('categories', []):
                await self._join(category_group(slug))
            for source_id in content.get('sources', []):
                await self._join(source_group(source_id))
            await self.send_json({'type': 'subscribed', 'groups': sorted(self.joined_groups)})
            return

        if action == 'unsubscribe':
            for slug in content.get('categories', []):
                await self._leave(category_group(slug))
            for source_id in content.get('sources', []):
                await self._leave(source_group(source_id))
            await self.send_json({'type': 'unsubscribed', 'groups': sorted(self.joined_groups)})
            return

        await self.send_json({'type': 'error', 'message': f'Unknown action: {action!r}'})

    async def _join(self, group: str):
        if group not in self.joined_groups:
            await self.channel_layer.group_add(group, self.channel_name)
            self.joined_groups.add(group)

    async def _leave(self, group: str):
        if group in self.joined_groups:
            await self.channel_layer.group_discard(group, self.channel_name)
            self.joined_groups.discard(group)

    # ── Group event handlers ──────────────────────────────────────────────
    # Dispatched by Channels when something calls
    # channel_layer.group_send(group, {"type": "article_new", ...})

    async def article_new(self, event):
        await self.send_json({
            'type': 'article.new',
            'article': event['article'],
        })

    async def article_updated(self, event):
        await self.send_json({
            'type': 'article.updated',
            'article': event['article'],
        })

    async def breaking_news(self, event):
        await self.send_json({
            'type': 'breaking_news',
            'article': event['article'],
            'priority': event.get('priority', 'medium'),
        })
