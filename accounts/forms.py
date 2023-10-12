from django import forms
from .models import Account

class RegistrationForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(
        attrs={'class':'form-control', 'placeholder':'Enter Password',}
        ))
    confirm_password = forms.CharField(label='Confirm Password', widget=forms.PasswordInput(
        attrs={'class':'form-control', 'placeholder':'Confirm Password'}
        ))
    
    class Meta:
        model = Account
        fields = ['first_name','last_name','phone_number','email', 'password']

    def clean(self):
        cleaned_data = super(RegistrationForm, self).clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')

        if password != confirm_password:
            raise forms.ValidationError(
                "Passwords do not match!"
            )

    def __init__(self, *args, **kwargs):
        super(RegistrationForm, self).__init__(*args, **kwargs)
        for field in self.fields:
            self.fields[field].widget.attrs['class'] = 'form-control'
            if field == 'confirm_password':
                self.fields[field].widget.attrs['placeholder'] = 'Confirm Password'
            else:
                self.fields[field].widget.attrs['placeholder'] = 'Enter ' + field.replace('_', ' ').title()
        
