from django.core.cache import cache
from django.shortcuts import render
from rest_framework import generics, status, views, permissions
from .serializers import RegisterSerializer, SetNewPasswordSerializer, ResetPasswordEmailRequestSerializer, \
    EmailVerificationSerializer, LoginSerializer, LogoutSerializer, VerifyResetCodeSerializer, \
    ResendVerificationCodeSerializer
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from .models import User
from .utils import Util
from django.contrib.sites.shortcuts import get_current_site
from django.urls import reverse
import jwt
from django.conf import settings
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from .renderers import UserRenderer
from django.contrib.auth.tokens import PasswordResetTokenGenerator, default_token_generator
from django.utils.encoding import smart_str, force_str, smart_bytes, DjangoUnicodeDecodeError, force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.contrib.sites.shortcuts import get_current_site
from django.urls import reverse
from .utils import Util
from django.shortcuts import redirect
from django.http import HttpResponsePermanentRedirect
import os
import random
import string
import logging

logger = logging.getLogger(__name__)


def generate_token_code():
    """Generate a 6-digit random code"""
    return ''.join(random.choices(string.digits, k=6))


class CustomRedirect(HttpResponsePermanentRedirect):
    allowed_schemes = [os.environ.get('APP_SCHEME'), 'http', 'https']


class RegisterView(generics.GenericAPIView):
    serializer_class = RegisterSerializer
    renderer_classes = (UserRenderer,)

    def post(self, request):
        try:
            user_data = request.data
            serializer = self.serializer_class(data=user_data)
            serializer.is_valid(raise_exception=True)
            serializer.save()

            user_response_data = serializer.data
            user = User.objects.get(email=user_response_data['email'])

            # Generate 6-digit verification code
            verification_code = generate_token_code()

            # Store the code in cache with 30-minute expiry (longer for email verification)
            cache_key = f"email_verification_{user.pk}"
            cache.set(cache_key, {
                'code': verification_code,
                'user_id': user.pk,
                'attempts': 0,
                'email': user.email
            }, timeout=1800)  # 30 minutes

            # Prepare verification email with code
            email_body = f'''Hello {user.username},

Welcome to our platform! To complete your registration, please verify your email address.

Your email verification code is: {verification_code}

This code will expire in 30 minutes for security reasons.
Please enter this code in the app to verify your email address.

If you didn't create this account, please ignore this email.

Best regards,
AEACBIO TEAM'''

            email_data = {
                'email_body': email_body,
                'to_email': user.email,
                'email_subject': 'Verify Your Email Address'
            }

            # Send verification email
            email_sent = Util.send_email(email_data)

            if not email_sent:
                logger.warning(f"Failed to send verification email to {user.email}")
                # Clean up cache if email failed
                cache.delete(cache_key)
                return Response(
                    {'error': 'Failed to send verification code. Please try again.'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            # Include user data and verification instructions in response
            response_data = user_response_data.copy()
            response_data.update({
                'message': 'Registration successful. Please check your email for verification code.',
                'verification_required': True,
                'code_expires_in': '30 minutes'
            })

            return Response(response_data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Error in user registration: {str(e)}")
            return Response(
                {'error': 'Registration failed. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VerifyEmailAPIView(views.APIView):
    serializer_class = EmailVerificationSerializer

    code_param_config = openapi.Parameter(
        'code', in_=openapi.IN_QUERY, description='6-digit verification code', type=openapi.TYPE_STRING)

    @swagger_auto_schema(manual_parameters=[code_param_config])
    def post(self, request):
        try:
            serializer = self.serializer_class(data=request.data)
            serializer.is_valid(raise_exception=True)

            email = request.data.get('email')
            code = request.data.get('code')

            # Get user
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                return Response({
                    'error': 'Invalid email or verification code'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Check if already verified
            if user.is_verified:
                return Response({
                    'success': True,
                    'message': 'Email is already verified',
                    'user_id': str(user.id),
                    'email': user.email
                }, status=status.HTTP_200_OK)

            # Check cache for the verification code
            cache_key = f"email_verification_{user.pk}"
            cached_data = cache.get(cache_key)

            if not cached_data:
                return Response({
                    'error': 'Verification code has expired. Please request a new one.'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Check attempts (prevent brute force)
            if cached_data['attempts'] >= 5:  # More attempts for email verification
                cache.delete(cache_key)
                return Response({
                    'error': 'Too many failed attempts. Please request a new verification code.'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Verify the code
            if cached_data['code'] != code:
                # Increment attempts
                cached_data['attempts'] += 1
                cache.set(cache_key, cached_data, timeout=1800)

                return Response({
                    'error': 'Invalid verification code',
                    'attempts_remaining': 5 - cached_data['attempts']
                }, status=status.HTTP_400_BAD_REQUEST)

            # Code is valid - verify the user
            user.is_verified = True
            user.save()

            # Clear the verification cache
            cache.delete(cache_key)

            # Generate tokens for the verified user
            refresh = RefreshToken.for_user(user)

            return Response({
                'success': True,
                'message': 'Email verified successfully',
                'user_id': str(user.id),
                'email': user.email,
                'username': user.username,
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token)
                }
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error in email verification: {str(e)}")
            return Response(
                {'error': 'Email verification failed. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ResendVerificationCodeAPIView(views.APIView):
    serializer_class = ResendVerificationCodeSerializer

    def post(self, request):
        try:
            serializer = self.serializer_class(data=request.data)
            serializer.is_valid(raise_exception=True)

            email = request.data.get('email')

            # Get user
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                return Response({
                    'error': 'No account found with this email address'
                }, status=status.HTTP_404_NOT_FOUND)

            # Check if already verified
            if user.is_verified:
                return Response({
                    'message': 'Email is already verified'
                }, status=status.HTTP_200_OK)

            # Check if there's an existing code (rate limiting)
            cache_key = f"email_verification_{user.pk}"
            existing_data = cache.get(cache_key)

            # Generate new verification code
            verification_code = generate_token_code()

            # Store the new code in cache
            cache.set(cache_key, {
                'code': verification_code,
                'user_id': user.pk,
                'attempts': 0,
                'email': user.email
            }, timeout=1800)  # 30 minutes

            # Prepare email
            email_body = f'''Hello {user.username},

Here is your new email verification code: {verification_code}

This code will expire in 30 minutes for security reasons.
Please enter this code in the app to verify your email address.

Best regards,
AEACBIO TEAM'''

            email_data = {
                'email_body': email_body,
                'to_email': user.email,
                'email_subject': 'New Email Verification Code'
            }

            # Send email
            email_sent = Util.send_email(email_data)

            if not email_sent:
                logger.warning(f"Failed to resend verification email to {user.email}")
                cache.delete(cache_key)
                return Response(
                    {'error': 'Failed to send verification code. Please try again.'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            return Response({
                'success': 'New verification code sent to your email',
                'message': 'Please check your email for the new 6-digit verification code',
                'code_expires_in': '30 minutes'
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error in resending verification code: {str(e)}")
            return Response(
                {'error': 'Failed to resend verification code. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class LoginAPIView(generics.GenericAPIView):
    serializer_class = LoginSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class RequestPasswordResetEmail(generics.GenericAPIView):
    serializer_class = ResetPasswordEmailRequestSerializer

    def post(self, request):
        try:
            serializer = self.serializer_class(data=request.data)
            serializer.is_valid(raise_exception=True)

            email = request.data.get('email', '')

            if User.objects.filter(email=email).exists():
                user = User.objects.get(email=email)

                # Generate 6-digit reset code
                reset_code = generate_token_code()

                # Store the code in cache/redis with 15-minute expiry
                cache_key = f"password_reset_{user.pk}"
                cache.set(cache_key, {
                    'code': reset_code,
                    'user_id': user.pk,
                    'attempts': 0
                }, timeout=900)  # 15 minutes

                # Email content with the reset code
                email_body = f'''Hello {user.username},

                You requested a password reset for your account.

                Your password reset code is: {reset_code}

                This code will expire in 15 minutes for security reasons.
                Please enter this code in your app to proceed with password reset.

                If you didn't request this reset, please ignore this email.

                Best regards,
                AEACBIO TEAM'''

                email_data = {
                    'email_body': email_body,
                    'to_email': user.email,
                    'email_subject': 'Your Password Reset Code'
                }

                email_sent = Util.send_email(email_data)

                if not email_sent:
                    logger.warning(f"Failed to send password reset email to {user.email}")
                    # Clean up cache if email failed
                    cache.delete(cache_key)
                    return Response(
                        {'error': 'Failed to send reset code. Please try again.'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )

            # Always return success (don't reveal if email exists)
            return Response({
                'success': 'If an account with this email exists, we have sent you a password reset code.',
                'message': 'Please check your email for the 6-digit reset code.',
                'code_expires_in': '15 minutes'
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error in password reset request: {str(e)}")
            return Response(
                {'error': 'Password reset request failed. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VerifyResetCodeAPIView(generics.GenericAPIView):
    serializer_class = VerifyResetCodeSerializer

    def post(self, request):
        try:
            serializer = self.serializer_class(data=request.data)
            serializer.is_valid(raise_exception=True)

            email = request.data.get('email')
            code = request.data.get('code')

            # Get user
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                return Response({
                    'error': 'Invalid email or code'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Check cache for the code
            cache_key = f"password_reset_{user.pk}"
            cached_data = cache.get(cache_key)

            if not cached_data:
                return Response({
                    'error': 'Reset code has expired. Please request a new one.'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Check attempts (prevent brute force)
            if cached_data['attempts'] >= 3:
                cache.delete(cache_key)
                return Response({
                    'error': 'Too many failed attempts. Please request a new reset code.'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Verify the code
            if cached_data['code'] != code:
                # Increment attempts
                cached_data['attempts'] += 1
                cache.set(cache_key, cached_data, timeout=900)

                return Response({
                    'error': 'Invalid reset code',
                    'attempts_remaining': 3 - cached_data['attempts']
                }, status=status.HTTP_400_BAD_REQUEST)

            # Code is valid - generate secure token for password reset
            reset_token = default_token_generator.make_token(user)
            uidb64 = urlsafe_base64_encode(force_bytes(user.pk))

            # Store the validated session (short expiry - 10 minutes)
            reset_session_key = f"reset_session_{user.pk}"
            cache.set(reset_session_key, {
                'token': reset_token,
                'uidb64': uidb64,
                'verified': True
            }, timeout=600)  # 10 minutes

            # Clear the code cache
            cache.delete(cache_key)

            return Response({
                'success': True,
                'message': 'Code verified successfully',
                'reset_token': reset_token,
                'uidb64': uidb64,
                'expires_in': '10 minutes'
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error in code verification: {str(e)}")
            return Response(
                {'error': 'Code verification failed. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class SetNewPasswordAPIView(generics.GenericAPIView):
    serializer_class = SetNewPasswordSerializer

    def patch(self, request):
        try:
            serializer = self.serializer_class(data=request.data)
            serializer.is_valid(raise_exception=True)

            # Get user info for response
            uidb64 = request.data.get('uidb64')
            user_id = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=user_id)

            # Verify the reset session is still valid
            reset_session_key = f"reset_session_{user.pk}"
            session_data = cache.get(reset_session_key)

            if not session_data or not session_data.get('verified'):
                return Response({
                    'error': 'Reset session expired. Please verify your code again.'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Clear the reset session
            cache.delete(reset_session_key)

            return Response({
                'success': True,
                'message': 'Password reset successful',
                'user_id': str(user.id),
                'email': user.email
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error in password reset completion: {str(e)}")
            return Response(
                {'error': 'Password reset failed. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class LogoutAPIView(generics.GenericAPIView):
    serializer_class = LogoutSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        """
        Logout user by blacklisting the refresh token
        """
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(
                {"message": "Successfully logged out"},
                status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response(
                {"error": "Logout failed"},
                status=status.HTTP_400_BAD_REQUEST
            )