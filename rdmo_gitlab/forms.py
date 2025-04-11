from django import forms
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError

class ExportsChoiceMultiWidget(forms.MultiWidget):
    def __init__(self, attrs=None):
        widgets = {
            'checkbox': forms.CheckboxInput(),
            'text': forms.TextInput(),
        }
        super().__init__(widgets, attrs)

    def decompress(self, value):
        boolean_value = {
            'False': False,
            'True': True
        }
        if value:
            splitted_value = value.split(',')
            splitted_value[0] = boolean_value[splitted_value[0]]
            return splitted_value
        return [False, '']
    
    def get_context(self, name, value, checkbox_label, checkbox_id, attrs):
        context = super().get_context(name, value, attrs)
        # value is a list/tuple of values, each corresponding to a widget
        # in self.widgets.
        if not isinstance(value, (list, tuple)):
            value = self.decompress(value)

        final_attrs = context['widget']['attrs']
        subwidgets = []
        for i, (widget_name, widget) in enumerate(
            zip(self.widgets_names, self.widgets)
        ):
            try:
                widget_value = value[i]
            except IndexError:
                widget_value = None
            
            widget_attrs = final_attrs.copy()
            if widget_name == '_text':
                widget_attrs.update({'style': 'flex-grow:1;'})
            if widget_name == '_checkbox':
                widget_attrs.update({'id': checkbox_id})
            
            widget_context = widget.get_context(name + widget_name, widget_value, widget_attrs)['widget']
            if widget_name == '_checkbox':
                widget_context.update({'label': checkbox_label})
            subwidgets.append(widget_context)
        
        context['widget']['subwidgets'] = subwidgets
        return context

class ExportsChoiceMultiValueField(forms.MultiValueField):
    widget = ExportsChoiceMultiWidget
    def __init__(self):
        fields = (
            forms.BooleanField(),
            forms.CharField(),
        )
        super().__init__(fields)

    def compress(self, data_list):
        return ','.join(map(str, data_list))
    
class ExportsSelectMultiple(forms.SelectMultiple):
    allow_multiple_selected = True
    option_template_name = 'plugins/exports_multivalue_select_option.html'
    template_name = 'plugins/exports_multivalue_select.html'
    add_id_index = True
    checked_attribute = {'checked': True}
    option_inherits_attrs = True

    choice_widget = ExportsChoiceMultiWidget()

    def optgroups(self, name, value, attrs=None):
        'Return a list of optgroups for this widget.'
        selected_option_keys = [v.split(',')[0] for v in value]
        transformed_value = [f'{True},{v.split(",")[1]}' for v in value]
        
        groups = []
        for index, (option_value, (option_label, option_key)) in enumerate(self.choices):
            i = selected_option_keys.index(option_key) if option_key in selected_option_keys else None
            option_value = transformed_value[i] if i is not None else option_value
            decompressed_option_value = self.choice_widget.decompress(option_value)
            
            selected = self.allow_multiple_selected and decompressed_option_value[0]
            
            option_name = f'{name}_{option_key}'
            
            groups.append(self.create_option(
                self.choice_widget,
                option_name,
                option_value,
                option_label,
                option_key,
                selected,
                index,
                attrs=attrs,
            ))

        return groups

    def create_option(
        self, widget, name, value, label, key, selected, index, attrs=None
    ):
        index = str(index)
        option_attrs = (
            self.build_attrs(self.attrs, attrs) if self.option_inherits_attrs else {}
        )
        if 'id' in option_attrs:
            id = self.id_for_label(option_attrs['id'], index)
            option_attrs = {}
        if selected:
            option_attrs.update(self.checked_attribute)
        option_context = widget.get_context(name, value, label, id, option_attrs)
        choices_to_update = getattr(self, 'choices_to_update', None)
        option_in_repo = self.choices_to_update[key] if choices_to_update and key in self.choices_to_update.keys() else False
        
        return {
            'name': name,
            'value': value,
            'subwidgets': option_context['widget']['subwidgets'],
            'selected': selected,
            'option_in_repo': option_in_repo,
            'template_name': self.option_template_name,
        }
    
    def id_for_label(self, id_, index='0'):
        '''
        Use an incremented id for each option where the main widget
        references the zero index.
        '''
        if id_ and self.add_id_index:
            id_ = '%s_%s' % (id_, index)
        return id_
    
    def value_from_datadict(self, data, files, name):
        value = []
        for multiwidget_name in self.choice_names:
            multiwidget_value = self.choice_widget.value_from_datadict(data, files, f'{name}_{multiwidget_name}')
            if multiwidget_value[0]:
                value.append(f'{multiwidget_name},{multiwidget_value[1]}')

        return value

class ExportsMultipleChoiceField(forms.MultipleChoiceField):
    widget = ExportsSelectMultiple
    choice_field = ExportsChoiceMultiValueField()
    _choice_names = []
    _choices_to_update = {}
    
    @property
    def choice_names(self):
        return self._choice_names
    
    @choice_names.setter
    def choice_names(self, new_names):
        self._choice_names = self.widget.choice_names = new_names

    @property
    def choices_to_update(self):
        return self._choices_to_update

    @choices_to_update.setter
    def choices_to_update(self, new_values):
        self._choices_to_update = self.widget.choices_to_update = new_values

    def clean(self, value):
        '''
        Validate the given value and return its 'cleaned' value as an
        appropriate Python object. Raise ValidationError for any errors.
        '''
        value = self.to_python(value)

        if value in self.empty_values and self.required:
            raise ValidationError(self.error_messages['required'], code='required')

        for multivalue in value:
            choice_key, text_value = multivalue.split(',')
            if choice_key in self.choice_names:
                out = self.choice_field.clean([True, text_value])
        return value

class GitLabExportForm(forms.Form):
    def __init__(self, *args, **kwargs):
        export_choices = kwargs.pop('export_choices', None)
        export_choices_to_update = kwargs.pop('export_choices_to_update', None)
        super().__init__(*args, **kwargs)

        if export_choices_to_update is not None:
            self.fields['exports'].choices_to_update = export_choices_to_update
            self.fields['exports'].help_text = _('Warning: Existing content in GitLab will be overwritten. To avoid this, consider updating the file path or the branch.')

        if export_choices is not None:
            self.fields['exports'].choices = export_choices
            self.fields['exports'].choice_names = [c[1][1] for c in export_choices]
            self.fields['all_exports'].widget = forms.CheckboxInput(
                attrs={'onclick': f'select_all_exports({len(export_choices)})'}
            )

    repo = forms.CharField(label=_('GitLab repository'),
                           help_text=_('Please use the form username/repository or organization/repository.'))
    
    exports = ExportsMultipleChoiceField(
        label=_('Export choices'),
        help_text=_('Warning: Existing content in GitLab will be overwritten'),
    )
    all_exports = forms.BooleanField(
        label=_('Select all export choices'),
        required=False,
    )
    
    branch = forms.CharField(label=_('Branch'), initial='main')
    
    commit_message = forms.CharField(label=_('Commit message'))

class GitLabImportForm(forms.Form):
    repo = forms.CharField(label=_('GitLab repository'),
                            help_text=_('Please use the form username/repository or organization/repository.'))
    path = forms.CharField(label=_('File path'),)
    ref = forms.CharField(label=_('Branch, tag, or commit'), initial='main')