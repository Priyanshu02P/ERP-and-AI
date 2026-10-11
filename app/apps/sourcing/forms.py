from django import forms
from django.utils import timezone

from apps.core.forms import BootstrapFormMixin
from apps.masterdata.models import Vendor

from .models import INCOTERMS, RFQVendor


class RFQCreateForm(BootstrapFormMixin, forms.Form):
    title = forms.CharField(max_length=200)
    response_deadline = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    vendors = forms.ModelMultipleChoiceField(queryset=Vendor.objects.filter(is_active=True), widget=forms.CheckboxSelectMultiple)
    terms = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean_response_deadline(self):
        value = self.cleaned_data["response_deadline"]
        if value <= timezone.now():
            raise forms.ValidationError("Deadline must be in the future.")
        return value


class QuotationHeaderForm(BootstrapFormMixin, forms.Form):
    vendor = forms.ModelChoiceField(queryset=Vendor.objects.none())
    vendor_ref_no = forms.CharField(required=False, max_length=60, label="Vendor quote ref")
    quote_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), initial=timezone.localdate)
    valid_until = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    payment_terms = forms.CharField(required=False, max_length=200)
    lead_time_days = forms.IntegerField(required=False, min_value=0)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    # commercial terms
    discount_amount = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=2, initial=0,
                                         help_text="Total discount amount (not %).")
    freight_insurance = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=2, initial=0,
                                           label="Freight and insurance")
    taxes_duties = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=2, initial=0,
                                      label="Taxes and duties", help_text="Non-recoverable only (e.g. exclude GST you can claim back).")
    setup_charges = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=2, initial=0,
                                       label="Tooling / setup charges")
    incoterms = forms.ChoiceField(required=False, choices=[("", "-")] + INCOTERMS)
    min_order_qty = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=3, label="Minimum order quantity")
    warranty_months = forms.IntegerField(required=False, min_value=0)
    meets_specs = forms.NullBooleanField(widget=forms.NullBooleanSelect, required=False, label="Meets all mandatory specifications")
    certificates_available = forms.NullBooleanField(widget=forms.NullBooleanSelect, required=False, label="Required certificates available")
    capacity_confirmed = forms.NullBooleanField(widget=forms.NullBooleanSelect, required=False, label="Capacity confirmed for required quantity")
    deviations = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Exceptions to RFQ terms")
    file = forms.FileField(required=False, label="Vendor quotation document",
                           help_text="PDF, DOCX, XLSX, PNG or JPG (max 4 MB).")

    # Fields copied 1:1 onto the Quotation model.
    MODEL_FIELDS = ("vendor_ref_no", "quote_date", "valid_until", "payment_terms", "lead_time_days", "notes",
                    "discount_amount", "freight_insurance", "taxes_duties", "setup_charges", "incoterms",
                    "min_order_qty", "warranty_months", "meets_specs", "certificates_available",
                    "capacity_confirmed", "deviations")
    MONEY_FIELDS = ("discount_amount", "freight_insurance", "taxes_duties", "setup_charges")

    def clean(self):
        cleaned = super().clean()
        for name in self.MONEY_FIELDS:  # blank money = 0
            if cleaned.get(name) is None:
                cleaned[name] = 0
        return cleaned

    def __init__(self, *args, rfq, **kwargs):
        super().__init__(*args, **kwargs)
        invited = RFQVendor.objects.filter(rfq=rfq).values_list("vendor_id", flat=True)
        self.fields["vendor"].queryset = Vendor.objects.filter(pk__in=invited)


class QuotationLineForm(BootstrapFormMixin, forms.Form):
    rfq_line_id = forms.IntegerField(widget=forms.HiddenInput)
    unit_price = forms.DecimalField(required=False, min_value=0, max_digits=18, decimal_places=2)
    quantity = forms.DecimalField(min_value=0, max_digits=18, decimal_places=3)
    delivery_days = forms.IntegerField(required=False, min_value=0)
    remarks = forms.CharField(required=False, max_length=300)
