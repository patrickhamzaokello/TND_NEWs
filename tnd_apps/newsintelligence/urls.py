# urls.py
from django.urls import path
from . import views

app_name = 'digests'  # optional

urlpatterns = [
    path('digests/', views.DailyDigestListView.as_view(), name='digest-list'),
    path('digests/<int:pk>/', views.DailyDigestDetailView.as_view(), name='digest-detail'),

    # Alternative: date-based lookup (cleaner for sharing)
    # path('digests/<date:digest_date>/', views.DailyDigestDetailView.as_view(lookup_field='digest_date'), name='digest-by-date'),
]