from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from rest_framework import permissions
from drf_yasg.views import get_schema_view
from drf_yasg import openapi
from django.http import JsonResponse
from django.db import connection

from TNDNEWS import settings

schema_view = get_schema_view(
    openapi.Info(
        title="TndNews Backend Project",
        default_version='v1',
        description="Tndnews app aggregator",
        terms_of_service="https://newsapi.mwonya.com/terms/",
        contact=openapi.Contact(email="contact@mwonyanews.com"),
        license=openapi.License(name="TNDNEWS License"),
    ),
    public=True,
    permission_classes=[permissions.AllowAny, ],
    authentication_classes=[]
)


def health_check(request):
    try:
        # Check database connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "healthy"}, status=200)
    except Exception as e:
        return JsonResponse({"status": "unhealthy", "error": str(e)}, status=500)


urlpatterns = [
    path('admin/', admin.site.urls),
    # local apps
    path('auth/', include('tnd_apps.authentication.urls')),
    path('social_auth/', include(('tnd_apps.social_auth.urls', 'social_auth'), namespace="social_auth")),

    path('news/', include(('tnd_apps.news_scrapping.urls', 'news_scrapping'), namespace="news_scrapping")),

    path('intelligence/', include(('tnd_apps.newsintelligence.urls', 'news_intelligence'), namespace="news_intelligence")),

    path('health/', health_check, name='health_check'),

    path('videos/', include('tnd_apps.tndvideo.urls')),

    # Swagger endpoints
    path('', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('api/api.json/', schema_view.without_ui(cache_timeout=0), name='schema-json'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),

]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
