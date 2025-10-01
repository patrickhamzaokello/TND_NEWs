from django.core.cache import cache
from rest_framework import serializers
from .models import User
from django.contrib import auth
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from django.contrib.auth.tokens import  default_token_generator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(max_length=68, min_length=6, write_only=True)
    user_id = serializers.UUIDField(source='id', read_only=True)  # Map DB id → user_id
    username = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ['email', 'name', 'password', 'user_id', 'username']
        read_only_fields = ['user_id', 'username']

    def validate(self, attrs):
        email = attrs.get('email', '').lower()
        name = attrs.get('name', '')

        if not name:
            raise serializers.ValidationError({'name': 'Name is required'})
        if not email:
            raise serializers.ValidationError({'email': 'Email is required'})
        return attrs

    def create(self, validated_data):
        # Only pass required fields to create_user
        return User.objects.create_user(
            name=validated_data['name'],
            email=validated_data['email'],
            password=validated_data['password']
        )

class EmailVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(min_length=6, max_length=6)

    class Meta:
        fields = ['email', 'code']

    def validate_code(self, value):
        # Ensure code is exactly 6 digits
        if not value.isdigit():
            raise serializers.ValidationError("Code must contain only digits")
        if len(value) != 6:
            raise serializers.ValidationError("Code must be exactly 6 digits")
        return value

    def validate_email(self, value):
        return value.lower()


class ResendVerificationCodeSerializer(serializers.Serializer):
    email = serializers.EmailField()

    class Meta:
        fields = ['email']

    def validate_email(self, value):
        return value.lower()


class LoginSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(max_length=255, min_length=3)
    password = serializers.CharField(max_length=68, min_length=6, write_only=True)
    username = serializers.CharField(read_only=True)
    user_id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    tokens = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['email', 'name', 'password', 'username', 'user_id', 'tokens']
        read_only_fields = ['name', 'username', 'user_id', 'tokens']

    def get_tokens(self, obj):
        # Use the user from context
        user = self.context.get('user')
        if not user:
            raise serializers.ValidationError("User not found in context")
        return user.tokens()

    def validate(self, attrs):
        email = attrs.get('email', '').lower()
        password = attrs.get('password', '')

        # Check auth provider
        filtered_user_by_email = User.objects.filter(email=email)
        if filtered_user_by_email.exists() and filtered_user_by_email[0].auth_provider != 'email':
            raise AuthenticationFailed(
                f"Please continue your login using {filtered_user_by_email[0].auth_provider}"
            )

        # Authenticate
        user = auth.authenticate(email=email, password=password)
        if not user:
            raise AuthenticationFailed('Invalid credentials, try again')
        if not user.is_active:
            raise AuthenticationFailed('Account disabled, contact admin')
        if not user.is_verified:
            raise AuthenticationFailed('Email is not verified')

        # Store user in context for get_tokens
        self.context['user'] = user

        # Return validated input attributes
        return attrs

    def to_representation(self, instance):
        # instance is the validated attrs (email, password), but we use self.context['user']
        user = self.context.get('user')
        if not user:
            raise serializers.ValidationError("User not found in context")
        return {
            'email': user.email,
            'name': user.name,
            'username': user.username,
            'user_id': user.id,
            'tokens': self.get_tokens(user)
        }

class ResetPasswordEmailRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(min_length=2)

    class Meta:
        fields = ['email']

    def validate_email(self, value):
        # Basic email validation is already done by EmailField
        return value.lower()

class VerifyResetCodeSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(min_length=6, max_length=6)

    class Meta:
        fields = ['email', 'code']

    def validate_code(self, value):
        # Ensure code is exactly 6 digits
        if not value.isdigit():
            raise serializers.ValidationError("Code must contain only digits")
        if len(value) != 6:
            raise serializers.ValidationError("Code must be exactly 6 digits")
        return value

    def validate_email(self, value):
        return value.lower()


class SetNewPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(
        min_length=6, max_length=68, write_only=True)
    token = serializers.CharField(
        min_length=1, write_only=True)
    uidb64 = serializers.CharField(
        min_length=1, write_only=True)

    class Meta:
        fields = ['password', 'token', 'uidb64']

    def validate_password(self, value):
        # Add password strength validation if needed
        if len(value) < 6:
            raise serializers.ValidationError("Password must be at least 6 characters long")
        return value

    def validate(self, attrs):
        try:
            password = attrs.get('password')
            token = attrs.get('token')
            uidb64 = attrs.get('uidb64')

            # Decode user ID
            id = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=id)

            # Verify token using Django's default token generator
            if not default_token_generator.check_token(user, token):
                raise AuthenticationFailed('The reset token is invalid or expired', 401)

            # Check if reset session is valid
            reset_session_key = f"reset_session_{user.pk}"
            session_data = cache.get(reset_session_key)

            if not session_data or not session_data.get('verified'):
                raise AuthenticationFailed('Reset session expired. Please verify your code again.', 401)

            # Set new password
            user.set_password(password)
            user.save()

            return attrs

        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            raise AuthenticationFailed('The reset link is invalid', 401)
        except Exception as e:
            raise AuthenticationFailed('Password reset failed', 401)

class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()

    def validate(self, value):
        try:
            token = RefreshToken(value)
            token.blacklist()
            return value
        except TokenError:
            raise serializers.ValidationError("Token is expired or invalid")

    def save(self, **kwargs):
        # Token already blacklisted during validation
        pass


# Optional: Create a separate user profile serializer for getting user details
class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'name', 'email', 'phone', 'bio', 'is_verified', 'created_at', 'updated_at']
        read_only_fields = ['id', 'email', 'is_verified', 'created_at', 'updated_at']