# utils/apple.py
import jwt
import json
import requests
from django.conf import settings
from jwt.algorithms import RSAAlgorithm


class Apple:
    """Apple class to validate identity tokens and fetch user info"""

    @staticmethod
    def validate(auth_token):
        """Validate Apple Sign-in token with proper signature verification"""
        try:
            # Get Apple's public keys
            apple_jwt_url = "https://appleid.apple.com/auth/keys"
            response = requests.get(apple_jwt_url)
            response.raise_for_status()

            # Create a mapping of key IDs to key info
            apple_public_keys = {
                key_info["kid"]: key_info
                for key_info in response.json()["keys"]
            }

            # Get the key ID from the token header
            unverified_header = jwt.get_unverified_header(auth_token)
            key_id = unverified_header["kid"]

            # Find the matching public key
            if key_id not in apple_public_keys:
                raise ValueError("Public key not found")

            key = apple_public_keys[key_id]

            # Convert JWK to RSA public key
            apple_public_key = RSAAlgorithm.from_jwk(json.dumps(key))

            # Get the audience (client ID) from settings
            audience = getattr(settings, 'APPLE_CLIENT_ID', None)
            if not audience:
                raise ValueError("APPLE_CLIENT_ID not configured in settings")

            # Decode and verify the token with proper signature verification
            decoded_token = jwt.decode(
                auth_token,
                apple_public_key,
                audience=audience,
                algorithms=[key["alg"]],  # Use the algorithm from the key
                # All verification options are True by default
            )

            return decoded_token

        except requests.RequestException as e:
            raise ValueError(f"Failed to fetch Apple public keys: {str(e)}")
        except jwt.ExpiredSignatureError:
            raise ValueError("Token has expired")
        except jwt.InvalidAudienceError:
            raise ValueError("Invalid audience")
        except jwt.InvalidSignatureError:
            raise ValueError("Invalid token signature")
        except jwt.InvalidTokenError as e:
            raise ValueError(f"Invalid token: {str(e)}")
        except KeyError as e:
            raise ValueError(f"Missing required field in token: {str(e)}")
        except Exception as e:
            raise ValueError(f"Token validation failed: {str(e)}")