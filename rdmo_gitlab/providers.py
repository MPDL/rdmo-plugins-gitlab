import base64
import json
import logging

# from io import BytesIO
from urllib.parse import quote

from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.http import HttpResponse

from rdmo.core.imports import handle_fetched_file
from rdmo.core.plugins import get_plugin
from rdmo.projects.imports import RDMOXMLImport
from rdmo.projects.providers import OauthIssueProvider

from .forms import GitLabExportForm, GitLabImportForm
from .mixins import GitLabProviderMixin
from .maus_exports import MAUSExport
# from .utils import unzip, get_record_id_from_project_value, set_record_id_on_project_value

logger = logging.getLogger(__name__)

class GitLabExportProvider(GitLabProviderMixin, MAUSExport):
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
            smp_export_choices = [(f'False,{v["file_path"]}', (v["label"],k)) for k,v in smp_exports.items()]
            return smp_export_choices + export_choices
        else:
            return export_choices

    def render(self):
        self.pop_from_session(self.request, 'gitlab_export_choices_to_update')
        access_token = self.validate_access_token(self.request, self.get_from_session(self.request, 'access_token'))
        print('render()')
        print(f'    access_token: {access_token}')
        if access_token is None:
            redirect_url = self.request.build_absolute_uri()
            self.store_in_session(self.request, 'redirect_url', redirect_url)
            return self.authorize(self.request)
        
        context = {
            'form': GitLabExportForm(export_choices=self.export_choices),
            'source_title': self.gitlab_url,
            'submit_label': _('Proceed')
        }
        return render(self.request, 'plugins/gitlab_export_form.html', context, status=200)

    def submit(self):
        form = GitLabExportForm(self.request.POST, export_choices=self.export_choices)

        if 'cancel' in self.request.POST:
            if self.project is None:
                return redirect('projects')
            else:
                return redirect('project', self.project.id)

        if form.is_valid():
            choices_to_update = self.get_from_session(self.request, 'gitlab_export_choices_to_update')
            # print(f'choices_to_update: {choices_to_update}')
            if choices_to_update is None:
                choices_to_update = self.check_file_paths(form.cleaned_data)
                selected_choices = [c for c in self.export_choices if c[1][1] in choices_to_update.keys()]
                form = GitLabExportForm(self.request.POST, export_choices=selected_choices, export_choices_to_update=choices_to_update)
                context = {'form': form, 'source_title': self.gitlab_url, 'submit_label':_('Export to GitLab')}
                return render(self.request, 'plugins/gitlab_export_form.html', context, status=200)

            url, request_data = self.process_form_data(form.cleaned_data, choices_to_update)
            # print(f"request_data: {request_data}")

            if url is not None and request_data is not None:
                return self.make_request(self.request, 'post', url, json=request_data)
            else:
                return render(self.request, 'core/error.html', {
                    'title': _('Error processing form data'),
                    'errors': [_('Something went wrong in the creation of the selected choices')]
                }, status=200)
        
        context = {'form': form, 'source_title': self.gitlab_url, 'submit_label':_('Export to GitLab')}
        return render(self.request, 'plugins/gitlab_export_form.html', context, status=200)
    
    def check_file_paths(self, form_data):
        # print('check_file_paths()')
        # print(f'    form_data: {form_data}')

        choices_to_update = {}
        for e in form_data['exports']:
            print(f'    e: {e}')
            choice, file_path = e.split(',')
            url = '{api_url}/projects/{repo}/repository/files/{path}?ref={ref}'.format(
                api_url=self.api_url,
                repo=quote(form_data['repo'], safe=''),
                path=quote(file_path),
                ref=quote(form_data['branch'], safe='')
            )
            print(f'    url: {url}')

            response = self.make_request(self.request, 'head', url)
            
            choice_in_repo = True if response is not None and response.status_code == 200 else False
            choices_to_update[choice] = choice_in_repo

        # print(f'    choices_to_update: {choices_to_update}')

        self.store_in_session(self.request, 'gitlab_export_choices_to_update', choices_to_update)

        return choices_to_update
    
    def render_export(self, choice):
        smp_exports = getattr(self, 'smp_exports', None)
        if smp_exports and (
            choice in self.smp_exports.keys() or (choice.startswith('license_') and 'license' in self.smp_exports.keys())
        ):
            # print("    getting content from MAUSExport")
            response = self.render_smp_export(choice)
        else:
            export_plugin = get_plugin('PROJECT_EXPORTS', choice)
            export_plugin.project = self.project
            response = export_plugin.render()

        return response
    
    # def render_export_content(self, choice):
    #     response, file_name = self.render_export(choice)
    #     choice_content = []
    #     try:
    #         if file_name.endswith('.zip'):
    #             unzipped_files = unzip(BytesIO(response.content))
    #             for name, c in unzipped_files.items():
    #                 base64_bytes_of_content = base64.b64encode(c)
    #                 base64_string_of_content = base64_bytes_of_content.decode('utf-8')
    #                 choice_content.append((base64_string_of_content, name))

    #         else:
    #             binary = response.content
    #             base64_bytes_of_content = base64.b64encode(binary)
    #             base64_string_of_content = base64_bytes_of_content.decode('utf-8')
    #             choice_content.append((base64_string_of_content, file_name))
    #         # print("    content creation worked")
    #     except:
    #         logger.warning(f'No content created for {choice}')
    #         # print("    content creation didn't worked")
    #         pass

    #     return choice_content

    def render_export_content(self, choice):
        response = self.render_export(choice)
        try:            
            binary = response.content
            base64_bytes_of_content = base64.b64encode(binary)
            base64_string_of_content = base64_bytes_of_content.decode('utf-8')
            choice_content = base64_string_of_content
            # print("    content creation worked")
        except:
            logger.warning(f'No content created for {choice}')
            # print("    content creation didn't worked")
            choice_content = None

        return choice_content
    
    def process_form_data(self, form_data, choices_to_update):
        # print('process_form_data()')
        # print(f"    form_data: {form_data}")
        actions = []
        successfully_processed_exports = []

        exports = form_data['exports']
        for e in exports:
            choice, file_path = e.split(',')
            choice_in_repo = choices_to_update[choice]
            content = self.render_export_content(choice)
            if content is not None:
                successfully_processed_exports.append(choice)
                # name = next((c[1][0] for c in self.export_choices if c[1][1] == choice), choice)
                action = 'update' if choice_in_repo else 'create'
                actions.append({
                    'action': action,
                    'file_path': quote(file_path),
                    'content': content,
                    'encoding': 'base64'
                })
                # print(f"    created action for {file_path} with action: {action}")

        if len(successfully_processed_exports) == 0:
            logger.warning(f'No export content could be created for the selected choices: {exports}')
            # print(f'No export content could be created for the selected choices: {exports}')
            return None, None

        self.store_in_session(self.request, 'gitlab_successfully_processed_exports', successfully_processed_exports)
        self.store_in_session(self.request, 'gitlab_chosen_exports', [choice.split(',')[0] for choice in exports])

        url = '{api_url}/projects/{repo}/repository/commits'.format(
                api_url=self.api_url,
                repo=quote(form_data['repo'].replace('https://gitlab.com/', ''), safe=''),
            )

        request_data = {
            'branch': quote(form_data['branch'], safe=''),
            'commit_message': quote(form_data['commit_message'], safe=' '),
            'actions': actions
        }

        return url, request_data
        # return None, None

    def post_success(self, request, response):
        successfully_processed_exports = self.pop_from_session(request, 'gitlab_successfully_processed_exports')
        # print(f"successfully_processed_exports: {successfully_processed_exports}")
        chosen_exports = self.pop_from_session(request, 'gitlab_chosen_exports')
        # print(f"chosen_exports: {chosen_exports}")

        # commit_id = response.json().get('id')
        successful_uploads = []
        for e in chosen_exports:
            success = True if e in successfully_processed_exports else False
            name = next((c[1][0] for c in self.export_choices if c[1][1] == e), e)
            successful_uploads.append({'name': name, 'success': success})
            # if success:
                # print(f"saving commit id for {e}")
                # set_record_id_on_project_value(self.project, commit_id, e) # name.lower().replace(' ', '_'))
        
        repo_html_url = response.json().get('web_url').split("-/commit")[0]
        context = {'repo_html_url': repo_html_url, 'successful_uploads': successful_uploads}
        return render(request, 'plugins/gitlab_export_success.html', context, status=200)
    

