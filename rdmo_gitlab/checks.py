from django.conf import settings
from django.core.checks import Error, register

@register()
def check_gitlab_provider_settings(app_configs, **kwargs):
    errors = []

    if getattr(settings, 'GITLAB_PROVIDER', None):
        pass
    elif getattr(settings, 'GITLAB_PROVIDERS', None):
        pass
    else:
        errors.append(
            Error(
                'Neither settings.GITLAB_PROVIDER not settings.GITLAB_PROVIDERS exists.', 
                hint='Add GITLAB_PROVIDER for one or GITLAB_PROVIDERS for multiple providers ' \
                    'to config/settings/local.py')
        )

    if getattr(settings, 'GITLAB_PROVIDER', None):
        provider = settings.GITLAB_PROVIDER
        for key in ['client_id', 'client_secret', 'gitlab_url']:
            if not provider.get(key):
                errors.append(Error(f'Key "{key}" is missing from settings.GITLAB_PROVIDER')
        )
    
    if getattr(settings, 'GITLAB_PROVIDERS', None):
        providers = settings.GITLAB_PROVIDERS
        for provider_name, provider_values in providers.items():
            for key in ['client_id', 'client_secret', 'gitlab_url']:
                if not provider_values.get(key):
                    errors.append(
                        Error(f'Key "{key}" is missing from provider "{provider_name}" in settings.GITLAB_PROVIDERS')
                    )

    return errors