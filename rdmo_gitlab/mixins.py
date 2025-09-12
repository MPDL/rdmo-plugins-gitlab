import logging
import requests
from urllib.parse import urlencode, quote

from django.conf import settings
from django.urls import reverse
from django.shortcuts import render
from django.utils.crypto import get_random_string
from django.http import HttpResponseRedirect
from django.utils.translation import gettext_lazy as _
from django.utils.safestring import mark_safe

from rdmo.services.providers import OauthProviderMixin

logger = logging.getLogger(__name__)


class GitLabProviderMixin(OauthProviderMixin):

    @property
    def gitlab_url(self):
        return settings.GITLAB_PROVIDER['gitlab_url'].strip('/')

    @property
    def authorize_url(self):
        return f'{self.gitlab_url}/oauth/authorize'

    @property
    def token_url(self):
        return f'{self.gitlab_url}/oauth/token'

    @property
    def api_url(self):
        return f'{self.gitlab_url}/api/v4'

    @property
    def client_id(self):
        return settings.GITLAB_PROVIDER['client_id']

    @property
    def client_secret(self):
        return settings.GITLAB_PROVIDER['client_secret']

    @property
    def redirect_path(self):
        return reverse('oauth_callback', args=['gitlab'])

    def get_authorize_params(self, request, state):
        return {
            'client_id': self.client_id,
            'redirect_uri': request.build_absolute_uri(self.redirect_path),
            'response_type': 'code',
            'scope': 'api',
            'state': state
        }

    def get_callback_params(self, request):
        return {
            'token_url': self.token_url,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'code': request.GET.get('code'),
            'grant_type': 'authorization_code',
            'redirect_uri': request.build_absolute_uri(self.redirect_path)
        }
    
    def get_refresh_token_params(self, request, refresh_token):
        return {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'redirect_uri': request.build_absolute_uri(self.redirect_path),
        }
    
    def get_request_url(self, repo, path, ref=None):
        url = '{api_url}/projects/{repo}/repository/files/{path}'.format(
                api_url=self.api_url,
                repo=quote(repo.replace(self.gitlab_url, '').strip('/'), safe=''),
                path=quote(path.strip('../'), safe='')
            )
        
        if ref:
            url += '?ref={ref}'.format(ref=quote(ref, safe=''))

        return url
    
    def callback(self, request):
        if request.GET.get('state') != self.pop_from_session(request, 'state'):
            return render(request, 'core/error.html', {
                'title': _('GitLab callback error'),
                'errors': [_('State parameter did not match.')]
            }, status=200)

        url = self.token_url + '?' + urlencode(self.get_callback_params(request))

        response = requests.post(url, self.get_callback_data(request),
                                 auth=self.get_callback_auth(request),
                                 headers=self.get_callback_headers(request))

        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            logger.error('callback error: %s (%s)', response.content, response.status_code)
            raise e

        response_data = response.json()
        
        # store access token in session
        self.store_in_session(request, 'access_token', response_data.get('access_token'))
        self.store_in_session(request, 'refresh_token', response_data.get('refresh_token', None))
        
        redirect_url = self.pop_from_session(request, 'redirect_url')
        if redirect_url is not None:
            return HttpResponseRedirect(redirect_url)

        # get request data from session
        try:
            method, url, kwargs = self.pop_from_session(request, 'request')
            return self.make_request(request, method, url, **kwargs)
        except ValueError:
            pass
        
        return render(request, 'core/error.html', {
            'title': _('GitLab callback error'),
            'errors': [_('No redirect could be found.')]
        }, status=200)
    
    # https://docs.gitlab.com/api/oauth2/
    def validate_access_token(self, request, access_token):
        if access_token is None: return

        url = '{gitlab_url}/oauth/token/info'.format(
            gitlab_url=self.gitlab_url
        )
        response = requests.get(
            url,
            headers=self.get_authorization_headers(access_token)
        )

        try:
            response.raise_for_status()
        except:
            access_token = self.refresh_access_token(request)
            return access_token

        expires_in = response.json().get('expires_in', None)
        if expires_in and expires_in < 900: # 15 min
            access_token = self.refresh_access_token(request)

        return access_token
    
    def refresh_access_token(self, request):
        'Update access token with refresh_token if it exists'

        refresh_token = self.pop_from_session(request, 'refresh_token')
        if refresh_token is None: return

        url = self.token_url + '?' + urlencode(self.get_refresh_token_params(request, refresh_token))
        response = requests.post(url)
        
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            logger.error('GitLab refresh token error: %s (%s)', response.content, response.status_code)
            return 

        response_data = response.json()

        # store new access token in session
        access_token = response_data.get('access_token')
        self.store_in_session(request, 'access_token', access_token)
        self.store_in_session(request, 'refresh_token', response_data.get('refresh_token'))

        return access_token
    
    def get_repo_choices(self, access_token, minimum_repo_access_level):
        if access_token is None: return []

        url = '{api_url}/projects?min_access_level={min_access_level}&active={active}&per_page={per_page}&order_by={order_by}'.format(
                api_url=self.api_url,
                min_access_level=minimum_repo_access_level,
                active=True,
                per_page=10,
                order_by='updated_at'
            )
        
        response = requests.get(url, headers=self.get_authorization_headers(access_token=access_token))
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            # logger.error('error requesting gitlab app repo list: %s (%s)', response.content, response.status_code)
            logger.error('Error requesting GitLab repo list: %s (%s)', response.content, response.status_code)
            raise e

        repos = [r.get('web_url') for r in response.json()]

        repo_choices = [(r, r) for r in repos]        
        return repo_choices
    
    def get_repo_form_field_data(self, request, minimum_repo_access_level):
        access_token = self.validate_access_token(request, self.get_from_session(request, 'access_token'))
        repo_choices = self.get_repo_choices(access_token, minimum_repo_access_level)
        
        if access_token is None:
            state = get_random_string(length=32)
            self.store_in_session(request, 'state', state)

            url = self.authorize_url + '?' + urlencode(self.get_authorize_params(request, state))
            link_help_text = _('To connect to GitLab repositories, you first need to authorize the MPDL app.')
            
            repo_help_text = mark_safe(f'{link_help_text} <a href="{url}">{_("Authorize App")}</a>') if url is not None else ''

        else:    
            repo_help_text = _('''These are your most recently updated, accessible GitLab repositories (up to 10 will be shown here). 
                To add another repository to this list, please update the repository and reload this page.''')
        
        return repo_choices, repo_help_text

    
    def get_form(self, request, form, *args, **kwargs):
        repo_access_level_map = {
            'GitLabExportForm': 30, # developer
            'GitLabImportForm': 15  # planner
        }
        minimum_repo_access_level = repo_access_level_map[form.__name__]
        repo_choices, repo_help_text = self.get_repo_form_field_data(request, minimum_repo_access_level)
        return form(
                *args,
                **kwargs,
                repo_choices=repo_choices, 
                repo_help_text=repo_help_text
            )
