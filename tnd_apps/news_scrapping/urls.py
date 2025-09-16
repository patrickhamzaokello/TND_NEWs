# URLs
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import NewsSourceViewSet, ArticleViewSet, UserProfileViewSet, CommentViewSet, GetOrCreatePushTokenView, \
    ListUserPushTokensView, DeactivatePushTokenView, PushTokenDetailView, DeactivatePushTokenByValueView, \
    UpdateTokenUsageView, BulkDeactivateTokensView, CategoryViewSet

router = DefaultRouter()
router.register(r'sources', NewsSourceViewSet)
router.register(r'articles', ArticleViewSet)
router.register(r'profiles', UserProfileViewSet)
router.register(r'comments', CommentViewSet)
router.register(r'categories', CategoryViewSet)


urlpatterns = [
    path('', include(router.urls)),
    # Main push token endpoints
    path('api/push-tokens/', GetOrCreatePushTokenView.as_view(), name='get_or_create_push_token'),
    path('api/push-tokens/list/', ListUserPushTokensView.as_view(), name='list_user_push_tokens'),

    # Token management by ID
    path('api/push-tokens/<int:id>/', DeactivatePushTokenView.as_view(), name='deactivate_push_token_by_id'),
    path('api/push-tokens/<int:id>/detail/', PushTokenDetailView.as_view(), name='push_token_detail'),

    # Token management by value
    path('api/push-tokens/deactivate/', DeactivatePushTokenByValueView.as_view(),
         name='deactivate_push_token_by_value'),
    path('api/push-tokens/update-usage/', UpdateTokenUsageView.as_view(), name='update_token_usage'),

    # Bulk operations
    path('api/push-tokens/bulk-deactivate/', BulkDeactivateTokensView.as_view(), name='bulk_deactivate_tokens'),

]