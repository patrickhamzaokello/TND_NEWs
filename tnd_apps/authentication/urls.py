from django.urls import path
from .views import RegisterView, LogoutAPIView, SetNewPasswordAPIView, LoginAPIView, \
    RequestPasswordResetEmail, VerifyResetCodeAPIView, VerifyEmailAPIView, ResendVerificationCodeAPIView
from rest_framework_simplejwt.views import (
    TokenRefreshView,
)


urlpatterns = [

    path('login/', LoginAPIView.as_view(), name="login"),
    path('logout/', LogoutAPIView.as_view(), name="logout"),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    ##### Registration and Email Verification
    path('register/', RegisterView.as_view(), name="register"),
    path('verify-email/', VerifyEmailAPIView.as_view(), name="verify-email"),
    path('resend-verification-code/', ResendVerificationCodeAPIView.as_view(), name="resend-verification-code"),

    ##### Password Reset Flow
    path('request-reset-email/', RequestPasswordResetEmail.as_view(), name="request-reset-email"),
    path('verify-reset-code/', VerifyResetCodeAPIView.as_view(), name="verify-reset-code"),
    path('password-reset-complete/', SetNewPasswordAPIView.as_view(), name='password-reset-complete'),
]