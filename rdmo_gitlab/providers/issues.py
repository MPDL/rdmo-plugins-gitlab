import json
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpResponse
from django.utils.translation import gettext_lazy as _

from rdmo.projects.providers import OauthIssueProvider

from ..mixins import GitLabProviderMixin


class GitLabIssueProvider(GitLabProviderMixin, OauthIssueProvider):
    add_label = _('Add GitLab integration')
    send_label = _('Send to GitLab')
    description = _(
        'This integration allows the creation of issues in arbitrary GitLab repositories. '
        'The upload of attachments is not supported by GitLab.'
    )

    def get_post_url(self, request, issue, integration, subject, message, attachments):
        repo_url = integration.get_option_value('repo_url')
        if repo_url:
            gitlab_url = '/'.join(repo_url.split('/')[:3])  # ['https:', '', {instance domain}, {user}, {repo name}]
            repo = quote(repo_url.replace(gitlab_url, '').strip('/'), safe='')

            if getattr(settings, 'GITLAB_PROVIDER', None):
                provider = (
                    settings.GITLAB_PROVIDER
                    if settings.GITLAB_PROVIDER.get('gitlab_url').strip('/') == gitlab_url
                    else None
                )
            elif getattr(settings, 'GITLAB_PROVIDERS', None):
                providers = settings.GITLAB_PROVIDERS.values()
                provider = next(
                    (provider for provider in providers if provider.get('gitlab_url').strip('/') == gitlab_url), None
                )
            else:
                provider = None

            if provider is not None:
                repo = quote(repo_url.replace(gitlab_url, '').strip('/'), safe='')
                return f'{gitlab_url}/api/v4/projects/{repo}/issues'

    def get_post_data(self, request, issue, integration, subject, message, attachments):
        return {'title': subject, 'description': message}

    def get_issue_url(self, response):
        return response.json().get('web_url')

    def webhook(self, request, integration):
        secret = integration.get_option_value('secret')
        header_token = request.headers.get('X-Gitlab-Token')

        if (secret is not None) and (header_token is not None) and (header_token == secret):
            try:
                payload = json.loads(request.body.decode())
                state = payload.get('object_attributes', {}).get('state')
                issue_url = payload.get('object_attributes', {}).get('url')

                if state and issue_url:
                    try:
                        issue_resource = integration.resources.get(url=issue_url)
                        if state == 'closed':
                            issue_resource.issue.status = issue_resource.issue.ISSUE_STATUS_CLOSED
                        else:
                            issue_resource.issue.status = issue_resource.issue.ISSUE_STATUS_IN_PROGRESS

                        issue_resource.issue.save()
                    except ObjectDoesNotExist:
                        pass

                return HttpResponse(status=200)

            except json.decoder.JSONDecodeError as e:
                return HttpResponse(e, status=400)

        raise Http404

    @property
    def fields(self):
        return [
            {
                'key': 'repo_url',
                'placeholder': 'https://gitlab.example.org/group/project',
                'help': _('The URL of the GitLab repository to send issues to.'),
            },
            {
                'key': 'secret',
                'placeholder': 'Secret (random) string',
                'help': _('The secret for a GitLab webhook to close a task (optional).'),
                'required': False,
                'secret': True,
            },
        ]

    def get_gitlab_url(self, request):
        # no provider is stored in session for GitLabIssueProvider,
        # so use stored request info to get gitlab_url
        _method, url, *_args = self.get_from_session(request, 'request')
        # ['https:', '', {instance domain}, api, v4, projects, {user}%2F{repo_name}, issues]
        gitlab_url = '/'.join(url.split('/')[:3])
        if getattr(settings, 'GITLAB_PROVIDER', None):
            provider = (
                settings.GITLAB_PROVIDER
                if settings.GITLAB_PROVIDER.get('gitlab_url').strip('/') == gitlab_url
                else None
            )
        elif getattr(settings, 'GITLAB_PROVIDERS', None):
            providers = settings.GITLAB_PROVIDERS.values()
            provider = next(
                (provider for provider in providers if provider.get('gitlab_url').strip('/') == gitlab_url), None
            )
        else:
            provider = None

        if provider is not None:
            return gitlab_url

    def _get_provider(self, request):
        provider = None

        _method, url, *_args = self.get_from_session(request, 'request')
        # ['https:', '', {instance domain}, api, v4, projects, {user}%2F{repo_name}, issues]
        gitlab_url = '/'.join(url.split('/')[:3])
        if getattr(settings, 'GITLAB_PROVIDER', None):
            provider = (
                settings.GITLAB_PROVIDER
                if settings.GITLAB_PROVIDER.get('gitlab_url').strip('/') == gitlab_url
                else None
            )
        elif getattr(settings, 'GITLAB_PROVIDERS', None):
            providers = settings.GITLAB_PROVIDERS.values()
            provider = next(
                (provider for provider in providers if provider['gitlab_url'].strip('/') == gitlab_url), None
            )

        return provider

    def get_client_id(self, request):
        provider = self._get_provider(request)

        return provider['client_id']

    def get_client_secret(self, request):
        provider = self._get_provider(request)

        return provider['client_secret']
