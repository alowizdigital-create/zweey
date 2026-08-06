import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.catalog.models import Product
from apps.core.permissions import boutique_role_required
from apps.tenants.models import Membership

from . import services
from .forms import ClientForm, InvoiceForm, InvoiceLineFormSet, PaymentForm, SaleForm
from .models import Client, Invoice, Sale
from .pdf import render_invoice_pdf
from .whatsapp import build_message, build_share_link, normalize_phone
from .whatsapp_api import WhatsAppSendError, is_configured, send_document

MANAGE_ROLES = (Membership.ADMIN_COMPTE, Membership.GERANT_BOUTIQUE, Membership.CAISSIER)


@login_required
def client_list(request):
    clients = Client.objects.filter(boutique=request.boutique).order_by("name")
    return render(request, "sales/client_list.html", {"clients": clients})


@login_required
def client_search(request):
    """Recherche client en direct pour l'écran de vente (POS), sur le même
    principe que catalog:product_search."""
    query = request.GET.get("q", "").strip()
    clients = Client.objects.filter(boutique=request.boutique)
    if query:
        clients = clients.filter(Q(name__icontains=query) | Q(phone__icontains=query))
    clients = clients.order_by("name")[:20]

    results = [
        {"id": str(c.id), "name": c.name, "phone": c.phone}
        for c in clients
    ]
    return JsonResponse({"results": results})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def client_create(request):
    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            client = form.save(commit=False)
            client.boutique = request.boutique
            client.save()
            messages.success(request, "Client créé.")
            return redirect("sales:client_list")
    else:
        form = ClientForm()
    return render(request, "sales/client_form.html", {"form": form})


def _extract_lines_data(formset):
    lines_data = []
    for form in formset:
        if not form.has_changed() or form.cleaned_data.get("DELETE"):
            continue
        description = form.cleaned_data.get("description")
        if not description:
            continue
        lines_data.append(
            {
                "product": form.cleaned_data.get("product"),
                "description": description,
                "quantity": form.cleaned_data["quantity"],
                "unit_price_ht": form.cleaned_data["unit_price_ht"],
                "tva_rate": form.cleaned_data["tva_rate"],
                "discount_amount": Decimal(form.cleaned_data.get("discount_amount") or 0),
            }
        )
    return lines_data


# --- Ventes -----------------------------------------------------------
# La vente est le point d'entrée du module : c'est elle qui déduit le
# stock à sa confirmation. La facture n'est qu'un document généré ensuite
# à partir d'une vente confirmée — voir sale_generate_invoice ci-dessous.

@login_required
def sale_list(request):
    sales = Sale.objects.filter(boutique=request.boutique).select_related("client", "invoice")
    return render(request, "sales/sale_list.html", {"sales": sales})


def _parse_cart(request):
    """Décode le panier envoyé par l'écran de caisse (JSON construit côté
    JS). Le produit et sa TVA sont toujours relus en base — jamais fait
    confiance au JSON pour ces valeurs — seule la quantité et le prix
    unitaire (modifiable par le caissier) viennent du client."""

    raw = request.POST.get("cart_json", "")
    try:
        items = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return None, "Panier invalide."

    if not items:
        return None, "Ajoutez au moins un produit au panier."

    product_ids = [item.get("product_id") for item in items]
    products = Product.objects.filter(id__in=product_ids, compte=request.compte, is_active=True)
    products_by_id = {str(p.id): p for p in products}

    lines_data = []
    for item in items:
        product = products_by_id.get(str(item.get("product_id")))
        if product is None:
            return None, "Un produit du panier n'existe plus."
        try:
            quantity = Decimal(str(item.get("quantity", "0")))
            unit_price_ht = Decimal(str(item.get("unit_price_ht", "0")))
        except InvalidOperation:
            return None, "Quantité ou prix invalide dans le panier."
        if quantity <= 0 or unit_price_ht < 0 or quantity != quantity.to_integral_value():
            return None, "Quantité ou prix invalide dans le panier."

        lines_data.append(
            {
                "product": product,
                "description": product.name,
                "quantity": quantity,
                "unit_price_ht": unit_price_ht,
                "tva_rate": product.tva_rate,
            }
        )
    return lines_data, None


