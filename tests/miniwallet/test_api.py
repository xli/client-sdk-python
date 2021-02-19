# Copyright (c) The Diem Core Contributors
# SPDX-License-Identifier: Apache-2.0

from diem.miniwallet import Account, Transaction, PaymentURI
from diem import identifier
import pytest, requests, copy


def test_account_resource(clients):
    accounts = clients.app.get_all(Account)
    for data in [clients.app.new_kyc_data(), clients.app.new_kyc_data()]:
        account = clients.app.create_account(kyc_data=data)
        assert account.id
        assert account.kyc_data == data
        assert clients.app.get(Account, id=account.id) == account.data

    accounts_after = clients.app.get_all(Account)
    assert len(accounts) + 2 == len(accounts_after)


def test_new_account_has_no_transaction(clients):
    account = clients.app.create_account()
    assert account.get_all(Transaction) == []


def test_deposit_transaction(clients, currency):
    amount = 1000
    account = clients.app.create_account()
    txn = account.deposit(currency, amount)
    assert account.balance(currency) == amount

    txns = account.get_all(Transaction)
    assert len(txns) == 1
    assert txns == [txn]

    assert txn.account_id == account.id
    assert txn.currency == currency
    assert txn.amount == amount

    assert txn.subaddress_hex is None
    assert txn.payee is None
    assert txn.signed_transaction is None
    assert txn.diem_transaction_version is None


def test_payment_uri_resource(clients, hrp):
    account = clients.stub.create_account()
    ret = account.create_payment_uri()
    assert ret.account_id == account.id
    assert ret.subaddress_hex
    assert ret.account_identifier
    address, subaddress = identifier.decode_account(ret.account_identifier, hrp)
    assert address
    assert subaddress
    assert ret.subaddress_hex == subaddress.hex()

    assert account.get_all(PaymentURI) == [ret]


def test_send_payment_transaction(clients, hrp, currency):
    foo = clients.stub.create_account()
    payment = foo.create_payment_uri()

    amount = 1234
    sender = clients.app.create_account(currency=currency, amount=amount)
    txn = sender.send_payment(currency, amount, payment.account_identifier)
    assert txn.account_id == sender.id
    assert txn.currency == currency
    assert txn.amount == amount
    assert txn.payee == payment.account_identifier
    assert txn.status == "pending"

    clients.wait_for(lambda: clients.app.reload(txn).subaddress_hex)
    clients.wait_for(lambda: clients.app.reload(txn).signed_transaction)
    clients.wait_for(lambda: clients.app.reload(txn).diem_transaction_version)
    clients.wait_for(lambda: clients.app.reload(txn).status == "completed")
    assert sender.balance(currency) == 0


def test_payment_uri_transaction(clients, hrp, currency):
    receiver = clients.app.create_account()
    payment_uri = receiver.create_payment_uri()

    amount = 1234
    stub_sender = clients.stub.create_account(currency=currency, amount=amount)
    send_txn = stub_sender.send_payment(currency, amount, payment_uri.account_identifier)
    clients.wait_for(lambda: clients.stub.reload(send_txn).status == "completed")

    txn = clients.wait_for(lambda: receiver.get(Transaction, subaddress_hex=payment_uri.subaddress_hex))
    assert txn.account_id == receiver.id
    assert txn.currency == currency
    assert txn.amount == amount
    assert txn.payee is None
    assert txn.status == "completed"
    assert txn.diem_transaction_version == send_txn.diem_transaction_version
    assert receiver.balance(currency) == amount


def test_receive_multiple_payments(clients, hrp, currency):
    receiver = clients.app.create_account()
    payment_uri = receiver.create_payment_uri()

    amount = 1234
    stub_sender1 = clients.stub.create_account(currency=currency, amount=amount)
    send_txn1 = stub_sender1.send_payment(currency, amount, payment_uri.account_identifier)
    clients.wait_for(lambda: clients.stub.reload(send_txn1).status == "completed")

    stub_sender2 = clients.stub.create_account(currency=currency, amount=amount)
    send_txn2 = stub_sender2.send_payment(currency, amount, payment_uri.account_identifier)
    clients.wait_for(lambda: clients.stub.reload(send_txn2).status == "completed")

    clients.wait_for(lambda: len(receiver.get_all(Transaction, subaddress_hex=payment_uri.subaddress_hex)) == 2)
    txns = receiver.get_all(Transaction, subaddress_hex=payment_uri.subaddress_hex)
    for index, txn in enumerate(txns):
        assert txn.account_id == receiver.id
        assert txn.currency == currency
        assert txn.amount == amount
        assert txn.payee is None
        assert txn.status == "completed"
        send_txn = send_txn1 if index == 0 else send_txn2
        assert txn.diem_transaction_version == send_txn.diem_transaction_version

    assert receiver.balance(currency) == amount * 2


