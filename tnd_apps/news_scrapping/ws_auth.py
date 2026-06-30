"""
JWT authentication for Channels WebSocket connections.

Browsers/mobile WebSocket clients cannot set an Authorization header during
the handshake, so the access token is passed as a query string parameter:

    wss://newsapi.mwonya.com/ws/articles/stream/?token=<JWT access token>

This mirrors the existing JWTAuthentication used by DRF
(djangorestframework_simplejwt), so the same access tokens issued by
/auth/login work here unchanged.
"""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken


@database_sync_to_async
def _get_user_from_token(token: str):
    from django.contrib.auth import get_user_model

    try:
        validated = AccessToken(token)
        user_id = validated.get('user_id')
        if not user_id:
            return AnonymousUser()
        User = get_user_model()
        return User.objects.get(id=user_id, is_active=True)
    except (InvalidToken, TokenError, Exception):
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    """Authenticates a Channels scope using a ?token=<JWT> query param."""

    async def __call__(self, scope, receive, send):
        query_string = scope.get('query_string', b'').decode()
        params = parse_qs(query_string)
        token = params.get('token', [None])[0]

        scope['user'] = await _get_user_from_token(token) if token else AnonymousUser()
        return await super().__call__(scope, receive, send)


def JWTAuthMiddlewareStack(inner):
    return JWTAuthMiddleware(inner)
