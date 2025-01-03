# -*- coding: utf-8 -*-
import os
from functools import wraps
from io import BytesIO
from logging.config import dictConfig

from datetime import datetime, date

from flask import Flask, url_for, render_template, session, redirect, json, send_file
from flask_oauthlib.contrib.client import OAuth, OAuth2Application
from flask_session import Session
from xero_python.accounting import AccountingApi, ContactPerson, Contact, Contacts, Invoice, Invoices, LineItem, LineAmountTypes, CurrencyCode
from xero_python.api_client import ApiClient, serialize
from xero_python.api_client.configuration import Configuration
from xero_python.api_client.oauth2 import OAuth2Token
from xero_python.exceptions import AccountingBadRequestException
from xero_python.identity import IdentityApi
from xero_python.utils import getvalue

import logging_settings
from utils import jsonify, serialize_model

dictConfig(logging_settings.default_settings)

# configure main flask application
app = Flask(__name__)
app.config.from_object("default_settings")
app.config.from_pyfile("config.py", silent=True)

if app.config["ENV"] != "production":
    # allow oauth2 loop to run over http (used for local testing only)
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# configure persistent session cache
Session(app)

# configure flask-oauthlib application
# TODO fetch config from https://identity.xero.com/.well-known/openid-configuration #1
oauth = OAuth(app)
xero = oauth.remote_app(
    name="xero",
    version="2",
    client_id=app.config["CLIENT_ID"],
    client_secret=app.config["CLIENT_SECRET"],
    endpoint_url="https://api.xero.com/",
    authorization_url="https://login.xero.com/identity/connect/authorize",
    access_token_url="https://identity.xero.com/connect/token",
    refresh_token_url="https://identity.xero.com/connect/token",
    scope="offline_access openid profile email accounting.transactions "
    "accounting.journals.read accounting.transactions payroll.payruns accounting.reports.read "
    "files accounting.settings.read accounting.settings accounting.attachments payroll.payslip payroll.settings files.read openid assets.read profile payroll.employees projects.read email accounting.contacts.read accounting.attachments.read projects assets accounting.contacts payroll.timesheets accounting.budgets.read",
)  # type: OAuth2Application


# configure xero-python sdk client
api_client = ApiClient(
    Configuration(
        debug=app.config["DEBUG"],
        oauth2_token=OAuth2Token(
            client_id=app.config["CLIENT_ID"], client_secret=app.config["CLIENT_SECRET"]
        ),
    ),
    pool_threads=1,
)


# configure token persistence and exchange point between flask-oauthlib and xero-python
@xero.tokengetter
@api_client.oauth2_token_getter
def obtain_xero_oauth2_token():
    return session.get("token")


@xero.tokensaver
@api_client.oauth2_token_saver
def store_xero_oauth2_token(token):
    session["token"] = token
    session.modified = True


def xero_token_required(function):
    @wraps(function)
    def decorator(*args, **kwargs):
        xero_token = obtain_xero_oauth2_token()
        if not xero_token:
            return redirect(url_for("login", _external=True))

        return function(*args, **kwargs)

    return decorator


@app.route("/")
def index():
    xero_access = dict(obtain_xero_oauth2_token() or {})
    return render_template(
        "code.html",
        title="Home | oauth token",
        code=json.dumps(xero_access, sort_keys=True, indent=4),
    )

#get all invoices

@app.route("/invoices")
@xero_token_required
def get_invoices():
    xero_tenant_id = get_xero_tenant_id()
    accounting_api = AccountingApi(api_client)

    invoices = accounting_api.get_invoices(
        xero_tenant_id, statuses=["DRAFT", "SUBMITTED"]
    )
    code = serialize_model(invoices)
    sub_title = "Total invoices found: {}".format(len(invoices.invoices))

    return render_template(
        "code.html", title="Invoices", code=code, sub_title=sub_title
    )

#get invoice
#requires invoice_id
@app.route("/invoiceID")
@xero_token_required
def get_invoice_id():
    xero_tenant_id = get_xero_tenant_id()
    accounting_api = AccountingApi(api_client)

    invoice = accounting_api.get_invoice(
        xero_tenant_id, invoice_id="0e64a623-c2a1-446a-93ed-eb897f118cbc"
    )
    code = serialize_model(invoice)
    sub_title = "Invoice found:"

    return render_template(
        "code.html", title="Invoice", code=code, sub_title=sub_title
    )

@app.route("/invoiceNum")
@xero_token_required
def get_invoice_num():
    xero_tenant_id = get_xero_tenant_id()
    accounting_api = AccountingApi(api_client)

    invoice = accounting_api.get_invoices(
        xero_tenant_id, invoice_numbers=["INV-949"]
    )
    code = serialize_model(invoice)
    sub_title = "Invoice found:"

    return render_template(
        "code.html", title="Invoice", code=code, sub_title=sub_title
    )

