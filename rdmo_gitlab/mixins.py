import logging
from urllib.parse import quote, urlencode

from django import forms
from django.conf import settings
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.utils.translation import gettext_lazy as _

import requests

from rdmo.services.providers import OauthProviderMixin

logger = logging.getLogger(__name__)


class GitLabProviderMixin(OauthProviderMixin):
    def get_gitlab_url(self, request):
        if self.__class__.__name__ == 'GitLabIssueProvider':
            # no provider is stored in session for GitLabIssueProvider,
            # so use stored request info to get gitlab_url
            _method, url, *_args = self.get_from_session(request, 'request')
            # ['https:', '', {instance domain}, api, v4, projects, {user}%2F{repo_name}, issues]
            gitlab_url = '/'.join(url.split('/')[:3])
            return gitlab_url

        provider = self.get_from_session(request, 'gitlab_provider')
        return provider['gitlab_url'].strip('/')

    def get_authorize_url(self, request):
        return f'{self.get_gitlab_url(request)}/oauth/authorize'

    def get_token_url(self, request):
        return f'{self.get_gitlab_url(request)}/oauth/token'

    def get_api_url(self, request):
        return f'{self.get_gitlab_url(request)}/api/v4'

    def _get_provider(self, request):
        provider = None
        if getattr(settings, 'GITLAB_PROVIDER', None):
            provider = settings.GITLAB_PROVIDER
        elif getattr(settings, 'GITLAB_PROVIDERS', None):
            _method, url, *_args = self.get_from_session(request, 'request')
            # ['https:', '', {instance domain}, api, v4, projects, {user}%2F{repo_name}, issues]
            gitlab_url = '/'.join(url.split('/')[:3])

            providers = settings.GITLAB_PROVIDERS.values()
            provider = next(
                (provider for provider in providers if provider['gitlab_url'].strip('/') == gitlab_url), None
            )

        return provider

    def get_client_id(self, request):
        provider = self.get_from_session(request, 'gitlab_provider')

        if self.__class__.__name__ == 'GitLabIssueProvider':
            provider = self._get_provider(request)

        return provider['client_id']

    def get_client_secret(self, request):
        provider = self.get_from_session(request, 'gitlab_provider')

        if self.__class__.__name__ == 'GitLabIssueProvider':
            provider = self._get_provider(request)

        return provider['client_secret']

    @property
    def redirect_path(self):
        return reverse('oauth_callback', args=['gitlab'])

    class ProviderForm(forms.Form):
        provider = forms.ChoiceField(
            label=_('GitLab instance'), help_text=_('Select one of the supported instances'), widget=forms.RadioSelect
        )

        def __init__(self, *args, **kwargs):
            provider_choices = kwargs.pop('provider_choices')
            super().__init__(*args, **kwargs)

            self.fields['provider'].choices = provider_choices

    def get_authorize_params(self, request, state):
        return {
            'client_id': self.get_client_id(request),
            'redirect_uri': request.build_absolute_uri(self.redirect_path),
            'response_type': 'code',
            'scope': 'api',
            'state': state,
        }

    def get_callback_params(self, request):
        return {
            'token_url': self.get_token_url(request),
            'client_id': self.get_client_id(request),
            'client_secret': self.get_client_secret(request),
            'code': request.GET.get('code'),
            'grant_type': 'authorization_code',
            'redirect_uri': request.build_absolute_uri(self.redirect_path),
        }

    def get_refresh_token_params(self, request, refresh_token):
        return {
            'client_id': self.get_client_id(request),
            'client_secret': self.get_client_secret(request),
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'redirect_uri': request.build_absolute_uri(self.redirect_path),
        }

    def get_request_url(self, request, repo, path=None, suffix=None, ref=None):
        """Create the request url for export and import choices."""

        url = '{api_url}/projects/{repo}'.format(
            api_url=self.get_api_url(request),
            repo=quote(repo.replace(self.get_gitlab_url(request), '').strip('/'), safe=''),
        )

        if path:
            url += '/repository/files/{path}'.format(
                path=quote(path.removeprefix('../').removeprefix('./').strip('/'), safe='')
            )

        if suffix:
            url += suffix

        if ref:
            url += '?ref={ref}'.format(ref=quote(ref, safe=''))

        return url

    def get_file_metadata(self, access_token, url):
        if access_token:
            response = requests.head(url, headers=self.get_authorization_headers(access_token))
            try:
                response.raise_for_status()
                return response
            except requests.HTTPError:
                return None

        return None

    def select_provider(self, request):
        if getattr(settings, 'GITLAB_PROVIDER', None):
            provider = settings.GITLAB_PROVIDER
            self.store_in_session(request, 'gitlab_provider', provider)
            redirect_url = self.pop_from_session(request, 'redirect_url')
            if redirect_url is not None:
                return HttpResponseRedirect(redirect_url)

        elif getattr(settings, 'GITLAB_PROVIDERS', None):
            providers = settings.GITLAB_PROVIDERS.keys()
            provider_choices = [(p, p) for p in providers]
            context = {'form': self.ProviderForm(provider_choices=provider_choices), 'submit': _('Select provider')}
            return render(request, 'plugins/gitlab_provider_form.html', context, status=200)

        return render(
            request,
            'core/error.html',
            {'title': _('GitLab error'), 'errors': [_('No redirect could be found.')]},
            status=200,
        )

    def set_provider(self, request):
        provider_key = self.request.POST.get('provider')
        provider = settings.GITLAB_PROVIDERS.get(provider_key)
        self.store_in_session(request, 'gitlab_provider', provider)

        redirect_url = self.pop_from_session(request, 'redirect_url')
        if redirect_url is not None:
            return HttpResponseRedirect(redirect_url)

        return render(
            request,
            'core/error.html',
            {'title': _('GitLab error'), 'errors': [_('No redirect could be found.')]},
            status=200,
        )

    def authorize(self, request):
        # get random state and store in session
        state = get_random_string(length=32)
        self.store_in_session(request, 'state', state)

        url = self.get_authorize_url(request) + '?' + urlencode(self.get_authorize_params(request, state))
        return HttpResponseRedirect(url)

    def callback(self, request):
        if request.GET.get('state') != self.pop_from_session(request, 'state'):
            return render(
                request,
                'core/error.html',
                {'title': _('OAuth authorization not successful'), 'errors': [_('State parameter did not match.')]},
                status=200,
            )

        url = self.get_token_url(request) + '?' + urlencode(self.get_callback_params(request))

        response = requests.post(
            url,
            self.get_callback_data(request),
            auth=self.get_callback_auth(request),
            headers=self.get_callback_headers(request),
        )

        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            logger.error('callback error: %s (%s)', response.content, response.status_code)
            raise e

        response_data = response.json()

        # store access token in session
        self.store_in_session(request, 'access_token', response_data.get('access_token'))
        self.store_in_session(request, 'refresh_token', response_data.get('refresh_token'))

        redirect_url = self.pop_from_session(request, 'redirect_url')
        if redirect_url is not None:
            return HttpResponseRedirect(redirect_url)

        # get post data from session
        try:
            method, *args = self.pop_from_session(request, 'request')
            if method == 'get':
                return self.get(request, *args)
            elif method == 'post':
                return self.post(request, *args)
        except ValueError:
            pass

        return render(
            request,
            'core/error.html',
            {'title': _('OAuth authorization successful'), 'errors': [_('But no redirect could be found.')]},
            status=200,
        )

    def validate_access_token(self, request, access_token):
        # https://docs.gitlab.com/api/oauth2/

        if access_token is None:
            return None

        url = f'{self.get_gitlab_url(request)}/oauth/token/info'
        response = requests.get(url, headers=self.get_authorization_headers(access_token))

        try:
            response.raise_for_status()
        except requests.HTTPError:
            access_token = self.refresh_access_token(request)
            return access_token

        expires_in = response.json().get('expires_in', None)
        if expires_in and expires_in < 900:  # 15 min
            access_token = self.refresh_access_token(request)

        return access_token

    def refresh_access_token(self, request):
        "Update access token with refresh_token if it exists"

        refresh_token = self.pop_from_session(request, 'refresh_token')
        if refresh_token is None:
            return None

        url = self.get_token_url(request) + '?' + urlencode(self.get_refresh_token_params(request, refresh_token))
        response = requests.post(url)

        try:
            response.raise_for_status()
        except requests.HTTPError:
            logger.error('GitLab refresh token error: %s (%s)', response.content, response.status_code)
            return

        response_data = response.json()

        # store new access token in session
        access_token = response_data.get('access_token')
        self.store_in_session(request, 'access_token', access_token)
        self.store_in_session(request, 'refresh_token', response_data.get('refresh_token'))

        return access_token

    def get_repo_choices(self, request, access_token, minimum_repo_access_level, per_page=10):
        if access_token is None:
            return []

        url = '{api_url}/projects?min_access_level={mal}&active={a}&per_page={pp}&order_by={ob}'.format(
            api_url=self.get_api_url(request), mal=minimum_repo_access_level, a=True, pp=per_page, ob='updated_at'
        )

        response = requests.get(url, headers=self.get_authorization_headers(access_token=access_token))
        try:
            response.raise_for_status()
        except requests.HTTPError:
            logger.error('Error requesting GitLab repo list: %s (%s)', response.content, response.status_code)
            return []

        repos = [r.get('web_url') for r in response.json()]
        repo_choices = [(r, r) for r in repos]

        return repo_choices

    def get_repo_form_field_data(self, request, access_token, minimum_repo_access_level):
        repo_choices = self.get_repo_choices(request, access_token, minimum_repo_access_level)

        if len(repo_choices) == 0:
            repo_help_text = _('You do not have any GitLab repositories yet.')

        else:
            repo_help_text = _('These are your most recently updated, accessible GitLab repositories.')

        return repo_choices, repo_help_text
