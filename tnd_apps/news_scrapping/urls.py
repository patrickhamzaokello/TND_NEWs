# URLs
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import NewsSourceViewSet,ArticleViewSet, UserProfileViewSet

router = DefaultRouter()
router.register(r'sources', NewsSourceViewSet)
router.register(r'articles', ArticleViewSet)
router.register(r'profiles', UserProfileViewSet)

urlpatterns = [
    path('', include(router.urls)),
]