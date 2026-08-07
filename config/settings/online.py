"""
Serveur en ligne, déployé via Docker sur le VPS Hostinger (zweey.com).
"""
import os
from .base import *  # noqa: F401,F403
from .base import env
from pathlib import Path


DEBUG = env.bool("DJANGO_DEBUG", default=False)

# ALLOWED_HOSTS = env.list(
#     "DJANGO_ALLOWED_HOSTS",
#     default=["zweey.com", "www.zweey.com", "api.zweey.com"],
# )

# CSRF_TRUSTED_ORIGINS = env.list(
#     "DJANGO_CSRF_TRUSTED_ORIGINS",
#     default=["https://zweey.com", "https://www.zweey.com", "https://api.zweey.com"],
# )



ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["test.zweey.com/", "www.test.zweey.com/", "https://test.zweey.com/"],
)

CSRF_TRUSTED_ORIGINS = env.list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=["https://test.zweey.com/", "https://test.zweey.com/", ""],
)

# DATABASES = {
#     "default": env.db("DATABASE_URL", default="postgres://postgres:postgres@db:5432/postgres"),
# }
BASE_DIR = Path(__file__).resolve().parent.parent

# DATABASES = {
#     "default": {
#         "ENGINE": "django.db.backends.sqlite3",
#         "NAME": BASE_DIR / "db.sqlite3",
#     }
# }

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.getenv('DB_NAME', 'zweeydb'),
        'USER': os.getenv('DB_USER', 'zweeyuser'),
        'PASSWORD': os.getenv('DB_PASSWORD', 'Jeanpierre236'),
        'HOST': os.getenv('DB_HOST', 'stocketfacturation-zweeydatabase-pss441'),
        'PORT': os.getenv('DB_PORT', '3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *[m for m in MIDDLEWARE if m != "django.middleware.security.SecurityMiddleware"],  # noqa: F405
]

STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
