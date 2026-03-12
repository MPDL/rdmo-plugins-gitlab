import base64
import json
import logging
import requests
from urllib.parse import quote

from django import forms
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponse, Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext, gettext_lazy as _

from rdmo.core.plugins import get_plugin
from rdmo.projects.providers import OauthIssueProvider
from rdmo.projects.exports import Export

from rdmo_maus.exports.smp_exports import SMPExportMixin
from rdmo_maus.forms.custom_validators import validate_file_path, FilePathExtensionValidator

from ..mixins import GitLabProviderMixin
from ..forms.forms import GitLabExportForm

logger = logging.getLogger(__name__)

class GitLabExportProvider(GitLabProviderMixin, Export, SMPExportMixin):
    choice_labels = [
        ('xml', _('RDMO XML')),
        ('csvcomma', _('CSV (comma separated)')), 
        ('csvsemicolon', _('CSV (semicolon separated)')), 
        ('json', _('JSON'))
    ]
    
    @property
    def export_choices(self):
        export_choices = []
        for choice_key, choice_label in self.choice_labels:
            file_extension = 'csv' if choice_key.startswith('csv') else choice_key
            catalog = self.project.catalog.uri_path
            catalog = catalog.lower() if isinstance(catalog, str) else 'project_export'
            file_path = f"data/{catalog}{f'_{choice_key}' if file_extension == 'csv' else ''}.{file_extension}"
            file_path_label = _('File path')

            export_choices.append(
                (f'False,{file_path}', (choice_label, file_path_label), choice_key)
            )

        smp_exports = getattr(self, 'smp_exports', None)
        if smp_exports and len(smp_exports) > 0:
            smp_export_choices = [(f'False,{v["file_path"]}', (v["label"], file_path_label), k) for k,v in smp_exports.items()]
            return smp_export_choices + export_choices

        return export_choices

    @property
    def export_choice_validators(self):
        export_choice_validators = {}

        valid_extensions = {
            'xml': '.xml',
            'csvcomma': '.csv', 
            'csvsemicolon': '.csv', 
            'json': '.json',
        }
        choice_keys = ['xml', 'csvcomma', 'csvsemicolon', 'json']
        
        smp_exports = getattr(self, 'smp_exports', None)
        if smp_exports and len(smp_exports) > 0:
            valid_extensions.update(
                {k: f".{v['file_path'].split('.')[-1]}" for k,v in smp_exports.items() if not k.startswith('license')}
            )
            choice_keys.extend(smp_exports.keys())

        for choice_key in choice_keys:
            if choice_key.startswith('license'):
                export_choice_validators[choice_key] = {
                    'text': [validate_file_path]
                }
                continue
            
            export_choice_validators[choice_key] = {
                'text': [validate_file_path, FilePathExtensionValidator(valid_extensions.get(choice_key))]
            }
        
        return export_choice_validators
    
    @property
    def export_choice_attributes(self):
        export_choice_attributes = {}
        for c in self.export_choices:
            simple_checkbox = False
            values = c[0].split(',')
            if isinstance(values, list) and len(values) == 1:
                simple_checkbox = True

            choice_key = c[2]
            if not simple_checkbox:
                export_choice_attributes[choice_key] = {
                    'text': {
                        'placeholder': _('example_folder/example_file.extension'),
                    }
                }

        return export_choice_attributes
    
    def render(self):
        self.pop_from_session(self.request, 'gitlab_export_choice_warnings')
        
        access_token = self.validate_access_token(self.request, self.get_from_session(self.request, 'access_token'))
        if access_token is None:
            redirect_url = self.request.build_absolute_uri()
            self.store_in_session(self.request, 'redirect_url', redirect_url)
            return self.authorize(self.request)
        
        context = {
            'new_repo_name_display': 'none',
            'repo_display': 'block',
            'form': self.get_form(
                self.request, 
                GitLabExportForm, 
                export_choices=self.export_choices,
                export_choice_validators=self.export_choice_validators,
                export_choice_attributes=self.export_choice_attributes
            ),
            'source_title': self.gitlab_url
        }
        return render(self.request, 'plugins/gitlab_export_form.html', context, status=200)

    def submit(self):
        form = self.get_form(
            self.request, 
            GitLabExportForm, 
            self.request.POST, 
            export_choices=self.export_choices,
            export_choice_validators=self.export_choice_validators,
            export_choice_attributes=self.export_choice_attributes
        )

        if 'cancel' in self.request.POST:
            if self.project is None:
                return redirect('projects')
            else:
                return redirect('project', self.project.id)

        if form.is_valid():
        
            # 1. Validate export choices: Check submitted file paths to warn user if repo files will be overwritten
            export_choice_warnings = self.get_from_session(self.request, 'gitlab_export_choice_warnings')
            new_repo = form.cleaned_data['new_repo']
            
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
                    'errors': [_('Export choices could not be created or repository content would have been overwritten without a warning.')]
                }, status=200)
        
        new_repo = True if 'new_repo' in form.data else False
        context = {
            'new_repo_name_display': 'block' if new_repo else 'none',
            'repo_display': 'none' if new_repo else 'block',
            'form': form,
            'source_title': self.gitlab_url
        }
        return render(self.request, 'plugins/gitlab_export_form.html', context, status=200)
    
    def get_file_metadata(self, request, url):
        access_token = self.get_from_session(request, 'access_token')
        if access_token:
            response = requests.head(url, headers=self.get_authorization_headers(access_token))
            try:
                response.raise_for_status()
                return response
            except:
                return None
        
        return None

    def check_file_paths(self, exports, repo, branch):
        export_choice_warnings = {}
        choice_keys = []
        for e in exports:
            choice_key, file_path = e.split(',')
            choice_keys.append(choice_key)
            url = self.get_request_url(repo, path=file_path, ref=branch)

            response = self.get_file_metadata(self.request, url)
            if response is not None and response.status_code == 200:
                export_choice_warnings[choice_key] = [gettext('A file with the same path exists in the selected repository and will be overwritten')]
        
        return export_choice_warnings, choice_keys, exports, branch
    
    def validate_export_choices(self, form_data):
        export_choice_warnings, selected_choice_keys, checked_export_choices, checked_branch = self.check_file_paths(
            form_data['exports'], 
            form_data['repo'], 
            form_data['branch']
        )
        self.store_in_session(self.request, 'gitlab_export_choice_warnings', export_choice_warnings)
        self.store_in_session(self.request, 'gitlab_checked_export_choices', checked_export_choices)
        self.store_in_session(self.request, 'gitlab_checked_branch', checked_branch)
        
        selected_choices = [c for c in self.export_choices if c[2] in selected_choice_keys]
        form = self.get_form(
            self.request, 
            GitLabExportForm, 
            self.request.POST, 
            export_choices=selected_choices, 
            export_choice_warnings=export_choice_warnings,
            export_choice_validators=self.export_choice_validators,
            export_choice_attributes=self.export_choice_attributes
        ) 
        context = {
            'new_repo_name_display': 'none',
            'repo_display': 'block',
            'form': form
        }             
        return context, export_choice_warnings
    
    def render_export(self, choice_key):
        smp_exports = getattr(self, 'smp_exports', None)
        if smp_exports and choice_key in self.smp_exports.keys():
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
        except:
            logger.warning(f'GitLabExportProvider - No content created for {choice_key}')
            choice_content = None

        return choice_content
    
    def process_form_data(self, form_data, update_without_warning=False):
        export_choice_warnings = self.pop_from_session(self.request, 'gitlab_export_choice_warnings')
        actions = []
        processed_exports = []

        checked_export_choices = self.pop_from_session(self.request, 'gitlab_checked_export_choices')
        checked_branch = self.pop_from_session(self.request, 'gitlab_checked_branch')
        new_repo = form_data['new_repo']
        branch = 'main' if new_repo else form_data['branch']

        exports = form_data['exports']
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
                        'label': next((c[1][0] for c in self.export_choices if c[2] == choice_key), choice_key), 
                        'success': False,
                        'processing_status': _('not exported - it would have overwritten existing file in repository.')
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

            choice_label = next((c[1][0] for c in self.export_choices if c[2] == choice_key), choice_key)
            processed_exports.append({
                'key': choice_key,
                'label': choice_label, 
                'success': success,
                'processing_status': processing_status
            })

        successfully_processed_exports = list(filter(lambda x: x['success'] == True, processed_exports))
        if len(successfully_processed_exports) == 0:
            logger.warning(f'GitLabExportProvider - No export content could be created for the selected choices: {exports}.')
            return None, None

        self.store_in_session(self.request, 'gitlab_processed_exports', processed_exports)

        repo = 'repo_placeholder' if new_repo else form_data['repo'].replace(self.gitlab_url, '').strip('/')
        url = self.get_request_url(repo, suffix='/repository/commits')
        
        request_data = {
            'branch': branch,
            'commit_message': form_data['commit_message'],
            'actions': actions
        }

        if new_repo:
            request_data['url'] = url
            self.store_in_session(self.request, 'gitlab_export_data', request_data)

            url = '{api_url}/projects'.format(api_url=self.api_url)
            new_repo_name = form_data['new_repo_name']
            request_data = {'name': new_repo_name}

        return url, request_data

    def post_success(self, request, response):
        request_data = self.pop_from_session(self.request, 'gitlab_export_data')
        if isinstance(request_data, dict):
            repo = response.json().get('path_with_namespace')
            if repo:
                url = request_data.pop('url').replace('repo_placeholder', quote(repo, safe=''))
                return self.post(self.request, url, json=request_data)
        
        processed_exports = self.pop_from_session(request, 'gitlab_processed_exports')
        repo_html_url = response.json().get('web_url').split("-/commit")[0]
        
        successful_exports = list(filter(lambda x: x['success'] == True, processed_exports))
        if len(successful_exports) == len(processed_exports):
            return redirect(repo_html_url)
        
        context = {
            'repo_html_url': repo_html_url, 
            'processed_exports': processed_exports, 
            'source_title': self.gitlab_url
        }
        return render(request, 'plugins/gitlab_export_success.html', context, status=200)
    

class GitLabIssueProvider(GitLabProviderMixin, OauthIssueProvider):
    add_label = _('Add GitLab integration')
    send_label = _('Send to GitLab')

    @property
    def description(self):
        return _(f'This integration allows the creation of issues in arbitrary repositories on {self.gitlab_url}. '
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
            repo = quote(repo_url.replace(self.gitlab_url, '').strip('/'), safe='')
            return '{api_url}/api/v4/projects/{repo}/issues'.format(
                api_url=self.gitlab_url, 
                repo=repo
            )

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
        self.store_in_session(request, 'redirect_url', redirect_url)
        minimum_repo_access_level = 15 # planner
        repo_choices, repo_help_text = self.get_repo_form_field_data(request, minimum_repo_access_level)

        gitlab_app_repo_url = {**self._fields['repo_url']}
        if repo_choices is not None:
            gitlab_app_repo_url['widget'] = forms.RadioSelect(choices=repo_choices)
            
        if repo_help_text is not None:
            gitlab_app_repo_url['help'] = repo_help_text

        self.fields = {'repo_url': gitlab_app_repo_url}