#create multiple invoices
#requires multiple invoices in array
@app.route("/createInvoices")
@xero_token_required
def create_invoices():
    xero_tenant_id = get_xero_tenant_id()
    accounting_api = AccountingApi(api_client)

    contact = Contact(
        name="John Doe",
        email_address="john.doe@example.com",
    )

    line_item = LineItem(
        description="Consulting services",
        quantity=10,
        unit_amount=100.00,
        account_code="200",
        )

    due_date = date(2026, 11, 12)

    emptyInvoice = Invoice(
        type="ACCREC",  # Accounts receivable (sales invoice)
        contact=contact,
        line_items=[line_item],
        line_amount_types=LineAmountTypes("Exclusive"),  # Prices exclude tax
        invoice_number="INV-949",
        due_date=due_date,
        currency_code=CurrencyCode("AUD"),
        status="DRAFT",  # Draft status for testing
        total=1000.00,
        total_tax=100.00,
        amount_due=1100.00,
        )
    
    invoices = Invoices(invoices=[emptyInvoice])
    try:
        created_invoices = accounting_api.create_invoices(
            xero_tenant_id, invoices=invoices
        )
    except AccountingBadRequestException as exception:
        sub_title = "Error: " + exception.reason
        result_list = None
        code = jsonify(exception.error_data)
    else:
        sub_title = ""
        result_list = []
        for invoice in created_invoices.invoices:
            if invoice.has_errors:
                error = getvalue(invoice.validation_errors, "0.message", "")
                result_list.append("Error: {}".format(error))
            else:
                result_list.append("Invoice {} created.".format(invoice.invoice_number))
        code = serialize_model(created_invoices)

    return render_template(
        "code.html",
        title="Create Multiple Invoices",
        code=code,
        result_list=result_list,
        sub_title=sub_title,
    )

#get invoice
#requires invoice_id
@app.route("/invoice")
@xero_token_required
def get_invoice():
    xero_tenant_id = get_xero_tenant_id()
    accounting_api = AccountingApi(api_client)

    invoice = accounting_api.get_invoice(
        xero_tenant_id, invoice_id="0e64a623-c2a1-446a-93ed-eb897f118cbc"
    )
    code = serialize_model(invoice)
    sub_title = "Invoice found:"

    return render_template(
        "code.html", title="Invoice", code=code, sub_title=sub_title
    )

#create invoice
#requires stuff
@app.route("/createInvoice")
@xero_token_required
def create_invoice():
    xero_tenant_id = get_xero_tenant_id()
    accounting_api = AccountingApi(api_client)

    contact = Contact(
        name="John Doe",
        email_address="john.doe@example.com",
    )

    line_item = LineItem(
        description="Consulting services",
        quantity=10,
        unit_amount=100.00,
        account_code="200",
        )
    
    emptyInvoice = Invoice(
        type="ACCREC",  # Accounts receivable (sales invoice)
        contact=contact,
        line_items=[line_item],
        line_amount_types=LineAmountTypes("Exclusive"),  # Prices exclude tax
        invoice_number="INV-001",
        currency_code=CurrencyCode("AUD"),
        status="DRAFT",  # Draft status for testing
        total=1000.00,
        total_tax=100.00,
        amount_due=1100.00,
        )
    
    invoices = Invoices(invoices=[emptyInvoice])

    try:
        created_invoices = accounting_api.create_invoices(
            xero_tenant_id,
            invoices=invoices
        )
    except AccountingBadRequestException as exception:
        sub_title = "Error: " + exception.reason
        code = jsonify(exception.error_data)
    else:
        sub_title = "Invoice {} created."
        code = serialize_model(created_invoices)

    return render_template(
        "code.html",
        title="Created Invoice",
        code=code,
        sub_title=sub_title
    )

#update invoice
#requires ?
@app.route("/updateInvoice")
@xero_token_required
def update_invoice():
    xero_tenant_id = get_xero_tenant_id()
    accounting_api = AccountingApi(api_client)
    oldInvoice = "INV-001"
    invoice_id=oldInvoice
    contact = Contact(
        name="John Doe",
        email_address="john.doe@example.com",
    )

    line_item = LineItem(
        description="Consulting services",
        quantity=10,
        unit_amount=100.00,
        account_code="200",
        )
    invoice = Invoice(
        type="ACCREC",  # Accounts receivable (sales invoice)
        contact=contact,
        line_items=[line_item],
        line_amount_types=LineAmountTypes("Exclusive"),  # Prices exclude tax
        invoice_number="INV-001",
        currency_code=CurrencyCode("AUD"),
        status="DRAFT",  # Draft status for testing
        total=1000.00,
        total_tax=100.00,
        amount_due=1100.00,
        )

    invoices = Invoices(invoices=[invoice])
    try:
        updated_invoice = accounting_api.update_invoice(
            xero_tenant_id,
            invoice_id=invoice_id,
            invoices=invoices
            )
    except AccountingBadRequestException as exception:
        sub_title = "Error: " + exception.reason
        code = jsonify(exception.error_data)
    else:
        sub_title = "Invoice updated."
        code = serialize_model(updated_invoice)

    return render_template(
        "code.html",
        title="Updated Invoice",
        code=code,
        sub_title=sub_title
    )

