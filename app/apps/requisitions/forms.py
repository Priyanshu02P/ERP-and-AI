from django import forms

from apps.core.forms import BootstrapFormMixin

from .models import PRLine, PurchaseRequisition


class PRForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = PurchaseRequisition
        fields = ["title", "department", "category", "needed_by", "justification"]
        widgets = {
            "needed_by": forms.DateInput(attrs={"type": "date"}),
            "justification": forms.Textarea(attrs={"rows": 3}),
        }


class PRLineForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = PRLine
        fields = ["description", "quantity", "uom", "est_unit_price"]


PRLineFormSet = forms.inlineformset_factory(
    PurchaseRequisition, PRLine, form=PRLineForm, extra=3, can_delete=True, min_num=1, validate_min=True,
)


class DecisionForm(BootstrapFormMixin, forms.Form):
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}),
                              help_text="Mandatory when rejecting or returning.")
