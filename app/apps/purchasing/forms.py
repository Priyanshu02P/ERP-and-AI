from django import forms

from apps.core.forms import BootstrapFormMixin
from apps.masterdata.models import Vendor
from apps.requisitions.models import PRStatus, PurchaseRequisition

from .models import POLine, PurchaseOrder


class POForm(BootstrapFormMixin, forms.ModelForm):
    """Direct (no-RFQ) PO or renewal draft. Whether it needs an exemption is decided on submit, from the amount."""

    class Meta:
        model = PurchaseOrder
        fields = ["title", "vendor", "department", "category", "pr", "justification", "exemption_reason",
                  "payment_terms", "charges_amount", "delivery_date", "is_recurring", "frequency", "start_date",
                  "end_date", "notes"]
        widgets = {
            "justification": forms.Textarea(attrs={"rows": 2}),
            "exemption_reason": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "delivery_date": forms.DateInput(attrs={"type": "date"}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {"pr": "Approved PR (optional)", "is_recurring": "Recurring purchase (standing agreement)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vendor"].queryset = Vendor.objects.filter(is_active=True)
        self.fields["pr"].queryset = PurchaseRequisition.objects.filter(status=PRStatus.APPROVED)
        self.fields["pr"].required = False
        self.fields["charges_amount"].help_text = "Net of discounts. Leave 0 if none."
        self.fields["exemption_reason"].help_text = ("Needed only if the total exceeds the direct-purchase threshold "
                                                     "(or recent orders to this vendor add up to more). A CPO must approve it.")
        self.fields["frequency"].required = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_recurring"):
            for name in ("frequency", "start_date", "end_date"):
                if not cleaned.get(name):
                    self.add_error(name, "Required for a recurring purchase.")
            if cleaned.get("start_date") and cleaned.get("end_date") and cleaned["end_date"] < cleaned["start_date"]:
                self.add_error("end_date", "End date cannot be before the start date.")
        else:
            cleaned["frequency"] = ""
        return cleaned


class POLineForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = POLine
        fields = ["description", "quantity", "uom", "unit_price"]


def line_formset(extra=3):
    return forms.inlineformset_factory(PurchaseOrder, POLine, form=POLineForm, extra=extra, can_delete=True,
                                       min_num=1, validate_min=True)


class DecisionForm(BootstrapFormMixin, forms.Form):
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}),
                              help_text="Mandatory when rejecting or returning.")


class ReasonForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(required=False, max_length=300)
