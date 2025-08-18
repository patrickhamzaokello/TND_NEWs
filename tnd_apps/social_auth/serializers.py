from rest_framework import serializers
from . import google, facebook, twitterhelper, apple
from .register import register_social_user
from rest_framework.exceptions import AuthenticationFailed
from django.conf import settings


class FacebookSocialAuthSerializer(serializers.Serializer):
    """Handles serialization of facebook related data"""
    auth_token = serializers.CharField()

    def validate_auth_token(self, auth_token):
        user_data = facebook.Facebook.validate(auth_token)

        try:
            user_id = user_data['id']
            email = user_data['email']
            name = user_data['name']
            provider = 'facebook'
            return register_social_user(
                provider=provider,
                user_id=user_id,
                email=email,
                name=name
            )
        except Exception as identifier:

            raise serializers.ValidationError(
                'The token  is invalid or expired. Please login again.'
            )

class AppleSocialAuthSerializer(serializers.Serializer):
    auth_token = serializers.CharField()
    @staticmethod
    def validate_auth_token(auth_token):
        try:
            user_data = apple.Apple.validate(auth_token)
        except ValueError as e:
            raise serializers.ValidationError(str(e))

        aud = user_data.get('aud')
        valid_client_ids = getattr(settings, 'APPLE_CLIENT_IDS', [])
        if aud not in valid_client_ids:
            raise AuthenticationFailed(f"Invalid Apple client ID (aud): {aud}")

        return register_social_user(
            provider='apple',
            user_id=user_data['sub'],
            email=user_data.get('email'),
            name=user_data.get('name', 'Apple User'),
        )



class GoogleSocialAuthSerializer(serializers.Serializer):
    auth_token = serializers.CharField()

    def validate_auth_token(self, auth_token):
        user_data = google.Google.validate(auth_token)
        try:
            user_data['sub']
        except:
            raise serializers.ValidationError(
                'The token is invalid or expired. Please login again.'
            )

        valid_client_ids = getattr(settings, 'GOOGLE_CLIENT_IDS', [])

        aud = user_data.get('aud')

        if aud not in valid_client_ids:
            raise AuthenticationFailed(f"Invalid client ID (audience): {aud}")

        user_id = user_data['sub']
        email = user_data['email']
        name = user_data['name']
        provider = 'google'

        return register_social_user(
            provider=provider, user_id=user_id, email=email, name=name)


class TwitterAuthSerializer(serializers.Serializer):
    """Handles serialization of twitter related data"""
    access_token_key = serializers.CharField()
    access_token_secret = serializers.CharField()

    def validate(self, attrs):

        access_token_key = attrs.get('access_token_key')
        access_token_secret = attrs.get('access_token_secret')

        user_info = twitterhelper.TwitterAuthTokenVerification.validate_twitter_auth_tokens(
            access_token_key, access_token_secret)

        try:
            user_id = user_info['id_str']
            email = user_info['email']
            name = user_info['name']
            provider = 'twitter'
        except:
            raise serializers.ValidationError(
                'The tokens are invalid or expired. Please login again.'
            )

        return register_social_user(
            provider=provider, user_id=user_id, email=email, name=name)