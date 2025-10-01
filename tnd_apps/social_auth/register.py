from django.contrib.auth import authenticate
from tnd_apps.authentication.models import User
import random
from rest_framework.exceptions import AuthenticationFailed
from django.conf import settings



def register_social_user(provider, user_id, email, name):
    """
    Register or retrieve a user for social authentication.
    Returns user data with tokens.
    """
    # Check if user exists with the given email and provider
    existing_user = User.objects.filter(email=email, auth_provider=provider).first()

    if existing_user:
        if not existing_user.is_active:
            raise AuthenticationFailed('Account disabled, contact admin')
        if not existing_user.is_verified:
            raise AuthenticationFailed('Email is not verified')
        # Generate tokens for existing user
        return {
            'email': existing_user.email,
            'name': existing_user.name,
            'username': existing_user.username,
            'user_id': existing_user.id,
            'tokens': existing_user.tokens()
        }

    # Create new user
    try:
        user = User.objects.create_user(
            name=name,
            email=email,
            password=None  # Social users don't need a password
        )
        user.auth_provider = provider
        user.is_verified = True  # Social auth typically verifies email
        user.save()

        return {
            'email': user.email,
            'name': user.name,
            'username': user.username,
            'user_id': user.id,
            'tokens': user.tokens()
        }
    except Exception as e:
        raise AuthenticationFailed(f"Failed to create user: {str(e)}")