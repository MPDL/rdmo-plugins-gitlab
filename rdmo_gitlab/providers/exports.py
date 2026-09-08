import base64
import logging
from urllib.parse import quote

from django.shortcuts import redirect, render
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from rdmo_maus.exports.smp_exports import SMPExportMixin
from rdmo_maus.forms.validators import FilePathExtensionValidator, validate_file_path

from rdmo.core.plugins import get_plugin
from rdmo.projects.exports import Export

from ..forms.forms import GitLabExportForm
from ..mixins import GitLabProviderMixin

logger = logging.getLogger(__name__)


class GitLabExportProvider(GitLabProviderMixin, Export, SMPExportMixin):
    @property
    def export_choices(self):

        catalog = self.project.catalog.uri_path
        catalog = catalog.lower() if isinstance(catalog, str) else 'project_export'

        export_choices = {  # check MultivalueCheckboxMultipleChoiceField in rdmo_maus.forms.fields.py for details
            'choices': [
                (f'True,data/{catalog}.xml', ('RDMO XML', _('File path')), 'xml'),
                (f'True,data/{catalog}_comma_separated.csv', (_('CSV (comma separated)'), _('File path')), 'csvcomma'),
                (
                    f'True,data/{catalog}_semicolon_separated.csv',
                    (_('CSV (semicolon separated)'), _('File path')),
                    'csvsemicolon',
                ),
                (f'True,data/{catalog}.json', ('JSON', _('File path')), 'json'),
            ],
            'choice_validators': {
                'xml': {'text': [validate_file_path, FilePathExtensionValidator('.xml')]},
                'csvcomma': {'text': [validate_file_path, FilePathExtensionValidator('.csv')]},
                'csvsemicolon': {'text': [validate_file_path, FilePathExtensionValidator('.csv')]},
                'json': {'text': [validate_file_path, FilePathExtensionValidator('.json')]},
            },
            'choice_attributes': {
                'xml': {
                    'text': {
                        'placeholder': _('example_folder/example_xml_file.xml'),
                    }
                },
                'csvcomma': {
                    'text': {
                        'placeholder': _('example_folder/example_csv_file.csv'),
                    }
                },
                'csvsemicolon': {
                    'text': {
                        'placeholder': _('example_folder/example_csv_file.csv'),
                    }
                },
                'json': {
                    'text': {
                        'placeholder': _('example_folder/example_json_file.json'),
                    }
                },
            },
        }

        smp_export_choices = getattr(self, 'smp_export_choices', None)
        if smp_export_choices:
            smp_export_choices.get('choices', []).extend(export_choices.get('choices', []))
            export_choices['choices'] = smp_export_choices.get('choices', [])
            export_choices['choice_validators'].update(smp_export_choices.get('choice_validators', {}))
            export_choices['choice_attributes'].update(smp_export_choices.get('choice_attributes', {}))

        return export_choices

    def render(self):
        self.pop_from_session(self.request, 'gitlab_export_choice_warnings')
        redirect_url = self.request.build_absolute_uri()

        provider = self.get_from_session(self.request, 'gitlab_provider')
        if provider is None:
            self.store_in_session(self.request, 'redirect_url', redirect_url)
            return self.select_provider(self.request)

        access_token = self.validate_access_token(self.request, self.get_from_session(self.request, 'access_token'))
        if access_token is None:
            self.store_in_session(self.request, 'redirect_url', redirect_url)
            return self.authorize(self.request)

        repo_choices, repo_help_text = self.get_repo_form_field_data(
            self.request, access_token, minimum_repo_access_level=30
        )  # 30 -> developer
        form_kwargs = {
            'repo_choices': repo_choices,
            'repo_help_text': repo_help_text,
            'export_choices': self.export_choices,
        }
        context = {'form': GitLabExportForm(**form_kwargs), 'source_title': self.get_gitlab_url(self.request)}
        return render(self.request, 'plugins/gitlab_export_form.html', context, status=200)

    def submit(self):
        if 'cancel' in self.request.POST:
            self.pop_from_session(self.request, 'gitlab_provider')

            if self.project is None:
                return redirect('projects')
            else:
                return redirect('project', self.project.id)

        method = self.request.POST.get('method')
        if method == 'set_provider':
            return getattr(self, method)(self.request)

        access_token = self.get_from_session(self.request, 'access_token')
        repo_choices, repo_help_text = self.get_repo_form_field_data(
            self.request, access_token, minimum_repo_access_level=30
        )  # 30 -> developer
        form_kwargs = {
            'repo_choices': repo_choices,
            'repo_help_text': repo_help_text,
            'export_choices': self.export_choices,
        }
        form = GitLabExportForm(self.request.POST, **form_kwargs)
        if form.is_valid():
            # 1. Validate export choices: Check submitted file paths to warn user if repo files will be overwritten
            export_choice_warnings = self.get_from_session(self.request, 'gitlab_export_choice_warnings')
            new_repo = form.cleaned_data.get('new_repo')

            if not new_repo and export_choice_warnings is None:
                context, export_choice_warnings = self.validate_export_choices(form)

                if len(export_choice_warnings) > 0:
                    return render(self.request, 'plugins/gitlab_export_form.html', context, status=200)

            # 2. Create file content for selected choices and export them
            url, request_data = self.process_form_data(form.cleaned_data)

            if url is not None and request_data is not None:
                return self.post(self.request, url, json=request_data)
            else:
                self.pop_from_session(self.request, 'gitlab_provider')
                return render(
                    self.request,
                    'core/error.html',
                    {
                        'title': _('Something went wrong'),
                        'errors': [
                            _(
                                'Either the export choices could not be created or the repository content '
                                'would have been overwritten without a warning.'
                            )
                        ],
                    },
                    status=200,
                )

        context = {'form': form, 'source_title': self.get_gitlab_url(self.request)}
        return render(self.request, 'plugins/gitlab_export_form.html', context, status=200)

    def check_file_paths(self, exports, repo, branch):
        access_token = self.get_from_session(self.request, 'access_token')
        export_choice_warnings = {}
        choice_keys = []
        for export in exports:
            choice_key, file_path = export.split(',')
            choice_keys.append(choice_key)
            url = self.get_request_url(self.request, repo, path=file_path, ref=branch)

            response = self.get_file_metadata(access_token, url)
            if response is not None and response.status_code == 200:
                export_choice_warnings[choice_key] = [
                    gettext('A file with the same path exists in the selected repository and will be overwritten')
                ]

        return export_choice_warnings, choice_keys, exports, branch

    def validate_export_choices(self, form):
        export_choice_warnings, selected_choice_keys, checked_export_choices, checked_branch = self.check_file_paths(
            form.cleaned_data.get('exports'), form.cleaned_data.get('repo'), form.cleaned_data.get('branch')
        )
        self.store_in_session(self.request, 'gitlab_export_choice_warnings', export_choice_warnings)
        self.store_in_session(self.request, 'gitlab_checked_export_choices', checked_export_choices)
        self.store_in_session(self.request, 'gitlab_checked_branch', checked_branch)

        selected_choices = [c for c in self.export_choices.get('choices', []) if c[2] in selected_choice_keys]

        form.fields['exports'].choices = selected_choices
        form.fields['exports'].widget.choice_warnings = export_choice_warnings

        context = {'form': form}

        return context, export_choice_warnings

    def render_export(self, choice_key):
        smp_export_choice_keys = getattr(self, 'smp_export_choice_keys', None)
        if smp_export_choice_keys and choice_key in smp_export_choice_keys:
            response = self.render_smp_export(choice_key)
        else:
            export_plugin = get_plugin('PROJECT_EXPORTS', choice_key)
            export_plugin.project = self.project
            response = export_plugin.render()

        return response

    def render_export_content(self, choice_key):
        response = self.render_export(choice_key)
        try:
            binary = response.content
            base64_bytes_of_content = base64.b64encode(binary)
            base64_string_of_content = base64_bytes_of_content.decode('utf-8')
            choice_content = base64_string_of_content
        except AttributeError:
            logger.warning('GitLabExportProvider - No content created for %s', choice_key)
            choice_content = None

        return choice_content

    def process_form_data(self, form_data, update_without_warning=False):
        export_choice_warnings = self.pop_from_session(self.request, 'gitlab_export_choice_warnings')
        checked_export_choices = self.pop_from_session(self.request, 'gitlab_checked_export_choices')
        checked_branch = self.pop_from_session(self.request, 'gitlab_checked_branch')
        new_repo = form_data.get('new_repo')
        branch = 'main' if new_repo else form_data.get('branch')

        actions = []
        processed_exports = []

        exports = form_data.get('exports')
        for export in exports:
            choice_key, file_path = export.split(',')
            initial_file_path = (
                file_path
                if new_repo
                else next(
                    (exp.split(',')[1] for exp in checked_export_choices if exp.split(',')[0] == choice_key), file_path
                )
            )
            initial_branch = 'main' if new_repo else checked_branch

            choice_in_repo = export_choice_warnings and choice_key in export_choice_warnings
            if file_path != initial_file_path or branch != initial_branch:
                new_export_choice_warnings, _new_choice_keys, _new_exports, _new_branch = self.check_file_paths(
                    [export], form_data.get('repo'), branch
                )
                if choice_key in new_export_choice_warnings and not update_without_warning:
                    processed_exports.append(
                        {
                            'key': choice_key,
                            'label': next(
                                (c[1][0] for c in self.export_choices.get('choices', []) if c[2] == choice_key),
                                choice_key,
                            ),
                            'success': False,
                            'processing_status': _(
                                'not exported - it would have overwritten existing file '
                                'in repository without a warning.'
                            ),
                        }
                    )
                    continue

                choice_in_repo = choice_key in new_export_choice_warnings

            content = self.render_export_content(choice_key)
            if content is None:
                success = False
                processing_status = _('not exported - it could not be created.')
            else:
                success = True
                processing_status = _('successfully exported.')

                action = 'update' if choice_in_repo else 'create'
                actions.append({'action': action, 'file_path': file_path, 'content': content, 'encoding': 'base64'})

            choice_label = next(
                (c[1][0] for c in self.export_choices.get('choices', []) if c[2] == choice_key), choice_key
            )
            processed_exports.append(
                {'key': choice_key, 'label': choice_label, 'success': success, 'processing_status': processing_status}
            )

        successfully_processed_exports = list(filter(lambda x: x['success'], processed_exports))
        if len(successfully_processed_exports) == 0:
            logger.warning(
                'GitLabExportProvider - No export content could be created for the selected choices: %s.', exports
            )
            return None, None

        self.store_in_session(self.request, 'gitlab_processed_exports', processed_exports)

        repo = 'repo_placeholder' if new_repo else form_data['repo']
        url = self.get_request_url(self.request, repo, suffix='/repository/commits')

        request_data = {'branch': branch, 'commit_message': form_data['commit_message'], 'actions': actions}

        if new_repo:
            request_data['url'] = url
            self.store_in_session(self.request, 'gitlab_export_data', request_data)

            url = f'{self.get_api_url(self.request)}/projects'
            new_repo_name = form_data['new_repo_name']
            request_data = {'name': new_repo_name, 'visibility': 'public'}

        return url, request_data

    def post_success(self, request, response):
        request_data = self.pop_from_session(request, 'gitlab_export_data')

        if isinstance(request_data, dict):
            repo = response.json().get('path_with_namespace')
            if repo:
                url = request_data.pop('url').replace('repo_placeholder', quote(repo, safe=''))
                return self.post(request, url, json=request_data)

        processed_exports = self.pop_from_session(request, 'gitlab_processed_exports')
        repo_html_url = response.json().get('web_url').split('-/commit')[0]

        successful_exports = list(filter(lambda x: x['success'], processed_exports))
        if len(successful_exports) == len(processed_exports):
            self.pop_from_session(request, 'gitlab_provider')
            return redirect(repo_html_url)

        context = {
            'repo_html_url': repo_html_url,
            'processed_exports': processed_exports,
            'source_title': self.get_gitlab_url(request),
        }

        self.pop_from_session(request, 'gitlab_provider')
        return render(request, 'plugins/gitlab_export_success.html', context, status=200)