#check existing invoice
#invoice_id/invoice_number
@app.route("/checkInvoice")
def check_invoice():
    xero_tenant_id = get_xero_tenant_id()
    accounting_api = AccountingApi(api_client)
    invoice_number="0e64a623-c2a1-446a-93ed-eb897f118cbc"


    invoice_id=invoice_number
    try:
        invoice = accounting_api.get_invoice(
            xero_tenant_id,
            invoice_id=invoice_id
        )
    except:
        sub_title = "No invoice with invoice id " + invoice_id + " exists."
        code =jsonify("")
    else:
        sub_title = "A invoice with invoice id " + invoice_id + " exists."
        code = serialize_model(invoice)

    return render_template(
        "code.html",
        title="Invoice check",
        code=code,
        sub_title=sub_title,
    )
    
def check_invoice_bool(invoice_id):
    xero_tenant_id = get_xero_tenant_id()
    accounting_api = AccountingApi(api_client)
    #invoice_number="0e64a623-c2a1-446a-93ed-eb897f118cbc"

    #invoice_id=invoice_number
    try:
        invoice = accounting_api.get_invoice(
            xero_tenant_id,
            invoice_id=invoice_id
        )
    except:
        return False
    else:
        return True


def check_invoices_bool(invoice_ids):
    xero_tenant_id = get_xero_tenant_id()
    accounting_api = AccountingApi(api_client)
    #invoice_number="0e64a623-c2a1-446a-93ed-eb897f118cbc"

    #invoice_id=invoice_number
    exists_array = []
    for invoice_id in invoice_ids:
        try:
            invoice = accounting_api.get_invoice(
                xero_tenant_id,
                invoice_id=invoice_id
                )
        except:
            pass
        else:
            exists_array.append(invoice_id)
    return exists_array

#check existing invoices
#invoice_id/invoice_number
@app.route("/checkInvoices")
def check_invoices():
    xero_tenant_id = get_xero_tenant_id()
    accounting_api = AccountingApi(api_client)
    invoice_number="0e64a623-c2a1-446a-93ed-eb897f118cbc"
    invoice_number2="7e024960-d582-452c-8b62-85308d99595b"

    invoice_ids=check_invoices_bool([invoice_number, invoice_number2])
    code=""
    sub_title=""
    for invoice_id in invoice_ids:
        invoice = accounting_api.get_invoice(
            xero_tenant_id,
            invoice_id=invoice_id
        )
        sub_title = sub_title + " " +invoice_id
        code = code + serialize_model(invoice)
    
    return render_template(
        "code.html",
        title="Invoice checks",
        code=code,
        sub_title="the invoice/s " + sub_title +" exsist"
    )

@app.route("/login")
def login():
    redirect_url = url_for("oauth_callback", _external=True)
    response = xero.authorize(callback_uri=redirect_url)
    return response


@app.route("/callback")
def oauth_callback():
    try:
        response = xero.authorized_response()
    except Exception as e:
        print(e)
        raise
    # todo validate state value
    if response is None or response.get("access_token") is None:
        return "Access denied: response=%s" % response
    store_xero_oauth2_token(response)
    return redirect(url_for("index", _external=True))


@app.route("/logout")
def logout():
    store_xero_oauth2_token(None)
    return redirect(url_for("index", _external=True))


@app.route("/export-token")
@xero_token_required
def export_token():
    token = obtain_xero_oauth2_token()
    buffer = BytesIO("token={!r}".format(token).encode("utf-8"))
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="x.python",
        as_attachment=True,
        attachment_filename="oauth2_token.py",
    )


@app.route("/refresh-token")
@xero_token_required
def refresh_token():
    xero_token = obtain_xero_oauth2_token()
    new_token = api_client.refresh_oauth2_token()
    return render_template(
        "code.html",
        title="Xero OAuth2 token",
        code=jsonify({"Old Token": xero_token, "New token": new_token}),
        sub_title="token refreshed",
    )


def get_xero_tenant_id():
    token = obtain_xero_oauth2_token()
    if not token:
        return None

    identity_api = IdentityApi(api_client)
    for connection in identity_api.get_connections():
        if connection.tenant_type == "ORGANISATION":
            return connection.tenant_id


if __name__ == '__main__':
    app.run(host='localhost', port=5000)
