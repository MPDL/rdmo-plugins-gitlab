import base64
import json
import logging
from urllib.parse import quote

from django import forms
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from rdmo_maus.exports.smp_exports import SMPExportMixin
from rdmo_maus.forms.validators import FilePathExtensionValidator, validate_file_path

from rdmo.core.plugins import get_plugin
from rdmo.projects.exports import Export
from rdmo.projects.providers import OauthIssueProvider

from ..forms.forms import GitLabExportForm
from ..mixins import GitLabProviderMixin

logger = logging.getLogger(__name__)

class GitLabExportProvider(GitLabProviderMixin, Export, SMPExportMixin):
    @property
    def export_choices(self):
        catalog = self.project.catalog.uri_path
        catalog = catalog.lower() if isinstance(catalog, str) else 'project_export'

        export_choices = { # check MultivalueCheckboxMultipleChoiceField in rdmo_maus.forms.fields.py for details
            'choices': [
                (f'False,data/{catalog}.xml', ('RDMO XML', _('File path')), 'xml'),
                (f'False,data/{catalog}_comma_separated.csv', (_('CSV (comma separated)'), _('File path')), 'csvcomma'),
                (
                    f'False,data/{catalog}_semicolon_separated.csv',
                    (_('CSV (semicolon separated)'), _('File path')),
                    'csvsemicolon'
                ),
                (f'False,data/{catalog}.json', ('JSON', _('File path')), 'json')
            ],
            'choice_validators': {
                'xml': {
                    'text': [validate_file_path, FilePathExtensionValidator('.xml')]
                },
                'csvcomma': {
                    'text': [validate_file_path, FilePathExtensionValidator('.csv')]
                },
                'csvsemicolon': {
                    'text': [validate_file_path, FilePathExtensionValidator('.csv')]
                },
                'json': {
                    'text': [validate_file_path, FilePathExtensionValidator('.json')]
                }
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
                }
            }
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

        context = {
            'form': self.get_form(
                self.request,
                GitLabExportForm,
                export_choices=self.export_choices
            ),
            'source_title': self.get_gitlab_url(self.request)
        }
        return render(self.request, 'plugins/gitlab_export_form.html', context, status=200)

    def submit(self):
        if 'cancel' in self.request.POST:
            self.pop_from_session(self.request, 'gitlab_provider')
            self.pop_from_session(self.request, 'gitlab_more_repos_available')
            self.pop_from_session(self.request, 'gitlab_repo_choices')
            self.pop_from_session(self.request, 'gitlab_repos_page')

            if self.project is None:
                return redirect('projects')
            else:
                return redirect('project', self.project.id)

        method = self.request.POST.get('method')
        if method == 'set_provider':
            return getattr(self, method)(self.request)

        self.store_in_session(self.request, 'gitlab_more_repos_available', False)

        form = self.get_form(self.request, GitLabExportForm, self.request.POST, export_choices=self.export_choices)
        if form.is_valid():

            # 1. Validate export choices: Check submitted file paths to warn user if repo files will be overwritten
            export_choice_warnings = self.get_from_session(self.request, 'gitlab_export_choice_warnings')
            new_repo = form.cleaned_data.get('new_repo')

            if not new_repo and export_choice_warnings is None:
                context, export_choice_warnings = self.validate_export_choices(form.cleaned_data)

                if len(export_choice_warnings) > 0:
                    return render(self.request, 'plugins/gitlab_export_form.html', context, status=200)

            # 2. Create file content for selected choices and export them
            url, request_data = self.process_form_data(form.cleaned_data)

            if url is not None and request_data is not None:
                return self.post(self.request, url, json=request_data)
            else:
                return render(self.request, 'core/error.html', {
                    'title': _('Something went wrong'),
                    'errors': [_('Either the export choices could not be created or the repository content '
                                 'would have been overwritten without a warning.')]
                }, status=200)

        new_repo = True if 'new_repo' in form.data else False
        context = {
            'form': form,
            'source_title': self.get_gitlab_url(self.request)
        }
        return render(self.request, 'plugins/gitlab_export_form.html', context, status=200)

    def check_file_paths(self, exports, repo, branch):
        export_choice_warnings = {}
        choice_keys = []
        for e in exports:
            choice_key, file_path = e.split(',')
            choice_keys.append(choice_key)
            url = self.get_request_url(self.request, repo, path=file_path, ref=branch)

            response = self.get_file_metadata(self.request, url)
            if response is not None and response.status_code == 200:
                export_choice_warnings[choice_key] = [
                    gettext('A file with the same path exists in the selected repository and will be overwritten')
                ]

        return export_choice_warnings, choice_keys, exports, branch

    def validate_export_choices(self, form_data):
        export_choice_warnings, selected_choice_keys, checked_export_choices, checked_branch = self.check_file_paths(
            form_data.get('exports'),
            form_data.get('repo'),
            form_data.get('branch')
        )
        self.store_in_session(self.request, 'gitlab_export_choice_warnings', export_choice_warnings)
        self.store_in_session(self.request, 'gitlab_checked_export_choices', checked_export_choices)
        self.store_in_session(self.request, 'gitlab_checked_branch', checked_branch)

        selected_choices = [c for c in self.export_choices.get('choices', []) if c[2] in selected_choice_keys]
        form = self.get_form(
            self.request,
            GitLabExportForm,
            self.request.POST,
            export_choices={
                **self.export_choices,
                'choices': selected_choices,
                'choice_warnings': export_choice_warnings
            }
        )
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
        for e in exports:
            choice_key, file_path = e.split(',')
            initial_file_path = file_path if new_repo else next(
                (exp.split(',')[1] for exp in checked_export_choices if exp.split(',')[0] == choice_key),
                file_path
            )
            initial_branch = 'main' if new_repo else checked_branch

            choice_in_repo = True if export_choice_warnings and choice_key in export_choice_warnings else False
            if file_path != initial_file_path or branch != initial_branch:
                new_export_choice_warnings, __, ___, ____ = self.check_file_paths([e], form_data['repo'], branch)
                if choice_key in new_export_choice_warnings and not update_without_warning:
                    processed_exports.append({
                        'key': choice_key,
                        'label': next(
                            (c[1][0] for c in self.export_choices.get('choices', []) if c[2] == choice_key),
                            choice_key
                        ),
                        'success': False,
                        'processing_status': _('not exported - it would have overwritten existing file '
                                               'in repository without a warning.')
                    })
                    continue

                choice_in_repo = True if choice_key in new_export_choice_warnings else False

            content = self.render_export_content(choice_key)
            if content is None:
                success = False
                processing_status = _('not exported - it could not be created.')
            else:
                success = True
                processing_status = _('successfully exported.')

                action = 'update' if choice_in_repo else 'create'
                actions.append({
                    'action': action,
                    'file_path': file_path,
                    'content': content,
                    'encoding': 'base64'
                })

            choice_label = next(
                (c[1][0] for c in self.export_choices.get('choices', []) if c[2] == choice_key),
                choice_key
            )
            processed_exports.append({
                'key': choice_key,
                'label': choice_label,
                'success': success,
                'processing_status': processing_status
            })

        successfully_processed_exports = list(filter(lambda x: x['success'], processed_exports))
        if len(successfully_processed_exports) == 0:
            logger.warning('GitLabExportProvider - No export content could be created for the '
                           'selected choices: %s.', exports)
            return None, None

        self.store_in_session(self.request, 'gitlab_processed_exports', processed_exports)

        repo = 'repo_placeholder' if new_repo else form_data['repo']
        url = self.get_request_url(self.request, repo, suffix='/repository/commits')

        request_data = {
            'branch': branch,
            'commit_message': form_data['commit_message'],
            'actions': actions
        }

        if new_repo:
            request_data['url'] = url
            self.store_in_session(self.request, 'gitlab_export_data', request_data)

            url = f'{self.get_api_url(self.request)}/projects'
            new_repo_name = form_data['new_repo_name']
            request_data = {'name': new_repo_name}

        return url, request_data

    def post_success(self, request, response):
        request_data = self.pop_from_session(self.request, 'gitlab_export_data')
        self.pop_from_session(request, 'gitlab_more_repos_available')
        self.pop_from_session(request, 'gitlab_repo_choices')
        self.pop_from_session(request, 'gitlab_repos_page')

        if isinstance(request_data, dict):
            repo = response.json().get('path_with_namespace')
            if repo:
                url = request_data.pop('url').replace('repo_placeholder', quote(repo, safe=''))
                return self.post(self.request, url, json=request_data)

        processed_exports = self.pop_from_session(request, 'gitlab_processed_exports')
        repo_html_url = response.json().get('web_url').split("-/commit")[0]

        successful_exports = list(filter(lambda x: x['success'], processed_exports))
        if len(successful_exports) == len(processed_exports):
            self.pop_from_session(self.request, 'gitlab_provider')
            return redirect(repo_html_url)

        context = {
            'repo_html_url': repo_html_url,
            'processed_exports': processed_exports,
            'source_title': self.get_gitlab_url(request)
        }

        self.pop_from_session(request, 'gitlab_provider')
        return render(request, 'plugins/gitlab_export_success.html', context, status=200)


