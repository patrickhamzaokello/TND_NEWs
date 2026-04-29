from django.core.cache import cache
from django.shortcuts import render
from rest_framework import generics, status, views, permissions, serializers
from rest_framework.exceptions import AuthenticationFailed
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
import uuid

logger = logging.getLogger(__name__)


def normalize_response_value(value):
    if isinstance(value, dict):
        return {key: normalize_response_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_response_value(item) for item in value]
    return str(value)


def success_response(data=None, message='', http_status=status.HTTP_200_OK):
    payload = {'success': True}
    if message:
        payload['message'] = message
    if data is not None:
        payload['data'] = data
    return Response(payload, status=http_status)


def error_response(message, http_status=status.HTTP_400_BAD_REQUEST, errors=None, code='validation_error'):
    payload = {
        'success': False,
        'message': normalize_response_value(message),
        'code': normalize_response_value(code),
    }
    if errors is not None:
        payload['errors'] = normalize_response_value(errors)
    return Response(payload, status=http_status)


def generate_token_code():
    """Generate a 6-digit random code"""
    return ''.join(random.choices(string.digits, k=6))


class CustomRedirect(HttpResponsePermanentRedirect):
    allowed_schemes = [os.environ.get('APP_SCHEME'), 'http', 'https']


class RegisterView(generics.GenericAPIView):
    serializer_class = RegisterSerializer
    permission_classes = (permissions.AllowAny,)

    def post(self, request):
        try:
            serializer = self.serializer_class(data=request.data)
            if not serializer.is_valid():
                email_errors = serializer.errors.get('email', [])
                if any('already exists' in str(error).lower() for error in email_errors):
                    return error_response(
                        'An account with this email already exists.',
                        http_status=status.HTTP_409_CONFLICT,
                        errors=serializer.errors,
                        code='user_already_exists',
                    )
                return error_response(
                    'Registration validation failed.',
                    errors=serializer.errors,
                )

            user = serializer.save()

            if not settings.EMAIL_VERIFICATION_REQUIRED:
                user.is_verified = True
                user.save(update_fields=['is_verified'])
                response_data = RegisterSerializer(user).data
                response_data.update({
                    'verification_required': False,
                    'tokens': user.tokens(),
                })
                return success_response(
                    response_data,
                    message='Registration successful.',
                    http_status=status.HTTP_201_CREATED,
                )

            # Generate 6-digit verification code
            verification_code = generate_token_code()

            # Store the code in cache with 30-minute expiry
            cache_key = f"email_verification_{user.pk}"
            cache.set(cache_key, {
                'code': verification_code,
                'user_id': user.pk,
                'attempts': 0,
                'email': user.email
            }, timeout=1800)  # 30 minutes

            # Prepare verification email with code
            email_body = f'''Hello {user.name},

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

            email_sent = Util.send_email(email_data)

            # Include user data and verification instructions in response
            response_data = RegisterSerializer(user).data
            response_data.update({
                'verification_required': True,
                'code_expires_in': '30 minutes',
                'email_sent': email_sent,
            })

            if email_sent:
                return success_response(
                    response_data,
                    message='Registration successful. Please check your email for verification code.',
                    http_status=status.HTTP_201_CREATED,
                )

            logger.warning(f"Failed to send verification email to {user.email}")
            return success_response(
                response_data,
                message='Registration successful, but the verification email could not be sent. Please request a new code or contact support.',
                http_status=status.HTTP_201_CREATED,
            )

        except serializers.ValidationError:
            raise
        except Exception as e:
            logger.error(f"Error in user registration: {str(e)}", exc_info=True)
            return error_response(
                'Registration failed due to a server error.',
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code='registration_failed',
            )


class VerifyEmailAPIView(views.APIView):
    serializer_class = EmailVerificationSerializer
    permission_classes = (permissions.AllowAny,)

    code_param_config = openapi.Parameter(
        'code', in_=openapi.IN_QUERY, description='6-digit verification code', type=openapi.TYPE_STRING)

    @swagger_auto_schema(manual_parameters=[code_param_config])
    def post(self, request):
        try:
            serializer = self.serializer_class(data=request.data)
            if not serializer.is_valid():
                return error_response('Email verification validation failed.', errors=serializer.errors)

            email = request.data.get('email')
            code = request.data.get('code')

            # Get user
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                return error_response('Invalid email or verification code.', code='invalid_verification')

            # Check if already verified
            if user.is_verified:
                return success_response({
                    'user_id': str(user.id),
                    'email': user.email
                }, message='Email is already verified')

            # Check cache for the verification code
            cache_key = f"email_verification_{user.pk}"
            cached_data = cache.get(cache_key)

            if not cached_data:
                return error_response('Verification code has expired. Please request a new one.', code='verification_code_expired')

            # Check attempts (prevent brute force)
            if cached_data['attempts'] >= 5:  # More attempts for email verification
                cache.delete(cache_key)
                return error_response('Too many failed attempts. Please request a new verification code.', code='too_many_attempts')

            # Verify the code
            if cached_data['code'] != code:
                # Increment attempts
                cached_data['attempts'] += 1
                cache.set(cache_key, cached_data, timeout=1800)

                return error_response(
                    'Invalid verification code.',
                    errors={'code': ['Invalid verification code'], 'attempts_remaining': 5 - cached_data['attempts']},
                    code='invalid_verification_code',
                )

            # Code is valid - verify the user
            user.is_verified = True
            user.save()

            # Clear the verification cache
            cache.delete(cache_key)

            # Generate tokens for the verified user
            refresh = RefreshToken.for_user(user)

            return success_response({
                'user_id': str(user.id),
                'email': user.email,
                'username': user.username,
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token)
                }
            }, message='Email verified successfully')

        except Exception as e:
            logger.error(f"Error in email verification: {str(e)}")
            return error_response('Email verification failed. Please try again.', http_status=status.HTTP_500_INTERNAL_SERVER_ERROR, code='email_verification_failed')


class ResendVerificationCodeAPIView(views.APIView):
    serializer_class = ResendVerificationCodeSerializer
    permission_classes = (permissions.AllowAny,)

    def post(self, request):
        try:
            serializer = self.serializer_class(data=request.data)
            if not serializer.is_valid():
                return error_response('Resend verification validation failed.', errors=serializer.errors)

            email = request.data.get('email')

            # Get user
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                return error_response('No account found with this email address.', http_status=status.HTTP_404_NOT_FOUND, code='user_not_found')

            # Check if already verified
            if user.is_verified:
                return success_response(message='Email is already verified')

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
                return error_response('Failed to send verification code. Please try again.', http_status=status.HTTP_503_SERVICE_UNAVAILABLE, code='email_delivery_failed')

            return success_response({
                'code_expires_in': '30 minutes'
            }, message='Please check your email for the new 6-digit verification code')

        except Exception as e:
            logger.error(f"Error in resending verification code: {str(e)}")
            return error_response('Failed to resend verification code. Please try again.', http_status=status.HTTP_500_INTERNAL_SERVER_ERROR, code='resend_verification_failed')


class LoginAPIView(generics.GenericAPIView):
    serializer_class = LoginSerializer
    permission_classes = (permissions.AllowAny,)

    def post(self, request):
        try:
            serializer = self.serializer_class(data=request.data, context={'request': request})
            if not serializer.is_valid():
                return error_response('Login validation failed.', errors=serializer.errors)
            logger.debug(f"Login response data: {serializer.data}")
            return success_response(serializer.data, message='Login successful')
        except AuthenticationFailed as exc:
            detail = exc.detail
            message = str(detail)
            code = 'authentication_failed'
            if isinstance(detail, dict):
                message = detail.get('message') or detail.get('detail') or message
                code = detail.get('code') or code
            return error_response(message, http_status=status.HTTP_401_UNAUTHORIZED, code=code)


class RequestPasswordResetEmail(generics.GenericAPIView):
    serializer_class = ResetPasswordEmailRequestSerializer
    permission_classes = (permissions.AllowAny,)

    def post(self, request):
        try:
            serializer = self.serializer_class(data=request.data)
            if not serializer.is_valid():
                return error_response('Password reset validation failed.', errors=serializer.errors)

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
                    return error_response('Failed to send reset code. Please try again.', http_status=status.HTTP_503_SERVICE_UNAVAILABLE, code='email_delivery_failed')

            # Always return success (don't reveal if email exists)
            return success_response({
                'code_expires_in': '15 minutes'
            }, message='If an account with this email exists, we have sent you a password reset code.')

        except Exception as e:
            logger.error(f"Error in password reset request: {str(e)}")
            return error_response('Password reset request failed. Please try again.', http_status=status.HTTP_500_INTERNAL_SERVER_ERROR, code='password_reset_request_failed')


class VerifyResetCodeAPIView(generics.GenericAPIView):
    serializer_class = VerifyResetCodeSerializer
    permission_classes = (permissions.AllowAny,)

    def post(self, request):
        try:
            serializer = self.serializer_class(data=request.data)
            if not serializer.is_valid():
                return error_response('Reset code validation failed.', errors=serializer.errors)

            email = request.data.get('email')
            code = request.data.get('code')

            # Get user
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                return error_response('Invalid email or code.', code='invalid_reset_code')

            # Check cache for the code
            cache_key = f"password_reset_{user.pk}"
            cached_data = cache.get(cache_key)

            if not cached_data:
                return error_response('Reset code has expired. Please request a new one.', code='reset_code_expired')

            # Check attempts (prevent brute force)
            if cached_data['attempts'] >= 3:
                cache.delete(cache_key)
                return error_response('Too many failed attempts. Please request a new reset code.', code='too_many_attempts')

            # Verify the code
            if cached_data['code'] != code:
                # Increment attempts
                cached_data['attempts'] += 1
                cache.set(cache_key, cached_data, timeout=900)

                return error_response(
                    'Invalid reset code.',
                    errors={'code': ['Invalid reset code'], 'attempts_remaining': 3 - cached_data['attempts']},
                    code='invalid_reset_code',
                )

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

            return success_response({
                'reset_token': reset_token,
                'uidb64': uidb64,
                'expires_in': '10 minutes'
            }, message='Code verified successfully')

        except Exception as e:
            logger.error(f"Error in code verification: {str(e)}")
            return error_response('Code verification failed. Please try again.', http_status=status.HTTP_500_INTERNAL_SERVER_ERROR, code='reset_code_verification_failed')


class SetNewPasswordAPIView(generics.GenericAPIView):
    serializer_class = SetNewPasswordSerializer
    permission_classes = (permissions.AllowAny,)

    def patch(self, request):
        try:
            serializer = self.serializer_class(data=request.data)
            if not serializer.is_valid():
                return error_response('Password reset completion validation failed.', errors=serializer.errors)

            # Get user info for response
            uidb64 = request.data.get('uidb64')
            user_id = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=user_id)

            # Verify the reset session is still valid
            reset_session_key = f"reset_session_{user.pk}"
            session_data = cache.get(reset_session_key)

            if not session_data or not session_data.get('verified'):
                return error_response('Reset session expired. Please verify your code again.', code='reset_session_expired')

            # Clear the reset session
            cache.delete(reset_session_key)

            return success_response({
                'user_id': str(user.id),
                'email': user.email
            }, message='Password reset successful')

        except Exception as e:
            logger.error(f"Error in password reset completion: {str(e)}")
            if isinstance(e, AuthenticationFailed):
                detail = e.detail
                message = str(detail)
                code = 'password_reset_failed'
                if isinstance(detail, dict):
                    message = detail.get('message') or detail.get('detail') or message
                    code = detail.get('code') or code
                return error_response(message, http_status=status.HTTP_400_BAD_REQUEST, code=code)
            return error_response('Password reset failed. Please try again.', http_status=status.HTTP_500_INTERNAL_SERVER_ERROR, code='password_reset_failed')


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
            return success_response(message="Successfully logged out")
        except Exception as e:
            return error_response("Logout failed", code='logout_failed')