# @login_required
# @boutique_role_required(*MANAGE_ROLES)
# def sale_create(request):
#     if request.method == "POST":
#         form = SaleForm(request.POST, boutique=request.boutique)
#         lines_data, error = _parse_cart(request)
#         if error:
#             messages.error(request, error)
#         elif form.is_valid():
#             sale = services.build_sale(
#                 boutique=request.boutique,
#                 client=form.cleaned_data["client"],
#                 created_by=request.user,
#                 lines_data=lines_data,
#             )
#             messages.success(request, f"{sale.number} créée en brouillon.")
#             return redirect("sales:sale_detail", sale_id=sale.id)
#     else:
#         form = SaleForm(boutique=request.boutique)

#     return render(request, "sales/sale_form.html", {"form": form})



@login_required
@boutique_role_required(*MANAGE_ROLES)
def sale_create(request):
    if request.method == "POST":
        form = SaleForm(request.POST, boutique=request.boutique)
        lines_data, error = _parse_cart(request)
        if error:
            messages.error(request, error)
        elif form.is_valid():
            sale = services.build_sale(
                boutique=request.boutique,
                client=form.cleaned_data["client"],
                created_by=request.user,
                lines_data=lines_data,
            )
            messages.success(request, f"{sale.number} créée en brouillon.")
            return redirect("sales:sale_detail", sale_id=sale.id)
    else:
        form = SaleForm(boutique=request.boutique)

    return render(request, "sales/sale_form.html", {"form": form})
    

@login_required
def sale_detail(request, sale_id):
    sale = get_object_or_404(
        Sale.objects.select_related("client", "invoice").prefetch_related("lines"),
        id=sale_id,
        boutique=request.boutique,
    )
    return render(request, "sales/sale_detail.html", {"sale": sale})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def sale_confirm(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id, boutique=request.boutique)
    services.confirm_sale(sale, created_by=request.user)
    messages.success(request, f"{sale.number} confirmée, stock mis à jour.")
    return redirect("sales:sale_detail", sale_id=sale.id)


