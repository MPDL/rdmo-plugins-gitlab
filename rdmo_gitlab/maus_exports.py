import requests
import base64

from django.template import TemplateSyntaxError
from django.http import HttpResponse

from rdmo.core.utils import render_to_format
from rdmo.domain.models import Attribute
from rdmo.projects.exports import Export
from rdmo.projects.utils import get_value_path
from rdmo.views.models import View

from .utils import zip

# def get_licenses(spdx_ids):
#     # https://github.com/spdx/license-list-data    
#     license_contents = {}
#     for id in spdx_ids:
#         url = 'https://api.github.com/repos/spdx/license-list-data/contents/text/{spdx_id}.txt'.format(spdx_id=id)        
#         response = requests.get(url, headers={'Accept': 'application/vnd.github+json'})
#         try:
#             response.raise_for_status()
#             base64_string_of_content = response.json().get('content')
#             base64_bytes_of_content = base64_string_of_content.encode('utf-8')
#             content_bytes = base64.b64decode(base64_bytes_of_content)
#             content = content_bytes.decode('utf-8')
#             license_contents[f'LICENSE_{id.replace("-", "_")}'] = content
#         except:
#             continue
        
#     return license_contents

def get_license(spdx_id):
    # https://github.com/spdx/license-list-data
    url = 'https://api.github.com/repos/spdx/license-list-data/contents/text/{spdx_id}.txt'.format(spdx_id=spdx_id)        
    response = requests.get(url, headers={'Accept': 'application/vnd.github+json'})
    try:
        response.raise_for_status()
        base64_string_of_content = response.json().get('content')
        base64_bytes_of_content = base64_string_of_content.encode('utf-8')
        content_bytes = base64.b64decode(base64_bytes_of_content)
        content = content_bytes.decode('utf-8')
    except:
        content = None
        
    return content

def get_project_license_ids(project, snapshot=None):
    attribute = Attribute.objects.get(uri='https://rdmorganiser.github.io/terms/domain/smp/software-license')
    spdx_ids = [license.value for license in project.values.filter(snapshot=snapshot, attribute=attribute)]
    return spdx_ids

# def render_to_license(project, snapshot=None):
#         spdx_ids = get_project_license_ids(project, snapshot)
#         license_contents = get_licenses(spdx_ids)
#         if len(license_contents) == 1:
#             content = list(license_contents.values())[0]
#             content_type = 'text/plain'
#             file_name = 'LICENSE'
#             content_disposition = f'attachment; filename="{file_name}"'

#         elif len(license_contents) > 1:
#             content = zip(license_contents)
#             content_type = 'application/zip'
#             file_name = 'licenses.zip'
#             content_disposition = f'attachment; filename="{file_name}"'

#         else:
#             return None
        
#         response = HttpResponse(
#             content,
#             headers={
#                 "Content-Type": content_type,
#                 "Content-Disposition": content_disposition,
#             },
#         )
#         return {'response': response, 'file_name': file_name}

# def render_to_license(project, snapshot=None):
#     spdx_ids = get_project_license_ids(project, snapshot)
#     license_contents = get_licenses(spdx_ids)
#     if len(license_contents) == 1:
#         content = list(license_contents.values())[0]
#         content_type = 'text/plain'
#         file_name = 'LICENSE'
#         content_disposition = f'attachment; filename="{file_name}"'

#     elif len(license_contents) > 1:
#         content = zip(license_contents)
#         content_type = 'application/zip'
#         file_name = 'licenses.zip'
#         content_disposition = f'attachment; filename="{file_name}"'

#     else:
#         return None
    
#     response = HttpResponse(
#         content,
#         headers={
#             "Content-Type": content_type,
#             "Content-Disposition": content_disposition,
#         },
#     )
#     return {'response': response, 'file_name': file_name}

def render_to_license(project, snapshot, choice):
    project_license_ids = get_project_license_ids(project, snapshot)
    spdx_id = next((l for l in project_license_ids if l.lower().replace("-", "_") == choice), choice)
    content = get_license(spdx_id)    
    if content is not None:    
        content_type = 'text/plain'
        file_name = f'LICENSE_{spdx_id.replace("-", "_")}'
        content_disposition = f'attachment; filename="{file_name}"'

        response = HttpResponse(
            content,
            headers={
                "Content-Type": content_type,
                "Content-Disposition": content_disposition,
            },
        )
        return response
    
    else:
        return None

def render_from_view(project, snapshot, view_uri, title, export_format):
    view = View.objects.get(uri=view_uri)

    try:
        rendered_view = view.render(project, snapshot)
    except TemplateSyntaxError:
        return None

    response = render_to_format(
        None, export_format, title, 'projects/project_view_export.html', {
        'format': export_format,
        'title': title,
        'view': view,
        'rendered_view': rendered_view,
        'resource_path': get_value_path(project, snapshot)
        }
    )

    return response

class MAUSExport(Export):
    smp_exports_map = {
        'readme': {
            'form_choice_label': 'README',
            'form_choice_file_path': 'README.md',
            'render_function': render_from_view,
            'render_function_kwargs': {
                'view_uri': 'https://dev-rdmo.int.mpdl.mpg.de/terms/views/smp_readme',
                'title': 'README.md',
                'export_format': 'markdown'
            }
        },
        'citation': {
            'form_choice_label': 'CITATION',
            'form_choice_file_path': 'CITATION.cff',
            'render_function': render_from_view,
            'render_function_kwargs': {
                'view_uri': 'https://dev-rdmo.int.mpdl.mpg.de/terms/views/smp_citation',
                'title': 'CITATION.cff',
                'export_format': 'plain'
            }
        },
        'license': {
            'form_choice_label': 'LICENSE',
            'form_choice_file_path': 'LICENSE',
            'render_function': render_to_license,
            'render_function_kwargs': {}
        }
    }

    @property
    def smp_exports(self):
        # Add smp specific export choices if project has SMP Catalog
        if self.project.catalog.uri_path == 'smp':
            smp_exports = {}
            for k, v in self.smp_exports_map.items():
                if k == 'license':
                    license_ids = get_project_license_ids(self.project, self.snapshot)
                    license_count = len(license_ids)
                    if license_count == 1:
                        k = f'license_{license_ids[0].lower().replace("-", "_")}'
                    elif license_count > 1:
                        license_exports = {
                            f'license_{l.lower().replace("-", "_")}': {
                                'label': f'LICENSE_{l.replace("-", "_")}', 
                                'file_path': f'LICENSE_{l.replace("-", "_")}'
                            }
                            for l in license_ids
                        }
                        smp_exports.update(license_exports)
                        continue
                    else:
                        continue

                smp_exports[k] = {'label': v['form_choice_label'], 'file_path': v['form_choice_file_path']}

            return smp_exports
        else:
            return {}

    def render_smp_export(self, choice):
        if choice.startswith('license'):
            form_choice_label, form_choice_file_path, render_function, kwargs = self.smp_exports_map['license'].values()
            kwargs['choice'] = choice.replace('license_', '')
        else:
            form_choice_label, form_choice_file_path, render_function, kwargs = self.smp_exports_map[choice].values()
        
        response = render_function(self.project, self.snapshot, **kwargs)
        
        return response