class GitLabIssueProvider(GitLabProviderMixin, OauthIssueProvider):
    add_label = _('Add GitLab integration')
    send_label = _('Send to GitLab')

    @property
    def description(self):
        return _(f'This integration allow the creation of issues in arbitrary repositories on {self.gitlab_url}. '
                 'The upload of attachments is not supported by GitLab.')

    def get_post_url(self, request, issue, integration, subject, message, attachments):
        repo_url = integration.get_option_value('repo_url')
        if repo_url:
            repo = repo_url.replace(self.gitlab_url, '').strip('/')
            return '{}/api/v4/projects/{}/issues'.format(self.gitlab_url, quote(repo, safe=''))

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
        return [
            {
                'key': 'repo_url',
                'placeholder': f'{self.gitlab_url}/username/repo',
                'help': _('The URL of the GitLab repository to send issues to.')
            },
            {
                'key': 'secret',
                'placeholder': 'Secret (random) string',
                'help': _('The secret for a GitLab webhook to close a task (optional).'),
                'required': False,
                'secret': True
            }
        ]


class GitLabImport(GitLabProviderMixin, RDMOXMLImport):

    def render(self):
        return render(self.request, 'projects/project_import_form.html', {
            'source_title': self.gitlab_url,
            'form': GitLabImportForm()
        }, status=200)

    def submit(self):
        form = GitLabImportForm(self.request.POST)

        if 'cancel' in self.request.POST:
            if self.project is None:
                return redirect('projects')
            else:
                return redirect('project', self.project.id)

        if form.is_valid():
            self.request.session['import_source_title'] = form.cleaned_data['path']

            url = '{api_url}/projects/{repo}/repository/files/{path}?ref={ref}'.format(
                api_url=self.api_url,
                repo=quote(form.cleaned_data['repo'], safe=''),
                path=quote(form.cleaned_data['path'], safe=''),
                ref=quote(form.cleaned_data['ref'], safe='')
            )

            return self.make_request(self.request, 'get', url)

        return render(self.request, 'projects/project_import_form.html', {
            'source_title': self.gitlab_url,
            'form': form
        }, status=200)

    def get_success(self, request, response):
        file_content = response.json().get('content')
        request.session['import_file_name'] = handle_fetched_file(base64.b64decode(file_content))

        if self.current_project:
            return redirect('project_update_import', self.current_project.id)
        else:
            return redirect('project_create_import')