@pytest.mark.parametrize(
    "data",
    [
        (
            Account,
            {
                "kyc_data": lambda _a, client, _c: client.new_kyc_data(),
            },
            [],
        ),
        (
            Transaction,
            {
                "account_id": lambda account, _c, _: account.id,
                "currency": lambda _a, _c, currency: currency,
                "amount": 123,
                "payee": "tdm1p7ujcndcl7nudzwt8fglhx6wxn08kgs5tm6mz4ustv0tyx",
            },
            ["account_id", "payee"],
        ),
        (
            PaymentURI,
            {
                "account_id": lambda account, _c, _: account.id,
            },
            [],
        ),
    ],
)
def test_return_400_error_when_create_resource_with_invali_data(clients, data, currency):
    typ, attrs, optional = data
    account = clients.app.create_account(currency=currency, amount=1_000_000)
    for k in attrs:
        if callable(attrs[k]):
            attrs[k] = attrs[k](account, clients.app, currency)

    for tc in create_invalid_test_data(attrs, optional=optional):
        invalid_attrs, error_msg = tc
        assert_client_error(clients.app, typ, error_msg, **invalid_attrs)


@pytest.mark.parametrize(
    "data",
    [
        "invalid json",
        "{}",
    ],
)
def test_validate_kyc_data(clients, currency, data):
    assert_client_error(
        clients.app,
        Account,
        "'kyc_data' must be JSON-encoded KycDataObject",
        kyc_data=data,
    )


@pytest.mark.parametrize(
    "invalid_payee",
    [
        "invalid id",
        "dm1p7ujcndcl7nudzwt8fglhx6wxnvqqqqqqqqqqqqqd8p9cq",
        "tdm1p7ujcndcl7nudzwt8fglhx6wxnvqqqqqqqqqqqqqv88j4x",
    ],
)
def test_send_payment_payee_is_invalid(clients, currency, invalid_payee):
    sender = clients.app.create_account(currency, 100)
    assert_client_error(
        clients.app,
        Transaction,
        "'payee' is invalid account identifier",
        account_id=sender.id,
        currency=currency,
        amount=1,
        payee=invalid_payee,
    )


def test_return_client_error_if_send_payment_more_than_account_balance(clients, currency):
    receiver = clients.stub.create_account()
    payment = receiver.create_payment_uri()
    sender = clients.app.create_account()
    assert_client_error(
        clients.app,
        Transaction,
        "account balance not enough",
        account_id=sender.id,
        currency=currency,
        amount=1,
        payee=payment.account_identifier,
    )


def test_account_balance_should_include_pending_transactions(clients, currency):
    receiver = clients.stub.create_account()
    payment = receiver.create_payment_uri()
    amount = 1_000_000_000
    sender = clients.app.create_account(currency, amount)
    txn = sender.send_payment(currency=currency, amount=amount, payee=payment.account_identifier)
    assert_client_error(
        clients.app,
        Transaction,
        "account balance not enough",
        account_id=sender.id,
        currency=currency,
        amount=1,
        payee=payment.account_identifier,
    )
    clients.wait_for(lambda: clients.app.reload(txn).status == "completed")
    print(txn)


def test_account_balance_validation_should_exclude_canceled_transactions(clients, currency, travel_rule_threshold):
    amount = travel_rule_threshold
    receiver = clients.stub.create_account()
    payment = receiver.create_payment_uri()
    sender = clients.app.create_account(currency, amount, kyc_data=clients.stub.new_reject_kyc_data())
    txn = sender.send_payment(currency=currency, amount=amount, payee=payment.account_identifier)
    clients.wait_for(lambda: sender.client.reload(txn).status == "canceled")

    txn = sender.send_payment(currency, travel_rule_threshold - 1, payment.account_identifier)
    clients.wait_for(lambda: sender.client.reload(txn).status == "completed")


@pytest.mark.parametrize(
    "amount",
    [
        0,
        1,
        1_000_000_000,
        1_000_000_000_000,
    ],
)
def test_internal_transfer(clients, currency, amount):
    receiver = clients.app.create_account()
    payment = receiver.create_payment_uri()
    sender = clients.app.create_account(currency, amount)
    txn = sender.send_payment(currency, amount, payee=payment.account_identifier)
    assert txn.amount == amount
    assert txn.payee == payment.account_identifier
    clients.wait_for(lambda: clients.app.reload(txn).status == "completed")
    assert txn.diem_transaction_version is None
    assert txn.signed_transaction is None
    assert txn.subaddress_hex is None
    assert sender.balance(currency) == 0
    assert receiver.balance(currency) == amount


def assert_client_error(client, typ, error_msg, **data):
    before_size = len(client.get_all(typ))
    with pytest.raises(requests.exceptions.HTTPError, match="400 Client Error") as einfo:
        client.create(typ, **data)
    assert error_msg in einfo.value.response.text
    assert len(client.get_all(typ)) == before_size


def create_invalid_test_data(valid, optional=[]):
    ret = []
    for field in valid:
        if field not in optional:
            data = copy.copy(valid)
            del data[field]
            ret.append((data, "%r is required" % field))

        data = copy.copy(valid)
        if isinstance(valid[field], str):
            data[field] = 1234567
        else:
            data[field] = "invalid"
        ret.append((data, "%r type must be %r" % (field, type(valid[field]).__name__)))

        if field in ["currency"]:
            data = copy.copy(valid)
            data[field] = "ABC"
            ret.append((data, "currency code is invalid"))
        if "amount" in field:
            data = copy.copy(valid)
            data[field] = -valid[field]
            ret.append((data, "%r value must be greater than or equal to zero" % field))

    return ret
