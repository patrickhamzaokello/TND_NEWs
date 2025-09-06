# URLs
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import NewsSourceViewSet, ArticleViewSet, UserProfileViewSet, CommentViewSet

router = DefaultRouter()
router.register(r'sources', NewsSourceViewSet)
router.register(r'articles', ArticleViewSet)
router.register(r'profiles', UserProfileViewSet)
router.register(r'comments', CommentViewSet)

urlpatterns = [
    path('', include(router.urls)),
]