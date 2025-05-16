import base64
import json
import logging
import requests

from urllib.parse import quote

from django import forms
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.http import HttpResponse, Http404

from rdmo.core.imports import handle_fetched_file
from rdmo.core.plugins import get_plugin
from rdmo.projects.imports import RDMOXMLImport
from rdmo.projects.providers import OauthIssueProvider
from rdmo.projects.exports import Export

from rdmo_maus.smp_exports import SMPExportMixin

from .forms import GitLabExportForm, GitLabImportForm
from .mixins import GitLabProviderMixin

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
            file_path = f"data/{self.project.title.replace(' ', '_')}{f'_{choice_key}' if file_extension == 'csv' else ''}.{file_extension}"

            export_choices.append(
                (f'False,{file_path}', (choice_label, choice_key))
            )

        smp_exports = getattr(self, 'smp_exports', None)
        if smp_exports and len(smp_exports) > 0:
            smp_export_choices = [(f'False,{v["file_path"]}', (v["label"], k)) for k,v in smp_exports.items()]
            return smp_export_choices + export_choices
        else:
            return export_choices

    def render(self):
        self.pop_from_session(self.request, 'gitlab_export_choices_to_update')
        access_token = self.validate_access_token(self.request, self.get_from_session(self.request, 'access_token'))
        if access_token is None:
            redirect_url = self.request.build_absolute_uri()
            self.store_in_session(self.request, 'redirect_url', redirect_url)
            return self.authorize(self.request)
        
        context = {
            'new_repo_name_display': 'none',
            'repo_display': 'block',
            'form': self.get_form(self.request, GitLabExportForm, export_choices=self.export_choices), # GitLabExportForm(export_choices=self.export_choices),
            'source_title': self.gitlab_url,
            'submit_label': _('Proceed')
        }
        return render(self.request, 'plugins/gitlab_export_form.html', context, status=200)

    def submit(self):
        form = self.get_form(self.request, GitLabExportForm, self.request.POST, export_choices=self.export_choices) # GitLabExportForm(self.request.POST, export_choices=self.export_choices)

        if 'cancel' in self.request.POST:
            if self.project is None:
                return redirect('projects')
            else:
                return redirect('project', self.project.id)

        if form.is_valid():
            new_repo = form.cleaned_data['new_repo']

            # 1. Check file paths to warn user if repo files will be overwritten
            choices_to_update = self.get_from_session(self.request, 'gitlab_export_choices_to_update')
            if not new_repo and choices_to_update is None:
                choices_to_update, checked_export_choices, checked_branch = self.check_file_paths(
                    form.cleaned_data['exports'], 
                    form.cleaned_data['repo'], 
                    form.cleaned_data['branch']
                )
                self.store_in_session(self.request, 'gitlab_export_choices_to_update', choices_to_update)
                self.store_in_session(self.request, 'gitlab_checked_export_choices', checked_export_choices)
                self.store_in_session(self.request, 'gitlab_checked_branch', checked_branch)
                
                selected_choices = [c for c in self.export_choices if c[1][1] in choices_to_update.keys()]
                form = self.get_form(
                    self.request, 
                    GitLabExportForm, 
                    self.request.POST, 
                    export_choices=selected_choices, 
                    export_choices_to_update=choices_to_update
                ) 
                # GitLabExportForm(self.request.POST, export_choices=selected_choices, export_choices_to_update=choices_to_update)
                context = {
                    'new_repo_name_display': 'none',
                    'repo_display': 'block',
                    'form': form, 
                    'source_title': self.gitlab_url, 
                    'submit_label':_('Export to GitLab')
                }             
                return render(self.request, 'plugins/gitlab_export_form.html', context, status=200)

            # 2. Create file content for selected choices and export them
            url, request_data = self.process_form_data(form.cleaned_data, choices_to_update)
            if url is not None and request_data is not None:
                return self.make_request(self.request, 'post', url, json=request_data)
            else:
                return render(self.request, 'core/error.html', {
                    'title': _('Something went wrong'),
                    'errors': [_('Export choices could not be created or repository content would have been overwritten without a warning')]
                }, status=200)
        
        new_repo = True if 'new_repo' in form.data else False
        context = {
            'new_repo_name_display': 'block' if new_repo else 'none',
            'repo_display': 'none' if new_repo else 'block',
            'form': form, 
            'source_title': self.gitlab_url, 
            'submit_label': _('Export to GitLab') if new_repo else _('Proceed')
        }
        return render(self.request, 'plugins/gitlab_export_form.html', context, status=200)
    
    def get_file_metadata(self, request, url):
        print('get_file_metadata()')
        access_token = self.get_from_session(request, 'access_token')
        print(f'    access_token: {access_token}')
        if access_token:
            response = requests.head(url, headers=self.get_authorization_headers(access_token))
            if response: 
                print(f'    response: ')
                try:
                    print(f'    {response.status_code}')
                except:
                    print(f'    cannot print status code')
                return response
        return None

    def check_file_paths(self, exports, repo, branch):
        print('check_file_paths()')
        choices_to_update = {}
        for e in exports:
            choice_key, file_path = e.split(',')
            print(f'    choice_key: {choice_key}, file_path: {file_path}')
            url = '{api_url}/projects/{repo}/repository/files/{path}?ref={ref}'.format(
                api_url=self.api_url,
                repo=quote(repo.replace(self.gitlab_url, '').strip('/'), safe=''),
                path=quote(file_path, safe=''),
                ref=quote(branch, safe='')
            )
            print(f'    url: {url}')

            response = self.get_file_metadata(self.request, url)
            # print(f'    response: {response.json()}')
            choice_in_repo = True if response is not None and response.status_code == 200 else False
            choices_to_update[choice_key] = choice_in_repo

        return choices_to_update, exports, branch
    
    def render_export(self, choice_key):
        smp_exports = getattr(self, 'smp_exports', None)
        if smp_exports and (
            choice_key in self.smp_exports.keys() or (choice_key.startswith('license_') and 'license' in self.smp_exports.keys())
        ):
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
            logger.warning(f'No content created for {choice_key}')
            choice_content = None

        return choice_content
    
    def process_form_data(self, form_data, choices_to_update, update_without_warning=False):
        actions = []
        processed_exports = []

        checked_export_choices = self.pop_from_session(self.request, 'gitlab_checked_export_choices')
        checked_branch = self.pop_from_session(self.request, 'gitlab_checked_branch')
        new_repo = form_data['new_repo']
        branch = 'main' if new_repo else form_data['branch']

        exports = form_data['exports']
        for e in exports:
            choice_key, file_path = e.split(',')
            initial_file_path = file_path if new_repo else next((exp.split(',')[1] for exp in checked_export_choices if exp.split(',')[0] == choice_key), file_path)
            initial_branch = 'main' if new_repo else checked_branch
            if file_path != initial_file_path or branch != initial_branch:
                new_choices_to_update, __, ___ = self.check_file_paths([e], form_data['repo'], branch)
                choices_to_update[choice_key] = new_choices_to_update[choice_key]
                if new_choices_to_update[choice_key] == True and not update_without_warning:
                    processed_exports.append({
                        'key': choice_key,
                        'label': next((c[1][0] for c in self.export_choices if c[1][1] == choice_key), choice_key), 
                        'success': False,
                        'processing_status': _('not exported - it would have overwritten existing file in repository')
                    })
                    continue
            
            choice_in_repo = False if new_repo else choices_to_update[choice_key]
            content = self.render_export_content(choice_key)
            if content is None:
                success = False
                processing_status = _('not exported - it could not be created')
            else:
                success = True
                processing_status = _('successfully exported')

                action = 'update' if choice_in_repo else 'create'
                actions.append({
                    'action': action,
                    'file_path': quote(file_path, safe="/ "),
                    'content': content,
                    'encoding': 'base64'
                })

            choice_label = next((c[1][0] for c in self.export_choices if c[1][1] == choice_key), choice_key)
            processed_exports.append({
                'key': choice_key,
                'label': choice_label, 
                'success': success,
                'processing_status': processing_status
            })

        successfully_processed_exports = list(filter(lambda x: x['success'] == True, processed_exports))
        if len(successfully_processed_exports) == 0:
            logger.warning(f'No export content could be created for the selected choices: {exports}')
            return None, None

        self.store_in_session(self.request, 'gitlab_processed_exports', processed_exports)

        repo = 'repo_placeholder' if new_repo else quote(form_data['repo'].replace(self.gitlab_url, '').strip('/'), safe='')
        url = '{api_url}/projects/{repo}/repository/commits'.format(
                api_url=self.api_url,
                repo=repo,
            )

        request_data = {
            'branch': quote(branch, safe=''),
            'commit_message': quote(form_data['commit_message'], safe=' '),
            'actions': actions
        }

        if new_repo:
            request_data['url'] = url
            self.store_in_session(self.request, 'gitlab_export_data', request_data)

            url = '{api_url}/projects'.format(api_url=self.api_url)
            new_repo_name = quote(form_data['new_repo_name'], safe='')
            request_data = {'name': new_repo_name}

        return url, request_data

    def post_success(self, request, response):
        request_data = self.pop_from_session(self.request, 'gitlab_export_data')
        if isinstance(request_data, dict):
            repo = response.json().get('path_with_namespace', None)
            if repo:
                url = request_data.pop('url').replace('repo_placeholder', quote(repo, safe=''))
                return self.make_request(self.request, 'post', url, json=request_data)
        
        processed_exports = self.pop_from_session(request, 'gitlab_processed_exports')
        repo_html_url = response.json().get('web_url').split("-/commit")[0]
        context = {'repo_html_url': repo_html_url, 'processed_exports': processed_exports}
        
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
            return '{}/api/v4/projects/{}/issues'.format(self.gitlab_url, repo)

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

    
class GitLabImport(GitLabProviderMixin, RDMOXMLImport):

    def render(self):
        access_token = self.validate_access_token(self.request, self.get_from_session(self.request, 'access_token'))
        if access_token is None:
            redirect_url = self.request.build_absolute_uri()
            self.store_in_session(self.request, 'redirect_url', redirect_url)
            return self.authorize(self.request)
        
        context = {
            'source_title': self.gitlab_url,
            'repo_display': 'block',
            'other_repo_display': 'none',
            'form': self.get_form(self.request, GitLabImportForm)
        }
        return render(self.request, 'plugins/gitlab_import_form.html', context, status=200)

    def submit(self):
        form = self.get_form(self.request, GitLabImportForm, self.request.POST)

        if 'cancel' in self.request.POST:
            if self.project is None:
                return redirect('projects')
            else:
                return redirect('project', self.project.id)

        if form.is_valid():
            self.request.session['import_source_title'] = form.cleaned_data['path']

            url = self.process_form_data(form.cleaned_data)

            return self.make_request(self.request, 'get', url)

        other_repo_check = True if 'other_repo_check' in form.data else False
        repo_display = 'none' if other_repo_check else 'block'
        other_repo_display = 'block' if other_repo_check else 'none'
        context = {
            'source_title': self.gitlab_url,
            'repo_display': repo_display,
            'other_repo_display': other_repo_display,
            'form': form
        }

        return render(self.request, 'plugins/gitlab_import_form.html', context, status=200)

    def process_form_data(self, form_data):
        other_repo_check  = form_data['other_repo_check']
        if other_repo_check:
            repo = quote(form_data['other_repo'].replace(self.gitlab_url, '').strip('/'), safe='')
        else:
            repo = quote(form_data['repo'].replace(self.gitlab_url, '').strip('/'), safe='')

        url = '{api_url}/projects/{repo}/repository/files/{path}?ref={ref}'.format(
            api_url=self.api_url,
            repo=repo,
            path=quote(form_data['path'], safe=''),
            ref=quote(form_data['ref'], safe='')
        )

        return url
    
    def get_success(self, request, response):
        file_content = response.json().get('content')
        request.session['import_file_name'] = handle_fetched_file(base64.b64decode(file_content))

        if self.current_project:
            return redirect('project_update_import', self.current_project.id)
        else:
            return redirect('project_create_import')
