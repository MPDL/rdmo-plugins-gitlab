from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from rdmo_maus.forms.validators import validate_text_field

def validate_new_repo_name(value):
    field_name = _('Repository name')
    min_length = 1
    max_length = 50
    not_allowed_pattern = f'[^A-Za-z0-9\-\_\.+ ]'
    allowed_char_name_str = _('alphanumeric, hyphen, underscore, period, plus sign and whitespace')

    errors = []
    
    try:
        validate_text_field(field_name, value, min_length, max_length, not_allowed_pattern, allowed_char_name_str)
    except ValidationError as ee:
        errors.extend([e for e in ee.error_list if e not in errors])

    if value.startswith(('-', '_', '.', '+')):
        errors.append(ValidationError(
            _('{field_name} must start with a letter or a digit.').format(
                field_name=field_name
            ),
            code='invalid'
        ))

    if value.endswith(('-', '_', '.', '.git', '.atom')):
        errors.append(ValidationError(
            _('{field_name} must not end with hyphen, underscore, period, ".git" or ".atom".').format(
                field_name=field_name
            ),
            code='invalid'
        ))

    if len(errors) > 0:
        raise ValidationError(errors)

def validate_import_file_path(value):
    if not value.endswith('.xml'):
        raise ValidationError(
            _('File must be in XML format.'),
            code='invalid'
        )