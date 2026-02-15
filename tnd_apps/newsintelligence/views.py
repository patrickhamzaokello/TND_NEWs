# views.py
from rest_framework import generics
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny  # ← change to IsAuthenticated if needed
from .models import DailyDigest
from .serializers import (
    DailyDigestListSerializer,
    DailyDigestDetailSerializer,
)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 60


class DailyDigestListView(generics.ListAPIView):
    """
    GET /api/digests/

    Paginated list of daily digests, newest first
    """
    queryset = DailyDigest.objects.all()
    serializer_class = DailyDigestListSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [AllowAny]  # ← or IsAuthenticated, etc.

    def get_queryset(self):
        # Most recent first + only published (optional filter)
        qs = super().get_queryset().order_by('-digest_date')

        # Optional: only show published digests in production
        # if not self.request.user.is_staff:
        #     qs = qs.filter(is_published=True)

        return qs


class DailyDigestDetailView(generics.RetrieveAPIView):
    """
    GET /api/digests/<id>/
    or /api/digests/<digest_date>/  (if you prefer date slugs)
    """
    queryset = DailyDigest.objects.all()
    serializer_class = DailyDigestDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = 'id'  # default
    # lookup_field = 'digest_date'  # ← alternative if you want /digests/2025-02-14/
    # lookup_url_kwarg = 'date'     # if using date in URL