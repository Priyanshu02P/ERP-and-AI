from django.contrib import admin

from .models import RFQ, Quotation, QuotationLine, RFQLine, RFQVendor


class RFQLineInline(admin.TabularInline):
    model = RFQLine
    extra = 0


class RFQVendorInline(admin.TabularInline):
    model = RFQVendor
    extra = 0


@admin.register(RFQ)
class RFQAdmin(admin.ModelAdmin):
    """Admin can correct metadata (e.g. an expired deadline on a draft); status changes go via the app."""

    list_display = ("rfq_number", "pr", "status", "response_deadline", "created_by")
    list_filter = ("status",)
    readonly_fields = ("rfq_number", "pr", "status", "created_by", "issued_at", "closed_at")
    inlines = [RFQLineInline, RFQVendorInline]


class QuotationLineInline(admin.TabularInline):
    model = QuotationLine
    extra = 0


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    list_display = ("quote_number", "rfq", "vendor", "revision_no", "total_amount", "status", "is_late")
    list_filter = ("status", "is_late")
    inlines = [QuotationLineInline]
