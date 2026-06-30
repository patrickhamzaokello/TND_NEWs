"""
ASGI config for TNDNEWS project.

Serves both regular HTTP (Django views/DRF) and WebSocket connections
(real-time article streaming) from a single ASGI application.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

import django
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'TNDNEWS.settings')
django.setup()

django_asgi_app = get_asgi_application()

# Imported after django.setup() so app registry is ready before models load.
from tnd_apps.news_scrapping.routing import websocket_urlpatterns  # noqa: E402
from tnd_apps.news_scrapping.ws_auth import JWTAuthMiddlewareStack  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': JWTAuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})
