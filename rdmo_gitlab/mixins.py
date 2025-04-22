import requests
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.urls import reverse
from django.shortcuts import render
from django.http import HttpResponseRedirect
from django.utils.translation import gettext_lazy as _

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
    
    # https://docs.gitlab.com/api/oauth2/
    def validate_access_token(self, request, access_token):
        # print('validate_access_token()')
        # print(f'    access_token: {access_token}')
        if access_token is None: return

        url = '{gitlab_url}/oauth/token/info'.format(
            gitlab_url=self.gitlab_url
        )
        # print(f'    url: {url}')
        response = requests.get(
            url,
            headers=self.get_authorization_headers(access_token)
        )
        # print(f'    response: {response.json()}')

        try:
            response.raise_for_status()
        except:
            access_token = self.refresh_access_token(request)
            return access_token

        expires_in = response.json().get('expires_in', None)
        # print(f'    expires_in: {expires_in}')
        if expires_in and expires_in < 900: # 15 min
            # print(f'    access_token still valid, but expires in less than 15 min')
            access_token = self.refresh_access_token(request)

        return access_token
    
    def get_refresh_token_params(self, request, refresh_token):
        return {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'redirect_uri': request.build_absolute_uri(self.redirect_path),
        }
    
    def refresh_access_token(self, request):
        # print('refresh_access_token()')
        'Update access token with refresh_token if it exists'

        refresh_token = self.pop_from_session(request, 'refresh_token')
        # print(f'    refresh_token: {refresh_token}')
        if refresh_token is None: return

        url = self.token_url + '?' + urlencode(self.get_refresh_token_params(request, refresh_token))
        response = requests.post(url)
        
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            logger.error('refresh token error: %s (%s)', response.content, response.status_code)
            return 

        response_data = response.json()
        # print(f'    response: {response.json()}')
        # store new access token in session
        access_token = response_data.get('access_token')
        self.store_in_session(request, 'access_token', access_token)
        self.store_in_session(request, 'refresh_token', response_data.get('refresh_token'))

        return access_token
    
    def make_request(self, request, method, url, apply_data_processing=False, **kwargs):
        methods = ['get', 'post', 'put']
        if method in methods:
            return super().make_request(request, method, url, apply_data_processing, **kwargs)
        
        if method != 'head':
            raise ValueError(f"Unsupported method: {method}")

        access_token = self.get_from_session(request, 'access_token')
        if access_token:
            # if the access_token is available make request to the upstream service
            logger.debug('%s: %s', method, url)

            headers = self.get_authorization_headers(access_token)
            response = requests.head(url, headers=headers)

            if response.status_code == 401:
                logger.warning('%s forbidden: %s (%s)', method, response.content, response.status_code)
            elif response.status_code == 404:
                return response
            else:
                try:
                    response.raise_for_status()
                    return response

                except requests.HTTPError:
                    logger.warning('%s error: %s (%s)', method, response.content, response.status_code)

        return None
    
    def callback(self, request):
        if request.GET.get('state') != self.pop_from_session(request, 'state'):
            return render(request, 'core/error.html', {
                'title': _('OAuth authorization not successful'),
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
            'title': _('OAuth authorization successful'),
            'errors': [_('But no redirect could be found.')]
        }, status=200)