@login_required
@boutique_role_required(*MANAGE_ROLES)
def sale_generate_invoice(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id, boutique=request.boutique)
    try:
        invoice = services.generate_invoice_from_sale(sale, created_by=request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("sales:sale_detail", sale_id=sale.id)
    messages.success(request, f"Facture {invoice.number} générée.")
    return redirect("sales:invoice_detail", invoice_id=invoice.id)


# --- Devis & factures ---------------------------------------------------
# Un devis peut être créé directement (ce n'est pas une vente réelle : pas
# d'impact sur le stock). Une facture, elle, n'est plus créée à la main :
# elle est toujours générée à partir d'une vente confirmée (voir
# sale_generate_invoice).

@login_required
def invoice_list(request):
    """Liste des factures uniquement — les devis ont leur propre liste
    (voir devis_list), ce sont deux documents distincts pour l'utilisateur
    même s'ils partagent le même modèle Invoice en base."""
    invoices = (
        Invoice.objects.filter(boutique=request.boutique, type=Invoice.FACTURE)
        .select_related("client")
    )
    return render(request, "sales/invoice_list.html", {"invoices": invoices})


@login_required
def devis_list(request):
    devis = (
        Invoice.objects.filter(boutique=request.boutique, type=Invoice.DEVIS)
        .select_related("client")
    )
    return render(request, "sales/devis_list.html", {"devis": devis})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def invoice_create(request):
    if request.method == "POST":
        form = InvoiceForm(request.POST, boutique=request.boutique)
        formset = InvoiceLineFormSet(request.POST, form_kwargs={"compte": request.compte})
        if form.is_valid() and formset.is_valid():
            lines_data = _extract_lines_data(formset)
            if not lines_data:
                messages.error(request, "Ajoutez au moins une ligne au devis.")
            else:
                invoice = services.build_invoice(
                    boutique=request.boutique,
                    client=form.cleaned_data["client"],
                    type=Invoice.DEVIS,
                    created_by=request.user,
                    lines_data=lines_data,
                    discount_amount=Decimal(form.cleaned_data["discount_amount"]),
                )
                messages.success(request, f"{invoice.number} créé en brouillon.")
                return redirect("sales:invoice_detail", invoice_id=invoice.id)
    else:
        form = InvoiceForm(boutique=request.boutique)
        formset = InvoiceLineFormSet(form_kwargs={"compte": request.compte})

    return render(request, "sales/invoice_form.html", {"form": form, "formset": formset})


def _public_pdf_url(request, invoice):
    return request.build_absolute_uri(reverse("sales:invoice_public_pdf", args=[invoice.id]))


@login_required
def invoice_detail(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related("client").prefetch_related("lines", "payments"),
        id=invoice_id,
        boutique=request.boutique,
    )
    payment_form = PaymentForm()

    whatsapp_url = None
    has_client_phone = bool(invoice.client and invoice.client.phone)
    if has_client_phone:
        whatsapp_url = build_share_link(
            phone=invoice.client.phone, invoice=invoice, pdf_url=_public_pdf_url(request, invoice)
        )

    return render(
        request,
        "sales/invoice_detail.html",
        {
            "invoice": invoice,
            "payment_form": payment_form,
            "whatsapp_url": whatsapp_url,
            "whatsapp_api_configured": is_configured(),
            "has_client_phone": has_client_phone,
        },
    )


@login_required
@boutique_role_required(*MANAGE_ROLES)
def invoice_send_whatsapp(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related("client"), id=invoice_id, boutique=request.boutique
    )
    phone = normalize_phone(invoice.client.phone) if invoice.client else None
    if not phone:
        messages.error(request, "Ce client n'a pas de numéro de téléphone.")
        return redirect("sales:invoice_detail", invoice_id=invoice.id)

    pdf_url = _public_pdf_url(request, invoice)
    try:
        send_document(
            to=phone,
            message=build_message(invoice),
            document_url=pdf_url,
            filename=f"{invoice.number}.pdf",
        )
    except WhatsAppSendError as exc:
        messages.error(request, f"Échec de l'envoi WhatsApp : {exc}")
    else:
        messages.success(request, f"Facture envoyée par WhatsApp à {invoice.client.name}.")
    return redirect("sales:invoice_detail", invoice_id=invoice.id)


@login_required
@boutique_role_required(*MANAGE_ROLES)
def invoice_validate(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, boutique=request.boutique)
    services.validate_invoice(invoice, created_by=request.user)
    messages.success(request, f"{invoice.number} validé.")
    return redirect("sales:invoice_detail", invoice_id=invoice.id)


@login_required
@boutique_role_required(*MANAGE_ROLES)
def payment_create(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, boutique=request.boutique)
    if request.method == "POST":
        form = PaymentForm(request.POST)
        if form.is_valid():
            services.record_payment(
                invoice,
                amount=form.cleaned_data["amount"],
                method=form.cleaned_data["method"],
                reference=form.cleaned_data["reference"],
                created_by=request.user,
            )
            messages.success(request, "Paiement enregistré.")
    return redirect("sales:invoice_detail", invoice_id=invoice.id)


@login_required
def invoice_pdf(request, invoice_id):
    invoice = get_object_or_404(
        Invoice.objects.select_related("client", "boutique").prefetch_related("lines"),
        id=invoice_id,
        boutique=request.boutique,
    )
    pdf_bytes = render_invoice_pdf(invoice)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{invoice.number}.pdf"'
    return response


def invoice_public_pdf(request, invoice_id):
    """Lien public (sans connexion) utilisé pour le partage WhatsApp — la
    sécurité repose sur le caractère non devinable de l'UUID de la facture,
    comme un lien de partage classique (Google Docs, Stripe...). Pas de
    scoping par boutique/compte ici puisque le visiteur n'est pas connecté."""
    invoice = get_object_or_404(
        Invoice.objects.select_related("client", "boutique").prefetch_related("lines", "payments"),
        id=invoice_id,
    )
    pdf_bytes = render_invoice_pdf(invoice)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{invoice.number}.pdf"'
    return response
