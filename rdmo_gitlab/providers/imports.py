import base64
import logging

from django.shortcuts import redirect, render
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

import requests
from rdmo_maus.imports.mixins import SMPRepoImportMixin

from rdmo.core.imports import handle_fetched_file

from ..forms.forms import GitLabImportForm
from ..mixins import GitLabProviderMixin

logger = logging.getLogger(__name__)

class GitLabImportProvider(GitLabProviderMixin, SMPRepoImportMixin):
    @property
    def import_choices(self):
        smp_import_choices = getattr(self, 'smp_import_choices', None)
        if smp_import_choices:
            choices = smp_import_choices.get('choices', [])
            choices = [c for c in choices if c[2] != 'sbom'] # sbom only enabled in paying plan
            smp_import_choices['choices'] = choices

            return smp_import_choices

        return {}

    def render(self):
        self.pop_from_session(self.request, 'gitlab_import_choice_warnings')
        redirect_url = self.request.build_absolute_uri()

        provider = self.get_from_session(self.request, 'gitlab_provider')
        if provider is None:
            self.store_in_session(self.request, 'redirect_url', redirect_url)
            return self.select_provider(self.request)

        access_token = self.validate_access_token(self.request, self.get_from_session(self.request, 'access_token'))
        if access_token is None:
            self.store_in_session(self.request, 'redirect_url', redirect_url)
            return self.authorize(self.request)

        form_kwargs = {'source_title': self.get_gitlab_url(self.request)}
        if len(self.import_choices) > 0:
            form_kwargs['import_choices'] = self.import_choices

        context = {
            'source_title': self.get_gitlab_url(self.request),
            'form': self.get_form(
                self.request,
                GitLabImportForm,
                **form_kwargs
            )
        }
        return render(self.request, 'plugins/gitlab_import_form.html', context, status=200)

    def submit(self):
        if 'cancel' in self.request.POST:
            self.pop_from_session(self.request, 'gitlab_provider')
            self.pop_from_session(self.request, 'gitlab_more_repos_available')
            self.pop_from_session(self.request, 'gitlab_repo_choices')
            self.pop_from_session(self.request, 'gitlab_repos_page')

            if self.current_project is None:
                return redirect('projects')
            else:
                return redirect('project', self.current_project.id)

        method = self.request.POST.get('method')
        if method == 'import_repo_subset':
            return getattr(self, method)()
        elif method == 'set_provider':
            return getattr(self, method)(self.request)

        self.store_in_session(self.request, 'gitlab_more_repos_available', False)

        return self.process_form_submission(self.request)

    def get_success(self, request, response):
        self.pop_from_session(request, 'gitlab_more_repos_available')
        self.pop_from_session(request, 'gitlab_repo_choices')
        self.pop_from_session(request, 'gitlab_repos_page')

        # XML file import
        # Only an xml file was imported and no processing is needed
        if len(self.import_choices) == 0:
            file_content = response.json().get('content')
            request.session['import_file_name'] = handle_fetched_file(base64.b64decode(file_content))

            if self.current_project:
                return redirect('project_update_import', self.current_project.id)
            else:
                return redirect('project_create_import')

        # Multiple-source imports
        # user can select multiple import sources, so processing is needed
        request_urls = self.pop_from_session(self.request, 'request_urls')
        import_choice_warnings = self.pop_from_session(self.request, 'gitlab_import_choice_warnings')

        failed_import_choices = []
        for c in self.import_choices.get('choices', []):
            if isinstance(import_choice_warnings, dict) and c[2] in import_choice_warnings:
                choice_label = c[1][0] if isinstance(c[1], tuple) else c[1]
                failed_import_choices.append(choice_label)

        authorization_headers = self.get_authorization_headers(self.get_from_session(request, 'access_token'))
        process_import = getattr(self, 'process_import', None)

        if callable(process_import):
            default_project_title = response.json().get('name') if 'name' in response.json() else _('GitLab Import')
            kwargs = {
                'request': request,
                'headers': authorization_headers,
                'request_urls': request_urls,
                'import_choice_warnings': import_choice_warnings,
                'default_project_title': default_project_title,
                'import_success_template': 'plugins/gitlab_import_success.html',
                'import_success_context': {
                    'source_title': self.get_gitlab_url(request),
                    'failed_import_choices': failed_import_choices
                }
            }

            self.pop_from_session(request, 'gitlab_provider')
            return process_import(**kwargs)

        self.pop_from_session(request, 'gitlab_provider')
        return render(request, 'core/error.html', {
            'title': _('Import error'),
            'errors': [_("Something went wrong.")]
        }, status=200)

    def import_repo_subset(self):
        if self.current_project:
            return redirect('project_update_import', self.current_project.id)
        else:
            return redirect('project_create_import')

    def process_form_submission(self, request):
        form_kwargs = {'source_title': self.get_gitlab_url(request)}
        if len(self.import_choices) > 0:
            form_kwargs['import_choices'] = self.import_choices

        form = self.get_form(self.request, GitLabImportForm, self.request.POST, **form_kwargs)
        if form.is_valid():
            self.request.session['import_source_title'] = self.source_title = 'GitLab'

            # XML file import
            # Only an xml file was imported and no processing is needed
            if len(self.import_choices) == 0:
                xml_url = self.process_form_data(request, form.cleaned_data, xml_url_only=True)
                return self.get(self.request, xml_url)

            # Multiple-source imports
            # user can select multiple import sources, so processing is needed

            #   1. Validate import choices: Check submitted file paths to warn user if repo files don't exist
            import_choice_warnings = self.get_from_session(self.request, 'gitlab_import_choice_warnings')

            if import_choice_warnings is None:
                context, import_choice_warnings = self.validate_import_choices(form.cleaned_data)

                if len(import_choice_warnings) > 0:
                    return render(self.request, 'plugins/gitlab_import_form.html', context, status=200)

            #   2. Import selected choices
            urls, import_choice_warnings = self.process_form_data(self.request, form.cleaned_data)

            repo_url = urls.pop('repo')

            if len(urls) == 0:
                return render(self.request, 'core/error.html', {
                    'title': _('Import error'),
                    'errors': [_('Either none of the import choices exist or they could not be requested.')]
                }, status=200)
            else:
                self.store_in_session(self.request, 'request_urls', urls)

            if len(import_choice_warnings) > 0:
                self.store_in_session(self.request, 'gitlab_import_choice_warnings', import_choice_warnings)

            return self.get(self.request, repo_url)

        context = {
            'source_title': self.get_gitlab_url(self.request),
            'form': form
        }

        return render(self.request, 'plugins/gitlab_import_form.html', context, status=200)

    def check_urls(self, form_data):
        other_repo_check  = form_data.get('other_repo_check')
        repo = form_data.get('other_repo') if other_repo_check else form_data.get('repo')

        imports = {}
        for _import in form_data.get('imports', []):
            i_list = _import.split(',')
            key = i_list[0]
            value = i_list[1] if len(i_list) > 1 else None
            imports[key] = value

        urls = {
            'repo': self.get_request_url(self.request, repo),
            'languages': self.get_request_url(self.request, repo, suffix='/languages'), # only in default branch
            'xml': (
                self.get_request_url(self.request, repo, path=imports.get('xml'), ref=form_data.get('ref'))
                if 'xml' in imports
                else None
            ),
            'citation': (
                self.get_request_url(self.request, repo, path=imports.get('citation'), ref=form_data.get('ref'))
                if 'citation' in imports
                else None
            ),
            'codemeta': (
                self.get_request_url(self.request, repo, path=imports.get('codemeta'), ref=form_data.get('ref'))
                if 'codemeta' in imports
                else None
            ),
            'license': self.get_request_url(self.request, repo, suffix='?license=yes') # only in default branch
        }
        selected_urls = {k:urls.get(k) for k in ['repo', *imports.keys()]}

        access_token = self.get_from_session(self.request, 'access_token')
        import_choice_warnings = {}
        choice_keys = []
        for choice_key, url in selected_urls.items():
            choice_keys.append(choice_key)
            if imports.get(choice_key): # i.e. if form value has a file path
                response = self.get_file_metadata(self.request, url)
            else:
                response = requests.get(url, headers=self.get_authorization_headers(access_token))

            if response is None or response.status_code >= 400:
                warning = (
                    gettext('Either there is no file with this path in the selected repository '
                            'or it cannot be requested')
                    if imports.get(choice_key) # i.e. if form value has a file path
                    else gettext('Repository endpoint cannot be requested')
                )
                import_choice_warnings[choice_key] = [warning]

        return import_choice_warnings, choice_keys, selected_urls

    def validate_import_choices(self, form_data):
        import_choice_warnings, selected_choice_keys, _checked_import_urls = self.check_urls(form_data)

        self.store_in_session(self.request, 'gitlab_import_choice_warnings', import_choice_warnings)

        selected_choices = [
            c for c in self.import_choices.get('choices', [])
            if c[2] in selected_choice_keys
        ]

        form_kwargs = {'source_title': self.get_gitlab_url(self.request)}
        if len(self.import_choices) > 0:
            form_kwargs['import_choices'] = {
                **self.import_choices,
                'choices': selected_choices,
                'choice_warnings': import_choice_warnings
            }

        form = self.get_form(
            self.request,
            GitLabImportForm,
            self.request.POST,
            **form_kwargs
        )

        context = {
            'source_title': self.get_gitlab_url(self.request),
            'form': form
        }
        return context, import_choice_warnings

    def process_form_data(self, request, form_data, xml_url_only=False):
        # XML file import
        # Only an xml file was imported and no processing is needed
        if xml_url_only:
            other_repo_check  = form_data.get('other_repo_check')
            repo = form_data.get('other_repo') if other_repo_check else form_data.get('repo')
            xml_url = self.get_request_url(request, repo, path=form_data.get('imports'), ref=form_data.get('ref'))

            return xml_url

        # Multiple-source imports
        # user can select multiple import sources, so processing is needed
        self.pop_from_session(self.request, 'gitlab_import_choice_warnings')

        new_choice_warnings, _new_choice_keys, new_urls = self.check_urls(form_data)

        selected_urls = {}
        for choice_key, url in new_urls.items():
            if choice_key not in new_choice_warnings:
                selected_urls[choice_key] = url

        return selected_urls, new_choice_warnings

    def get_license(self, url, headers):
        license_id = None

        response = requests.get(url, headers=headers)
        try:
            response.raise_for_status()
            license_dict = response.json().get('license')
            if license_dict:
                license_id = license_dict.get('key', '').upper()
        except requests.HTTPError:
            pass

        return license_id

    def get_languages(self, url, headers):
        languages = []

        response = requests.get(url, headers=headers)
        try:
            response.raise_for_status()
            languages = response.json().keys()
        except requests.HTTPError:
            pass

        return languages

    def _get_file(self, url, headers):
        content = None

        response = requests.get(url, headers=headers)
        try:
            response.raise_for_status()
            encoded_content = response.json().get('content')
            decoded_bytes = base64.b64decode(encoded_content)
            content = decoded_bytes.decode('utf-8')
        except requests.HTTPError:
            pass

        return content

    def get_citation(self, url, headers):
        return self._get_file(url, headers)

    def get_codemeta(self, url, headers):
        return self._get_file(url, headers)