class GitLabIssueProvider(GitLabProviderMixin, OauthIssueProvider):
    add_label = _('Add GitLab integration')
    send_label = _('Send to GitLab')
    description = _('This integration allows the creation of issues in arbitrary GitLab repositories. '
                    'The upload of attachments is not supported by GitLab.')

    _fields = {
        'repo_url': {
            'key': 'repo_url',
            'placeholder': 'placeholder',
            'help': _('The URL of the GitLab repository to send issues to.')
        },
        'secret': {
            'key': 'secret',
            'placeholder': 'Secret (random) string',
            'help': _('The secret for a GitLab webhook to close a task (optional).'),
            'required': False,
            'secret': True
        }
    }

    def get_post_url(self, request, issue, integration, subject, message, attachments):
        repo_url = integration.get_option_value('repo_url')
        if repo_url:
            repo = quote(repo_url.replace(self.get_gitlab_url(request), '').strip('/'), safe='')
            return f'{self.get_gitlab_url(request)}/api/v4/projects/{repo}/issues'

    def get_post_data(self, request, issue, integration, subject, message, attachments):
        return {
            'title': subject,
            'description': message
        }

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
        return self._fields.values()

    @fields.setter
    def fields(self, new_fields):
        if isinstance(new_fields, dict):
            for k, v in new_fields.items():
                self._fields[k] = v

    def integration_setup(self, request):
        redirect_url = request.build_absolute_uri()

        provider = self.get_from_session(request, 'gitlab_provider')
        if provider is None:
            self.store_in_session(request, 'redirect_url', redirect_url)
            return self.select_provider(request)

        minimum_repo_access_level = 15 # planner
        repo_choices, repo_help_text = self.get_repo_form_field_data(request, minimum_repo_access_level)

        gitlab_app_repo_url = {**self._fields['repo_url']}
        if repo_choices is not None:
            gitlab_app_repo_url['widget'] = forms.RadioSelect(choices=repo_choices)

        if repo_help_text is not None:
            gitlab_app_repo_url['help'] = repo_help_text

        self.fields = {'repo_url': gitlab_app_repo_url}
