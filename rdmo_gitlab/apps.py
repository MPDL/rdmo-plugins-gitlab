from django.apps import AppConfig

class RDMOGitLabConfig(AppConfig):
    name = 'rdmo_gitlab'

    def ready(self):
        import rdmo_gitlab.checks  # noqa: F401
